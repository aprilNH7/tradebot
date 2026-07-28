"""Diagnose *why* the system loses money instead of guessing at parameters.

Three questions, in order:

1. What does the adverse-excursion distribution actually look like? A stop is a
   quantile of that distribution. Setting it from intuition is how you end up
   paying 2% to protect a 0.5% target.
2. Are stop-outs bad *entries* or bad *stops*? Counterfactual: rerun with stops
   disabled and compare the same entries.
3. Does honoring the strategy's own stop/target — which the live engine has
   never read — change the answer?

Run:  python3 -m backtest.diagnose
"""
from __future__ import annotations

import argparse
import statistics
from collections import defaultdict

from backtest.data import load_universe
from backtest.harness import Backtester
from config.settings import settings
from strategies.grid_strategy import GridStrategy
from strategies.rsi_strategy import RSIStrategy
from strategies.sma_crossover import SMACrossoverStrategy


def quantile(xs: list[float], q: float) -> float:
    if not xs:
        return float("nan")
    s = sorted(xs)
    idx = q * (len(s) - 1)
    lo, hi = int(idx), min(int(idx) + 1, len(s) - 1)
    frac = idx - lo
    return s[lo] * (1 - frac) + s[hi] * frac


def build_strategies():
    return [
        SMACrossoverStrategy(),
        RSIStrategy(),
        GridStrategy(),
    ]


def excursion_report(result) -> None:
    print("\n=== 1. Adverse excursion distribution (MAE, % against entry) ===")
    print("    A stop at X% fires on every trip whose MAE is worse than -X%.")

    by_reason: dict[str, list] = defaultdict(list)
    for t in result.trips:
        by_reason[t.exit_reason].append(t)

    all_mae = [t.mae_pct for t in result.trips]
    print(f"\n  All {len(result.trips)} trips:")
    for q in (0.50, 0.70, 0.80, 0.90, 0.95, 0.99):
        print(f"    p{int(q*100):<2} MAE  {quantile(all_mae, 1 - q) * 100:7.3f}%")

    print("\n  Winners vs losers — the number that matters:")
    win_mae = [t.mae_pct for t in result.trips if t.pnl > 0]
    los_mae = [t.mae_pct for t in result.trips if t.pnl <= 0]
    for label, xs in (("winners", win_mae), ("losers", los_mae)):
        if not xs:
            continue
        print(f"    {label:<8} n={len(xs):<5} "
              f"median {statistics.median(xs)*100:7.3f}%  "
              f"p90 {quantile(xs, 0.10)*100:7.3f}%  "
              f"worst {min(xs)*100:7.3f}%")

    # The decisive statistic: how many *winners* would a given stop have killed?
    print("\n  Winner survival by stop width (what a tighter stop costs you):")
    print("    stop     winners killed    their PnL")
    winners = [t for t in result.trips if t.pnl > 0]
    for stop in (0.0025, 0.005, 0.0075, 0.01, 0.015, 0.02, 0.03):
        killed = [t for t in winners if t.mae_pct <= -stop]
        lost = sum(t.pnl for t in killed)
        pct = 100 * len(killed) / len(winners) if winners else 0
        print(f"    {stop*100:4.2f}%   {len(killed):5d} ({pct:5.1f}%)   ${lost:12,.2f}")

    print("\n  Exit reason breakdown:")
    for reason, trips in sorted(by_reason.items(),
                                key=lambda kv: sum(t.pnl for t in kv[1])):
        pnl = sum(t.pnl for t in trips)
        mfe = [t.mfe_pct for t in trips]
        bars = [t.bars_held for t in trips]
        print(f"    {reason:<16} n={len(trips):<5} ${pnl:12,.2f}  "
              f"avg ${pnl/len(trips):8,.2f}  "
              f"median MFE {statistics.median(mfe)*100:6.3f}%  "
              f"median bars {statistics.median(bars):5.1f}")

    # Did stopped-out trips ever show a profit before dying?
    stops = by_reason.get("stop_loss", [])
    if stops:
        never_green = [t for t in stops if t.mfe_pct <= 0]
        print(f"\n  Of {len(stops)} stop-outs, {len(never_green)} "
              f"({100*len(never_green)/len(stops):.1f}%) never traded above entry.")
        print("    If most were never green, the entry is wrong, not the stop.")
        got_green = [t for t in stops if t.mfe_pct > 0]
        if got_green:
            print(f"    The other {len(got_green)} reached a median "
                  f"+{statistics.median([t.mfe_pct for t in got_green])*100:.3f}% "
                  f"before reversing into the stop.")


def cost_decomposition(strategies_fn, bars, capital) -> None:
    """Split the result into gross edge and transaction cost.

    This is the question that decides whether any tuning is worth doing. With
    ~4,900 round trips a 2bps spread is a four-figure tax; if the system is only
    losing about that much, then there is no exit bug to find — the entries are
    a coin flip and the costs are the entire deficit.
    """
    print("\n=== 4. Gross edge vs transaction cost ===")
    print(f"  {'spread':<12} {'PnL':>12} {'trips':>7} {'exp/trip':>9} {'t':>7}")

    frictionless = None
    for bps in (0.0, 1.0, 2.0, 5.0, 10.0):
        r = Backtester(strategies_fn(), bars,
                       initial_capital=capital, spread_bps=bps).run()
        if bps == 0.0:
            frictionless = r
        print(f"  {bps:5.1f} bps    ${r.total_pnl:11,.2f} {len(r.trips):7d} "
              f"${r.expectancy:8,.2f} {r.t_stat:7.2f}")

    if frictionless is None:
        return
    print(f"\n  Gross (zero-cost) edge: ${frictionless.total_pnl:,.2f} over "
          f"{len(frictionless.trips)} trips "
          f"(${frictionless.expectancy:.3f}/trip, t={frictionless.t_stat:.2f})")
    print("  A system whose gross edge is not comfortably positive and")
    print("  significant cannot be rescued by exit tuning or by sizing. The")
    print("  only fixes are: better entries, or far fewer of them.")


def counterfactual(strategies_fn, bars, capital) -> None:
    print("\n=== 2. Counterfactual: same entries, different exit policy ===")
    print("    Isolates 'the stop is too tight' from 'the entry has no edge'.")

    scenarios = [
        ("baseline (2% stop / 4% target)", {}, False),
        ("stop effectively off (99%)", {"stop_loss_pct": 0.99}, False),
        ("tight 0.5% stop", {"stop_loss_pct": 0.005}, False),
        ("target matched to grid (0.5%)", {"take_profit_pct": 0.005}, False),
        ("0.5% stop + 0.5% target", {"stop_loss_pct": 0.005,
                                     "take_profit_pct": 0.005}, False),
        ("honor strategy's own levels", {}, True),
    ]

    print(f"\n  {'scenario':<34} {'PnL':>12} {'trips':>7} {'win%':>6} "
          f"{'exp/trip':>9} {'PF':>6}")
    for label, overrides, honor in scenarios:
        r = Backtester(
            strategies_fn(), bars,
            initial_capital=capital,
            risk_overrides=overrides,
            honor_signal_levels=honor,
        ).run()
        n = len(r.trips)
        pf = r.profit_factor
        pf_s = f"{pf:6.2f}" if pf is not None else "   inf"
        print(f"  {label:<34} ${r.total_pnl:11,.2f} {n:7d} "
              f"{r.win_rate*100:5.1f}% ${r.expectancy:8,.2f} {pf_s}")


def per_strategy(bars, capital) -> None:
    """Attribute gross edge to each strategy in isolation.

    Exit-reason totals are not attribution: a grid entry that dies on the global
    stop books its loss under `stop_loss`, not under `grid`. Running each
    strategy alone at zero cost is the only clean read on which entries, if any,
    carry an edge.
    """
    print("\n=== 5. Per-strategy gross edge, zero cost, run in isolation ===")
    print(f"  {'strategy':<16} {'gross PnL':>12} {'trips':>7} {'win%':>6} "
          f"{'exp/trip':>9} {'t':>7}")

    candidates = [
        ("sma_crossover", SMACrossoverStrategy),
        ("rsi", RSIStrategy),
        ("grid", GridStrategy),
    ]
    for name, cls in candidates:
        r = Backtester([cls()], bars, initial_capital=capital,
                       spread_bps=0.0).run()
        n = len(r.trips)
        if n == 0:
            print(f"  {name:<16} {'no trips':>12}")
            continue
        print(f"  {name:<16} ${r.total_pnl:11,.2f} {n:7d} "
              f"{r.win_rate*100:5.1f}% ${r.expectancy:8,.3f} {r.t_stat:7.2f}")

    print("\n  |t| > 1.98 is the bar for 'not noise'. Anything below that is a")
    print("  coin flip you are paying spread to play.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default=",".join(settings.STOCK_SYMBOLS))
    ap.add_argument("--days", type=int, default=180)
    ap.add_argument("--timeframe", default="1h")
    ap.add_argument("--capital", type=float, default=100_000.0)
    args = ap.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    bars = load_universe(symbols, timeframe=args.timeframe, days=args.days)
    total = sum(len(v) for v in bars.values())
    # A run with no data produces a table of clean $0.00 rows that looks like a
    # result. Refuse rather than print something that can be misread as one.
    if total == 0:
        raise SystemExit(
            f"No bars loaded for {args.timeframe}/{args.days}d. Check the cache "
            f"in data/bars or run backtest.run first to populate it."
        )
    print(f"Loaded {total:,} bars across {len(bars)} symbols "
          f"({args.timeframe}, {args.days}d)")

    base = Backtester(build_strategies(), bars,
                      initial_capital=args.capital).run()
    print(f"\nBaseline: ${base.total_pnl:,.2f} over {len(base.trips)} trips "
          f"(expectancy ${base.expectancy:.2f}, t={base.t_stat:.2f})")

    excursion_report(base)
    counterfactual(build_strategies, bars, args.capital)
    cost_decomposition(build_strategies, bars, args.capital)
    per_strategy(bars, args.capital)

    print("\n=== 3. Read the table above, not your priors ===")
    print("  If every scenario is negative the exit policy is not the problem;")
    print("  the entries have no edge and no stop placement will rescue them.")


if __name__ == "__main__":
    main()
