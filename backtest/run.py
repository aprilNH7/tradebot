"""Backtest CLI.

    python -m backtest.run --days 180 --target 400
    python -m backtest.run --sweep --target 400
    python -m backtest.run --refresh          # refetch bars
"""

import argparse
import itertools

from backtest.data import load_universe
from backtest.harness import Backtester
from config.settings import settings
from strategies.grid_strategy import GridStrategy
from strategies.rsi_strategy import RSIStrategy
from strategies.sma_crossover import SMACrossoverStrategy
from utils.logger import setup_logger

log = setup_logger("backtest.run")


def build_strategies(names):
    """Mirror main.build_engine's selection so the backtest measures the bot
    that actually runs. "all" excludes grid in both places; naming it directly
    still loads it so its numbers stay reproducible.
    """
    out = []
    if "sma" in names or "all" in names:
        out.append(SMACrossoverStrategy())
    if "rsi" in names or "all" in names:
        out.append(RSIStrategy())
    if "grid" in names:
        out.append(GridStrategy())
    return out


def split_bars(bars: dict, train_frac: float = 0.7):
    """Split every symbol's series at the same wall-clock date.

    Splitting per-symbol by index would leak: symbols with different bar counts
    would straddle different dates. Picking one cutoff date keeps train and test
    genuinely disjoint in time.
    """
    all_ts = sorted(b.timestamp for series in bars.values() for b in series)
    if not all_ts:
        return {}, {}
    cutoff = all_ts[int(len(all_ts) * train_frac)]
    train, test = {}, {}
    for sym, series in bars.items():
        tr = [b for b in series if b.timestamp < cutoff]
        te = [b for b in series if b.timestamp >= cutoff]
        if tr:
            train[sym] = tr
        if te:
            test[sym] = te
    return train, test, cutoff


def report(r, target: float, label: str = "BASELINE"):
    print(f"\n{'=' * 66}")
    print(f"  {label}")
    print(f"{'=' * 66}")
    if not r.trips:
        print("  no completed round trips")
        if r.blocked:
            print("  blocked:", dict(sorted(r.blocked.items(), key=lambda x: -x[1])[:5]))
        return

    print(f"  round trips        : {len(r.trips)}")
    print(f"  trading days       : {r.trading_days}")
    print(f"  total PnL          : ${r.total_pnl:,.2f}")
    print(f"  return             : {100 * r.total_pnl / r.initial_capital:.2f}%")
    print(f"  expectancy / trip  : ${r.expectancy:.3f}")
    print(f"  win rate           : {100 * r.win_rate:.1f}%")
    print(f"  avg win / avg loss : ${r.avg_win:.2f} / ${r.avg_loss:.2f}")
    pf = r.profit_factor
    print(f"  profit factor      : {pf:.2f}" if pf else "  profit factor      : n/a")
    print(f"  max drawdown       : {100 * r.max_drawdown:.2f}%")
    print(f"  daily Sharpe (ann) : {r.daily_sharpe:.2f}")
    print(f"  t-stat             : {r.t_stat:.2f}"
          f"   {'SIGNIFICANT' if abs(r.t_stat) > 1.98 else 'not significant'}")

    print(f"\n  --- ${target:,.0f}/DAY TARGET ---")
    print(f"  avg daily PnL      : ${r.avg_daily_pnl:,.2f}")
    print(f"  days hitting target: {r.days_hitting(target)}/{r.trading_days}"
          f"  ({100 * r.pct_days_hitting(target):.1f}%)")
    if r.avg_daily_pnl > 0:
        print(f"  scale needed       : {target / r.avg_daily_pnl:.1f}x")
    else:
        print("  scale needed       : n/a (strategy is not profitable)")

    daily = sorted(r.daily_pnl.values())
    if daily:
        print(f"  best / worst day   : ${daily[-1]:,.2f} / ${daily[0]:,.2f}")

    print("\n  by strategy:")
    for name, a in sorted(r.by_strategy().items(), key=lambda x: -x[1]["pnl"]):
        wr = 100 * a["wins"] / a["trips"]
        print(f"    {name:<16} {a['trips']:>5} trips  ${a['pnl']:>10,.2f}  {wr:>5.1f}% win")

    print("  by exit reason:")
    for name, a in sorted(r.by_exit_reason().items(), key=lambda x: -x[1]["pnl"]):
        print(f"    {name:<16} {a['trips']:>5} trips  ${a['pnl']:>10,.2f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=180)
    ap.add_argument("--timeframe", default=settings.CANDLE_TIMEFRAME)
    ap.add_argument("--symbols", nargs="*", default=None)
    ap.add_argument("--strategies", nargs="*", default=["all"])
    ap.add_argument("--capital", type=float, default=100_000.0)
    ap.add_argument("--spread-bps", type=float, default=2.0)
    ap.add_argument(
        "--passive-entries", action="store_true",
        help="Model resting limit entries that pay no spread. UPPER BOUND only: "
             "assumes every passive order fills, which live they will not.",
    )
    ap.add_argument("--target", type=float, default=400.0)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--sweep", action="store_true")
    args = ap.parse_args()

    symbols = args.symbols or settings.STOCK_SYMBOLS
    exchange = None
    try:
        from exchanges.alpaca_exchange import AlpacaExchange
        exchange = AlpacaExchange()
        exchange.connect()
    except Exception as e:
        log.warning(f"No exchange ({e}) — cache only")

    bars = load_universe(symbols, args.timeframe, args.days, exchange, args.refresh)
    if not bars:
        print("No bars available.")
        return
    total = sum(len(v) for v in bars.values())
    print(f"\nLoaded {total:,} bars across {len(bars)} symbols "
          f"({args.timeframe}, {args.days}d), spread {args.spread_bps}bps")

    strategies = build_strategies(args.strategies)

    base = Backtester(strategies, bars, initial_capital=args.capital,
                      spread_bps=args.spread_bps,
                      passive_entries=args.passive_entries).run()
    label = ("BASELINE (passive entries — UPPER BOUND, assumes every rest fills)"
             if args.passive_entries else "BASELINE (current settings)")
    report(base, args.target, label)

    if not args.sweep:
        return

    print(f"\n{'=' * 66}\n  PARAMETER SWEEP\n{'=' * 66}")
    # Stops are where the baseline bleeds, so the stop axis is the widest. A
    # mean-reversion book generally wants a wide stop and a near target, which
    # is the opposite of the shipped defaults — the sweep is here to settle that
    # with numbers rather than intuition.
    grid = {
        "stop_loss_pct": [0.01, 0.02, 0.04, 0.08],
        "take_profit_pct": [0.005, 0.01, 0.02, 0.05],
        "min_confidence": [0.30, 0.60],
    }
    keys = list(grid)
    train, test, cutoff = split_bars(bars, 0.7)
    print(f"  train: bars before {cutoff.date()}   test: bars from {cutoff.date()}")
    print("  Selection happens on train only. The test column is the number that\n"
          "  matters — an in-sample winner proves nothing.\n")

    rows = []
    for combo in itertools.product(*(grid[k] for k in keys)):
        ov = dict(zip(keys, combo))
        tr = Backtester(strategies, train, initial_capital=args.capital,
                        spread_bps=args.spread_bps, risk_overrides=ov).run()
        te = Backtester(strategies, test, initial_capital=args.capital,
                        spread_bps=args.spread_bps, risk_overrides=ov).run()
        rows.append((ov, tr, te))

    rows.sort(key=lambda x: -x[1].total_pnl)
    print(f"  {'stop':>5} {'tp':>6} {'conf':>5} | {'tr trips':>8} {'tr PnL':>10} "
          f"{'tr $/day':>9} | {'te trips':>8} {'te PnL':>10} {'te $/day':>9} "
          f"{'te t':>6}")
    for ov, tr, te in rows:
        print(f"  {ov['stop_loss_pct']:>5.2f} {ov['take_profit_pct']:>6.3f} "
              f"{ov['min_confidence']:>5.2f} | {len(tr.trips):>8} "
              f"${tr.total_pnl:>9,.0f} ${tr.avg_daily_pnl:>8,.0f} | "
              f"{len(te.trips):>8} ${te.total_pnl:>9,.0f} "
              f"${te.avg_daily_pnl:>8,.0f} {te.t_stat:>6.2f}")

    best_ov, best_tr, best_te = rows[0]
    report(best_tr, args.target, f"BEST ON TRAIN (in-sample) — {best_ov}")
    report(best_te, args.target, f"SAME PARAMS, OUT-OF-SAMPLE — {best_ov}")

    profitable = [r for r in rows if r[2].total_pnl > 0]
    print(f"\n  {len(profitable)}/{len(rows)} parameter sets profitable "
          f"out-of-sample.")
    signif = [r for r in profitable if r[2].t_stat > 1.98]
    print(f"  {len(signif)}/{len(rows)} are also statistically significant "
          f"out-of-sample (t > 1.98).")
    if not signif:
        print("  => No configuration shows a real edge. Raising size would only\n"
              "     scale the losses.")


if __name__ == "__main__":
    main()
