"""Live monitoring dashboard."""

from datetime import datetime

from flask import Flask, render_template, jsonify


def create_app(engine=None):
    app = Flask(__name__)
    app.engine = engine

    @app.route("/")
    def index():
        return render_template("dashboard.html")

    @app.route("/api/status")
    def status():
        if not app.engine:
            return jsonify({"error": "Engine not initialized"})
        return jsonify(app.engine.get_status())

    @app.route("/api/performance")
    def performance():
        if not app.engine:
            return jsonify({"error": "Engine not initialized"})
        return jsonify(app.engine.portfolio.get_performance())

    @app.route("/api/trades")
    def trades():
        if not app.engine:
            return jsonify({"error": "Engine not initialized"})
        closed = app.engine.portfolio.get_closed_trades()
        return jsonify([
            {
                "symbol": t.symbol,
                "side": t.side,
                "quantity": t.quantity,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "pnl": t.pnl,
                "strategy": t.strategy,
                "market": t.market,
                "entry_time": t.entry_time.isoformat(),
                "exit_time": t.exit_time.isoformat() if t.exit_time else None,
            }
            for t in closed[-50:]  # last 50 trades
        ])

    @app.route("/api/risk")
    def risk():
        if not app.engine:
            return jsonify({"error": "Engine not initialized"})
        return jsonify(app.engine.risk_manager.get_status())

    @app.route("/api/strategies")
    def strategies_breakdown():
        if not app.engine:
            return jsonify({"error": "Engine not initialized"})
        return jsonify(app.engine.portfolio.get_strategy_breakdown())

    return app
