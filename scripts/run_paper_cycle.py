#!/usr/bin/env python3
"""Run two deterministic, spot-only paper portfolios from the same closed Kraken candles."""
import json
import copy
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
KRAKEN = "https://api.kraken.com/0/public/"
KRAKEN_EUR_PAIRS = {
    "BTC": "XBTEUR", "ETH": "ETHEUR", "SOL": "SOLEUR", "XRP": "XRPEUR",
    "ADA": "ADAEUR", "DOGE": "XDGEUR", "DOT": "DOTEUR", "AVAX": "AVAXEUR",
    "LINK": "LINKEUR", "LTC": "LTCEUR", "XLM": "XLMEUR", "ATOM": "ATOMEUR",
    "ALGO": "ALGOEUR", "UNI": "UNIEUR", "AAVE": "AAVEEUR", "FIL": "FILEUR",
    "NEAR": "NEAREUR", "ICP": "ICPEUR", "ETC": "ETCEUR", "BCH": "BCHEUR",
    "MKR": "MKREUR", "SAND": "SANDEUR", "MANA": "MANAEUR", "EOS": "EOSEUR",
    "XTZ": "XTZEUR", "FLOW": "FLOWEUR", "KSM": "KSMEUR", "KAVA": "KAVAEUR",
    "SNX": "SNXEUR", "COMP": "COMPEUR",
}
PORTFOLIOS = (
    ("conservative", ROOT / "config.json", ROOT / "data" / "state.json", ROOT / "data" / "latest_report.json"),
    ("aggressive", ROOT / "config-aggressive.json", ROOT / "data" / "aggressive_state.json", ROOT / "data" / "latest_aggressive_report.json"),
    ("conservative_v2", ROOT / "config-conservative-v2.json", ROOT / "data" / "conservative_v2_state.json", ROOT / "data" / "latest_conservative_v2_report.json"),
    ("aggressive_v2", ROOT / "config-aggressive-v2.json", ROOT / "data" / "aggressive_v2_state.json", ROOT / "data" / "latest_aggressive_v2_report.json"),
)

def fork_state(parent, name, config, now):
    """Copy accounting exactly; comparison begins here, never at a new EUR500."""
    state = copy.deepcopy(parent)
    state["fork"] = {"parent": config["parentPortfolio"], "at": now,
                     "sourceLastRun": parent["lastRun"],
                     "equity": parent["lastReport"]["equity"],
                     "cash": parent["cash"], "positions": copy.deepcopy(parent["positions"]),
                     "tradeCount": len(parent["trades"]),
                     "fees": sum(t["fee"] for t in parent["trades"])}
    state["lastReport"].update(portfolio=name, experiment=config["experiment"], version=config["version"])
    return state

def entry_block(config, state, market, actions, now):
    if not config.get("riskControls"):
        return None
    if any(t["side"] == "SELL" for t in actions):
        return "cooldown: no entries in an exit cycle"
    if state.get("entryBlockedUntil") and now < state["entryBlockedUntil"]:
        return "cooldown after exit until " + state["entryBlockedUntil"]
    if any(s not in market for s in state["positions"]):
        return "risk check unavailable: missing held-asset price"
    if config.get("requireBtcTrend") and not market.get("BTC", {}).get("bullish", False):
        return "market filter: BTC trend not positive or unavailable"
    return None

def api(method, **params):
    with urlopen(KRAKEN + method + "?" + urlencode(params), timeout=8) as response:
        payload = json.load(response)
    if payload.get("error"):
        raise RuntimeError("; ".join(payload["error"]))
    return payload["result"]

def rsi(values, period=14):
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [max(0, change) for change in changes[-period:]]
    losses = [max(0, -change) for change in changes[-period:]]
    average_gain, average_loss = sum(gains) / period, sum(losses) / period
    if average_loss == 0:
        return 100.0
    return 100 - (100 / (1 + average_gain / average_loss))

def closed_candles(pair_id):
    raw = api("OHLC", pair=pair_id, interval=240)
    rows = next(value for key, value in raw.items() if key != "last")
    closes = [float(row[4]) for row in rows[:-1]]
    if len(closes) < 51:
        raise RuntimeError("not enough closed candles")
    return closes

def save(obj, path):
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")

def defaults(config):
    return {
        "active": False, "startedAt": None, "lastRun": None,
        "cash": config["initialCash"], "initialCash": config["initialCash"],
        "positions": {}, "trades": [], "lastReport": None, "equityHistory": [],
    }

def cycle_due(state, now):
    last_run = state.get("lastRun")
    if not last_run:
        return True
    last_at = datetime.fromisoformat(last_run.replace("Z", "+00:00"))
    return now - last_at >= timedelta(hours=4)

def cycle_status(state, now):
    """Expose the due calculation in Actions logs without changing account state."""
    last_run = state.get("lastRun")
    if not last_run:
        return {"lastRun": None, "elapsedSeconds": None, "due": True}
    last_at = datetime.fromisoformat(last_run.replace("Z", "+00:00"))
    elapsed = max(0, int((now - last_at).total_seconds()))
    return {"lastRun": last_run, "elapsedSeconds": elapsed, "due": elapsed >= 4 * 60 * 60}

def market_for(config, candles):
    fast_period, slow_period = config.get("fastPeriod", 20), config.get("slowPeriod", 50)
    return {
        symbol: {
            "pair": KRAKEN_EUR_PAIRS[symbol],
            "price": closes[-1],
            "bullish": statistics.mean(closes[-fast_period:]) > statistics.mean(closes[-slow_period:]),
            "rsi": round(rsi(closes), 2),
        }
        for symbol, closes in candles.items() if symbol in config["symbols"]
    }

def decision_reason(symbol, market, state, actions, config):
    data = market.get(symbol)
    if not data:
        return "market data unavailable", "NO DATA"
    action = next((item for item in actions if item["symbol"] == symbol), None)
    if action:
        return action["reason"], action["side"]
    if symbol in state["positions"]:
        return "position remains within exit rules", "HOLD"
    if state.get("entryBlockReason"):
        return state["entryBlockReason"], "SKIP"
    if not data["bullish"]:
        return "trend not positive", "SKIP"
    if data["rsi"] < config.get("rsiMin", 52):
        return "RSI below entry range", "SKIP"
    if data["rsi"] > config.get("rsiMax", 68):
        return "RSI above entry range", "SKIP"
    return "position limit or cash constraint", "SKIP"

def run_portfolio(name, config, state, market, skipped, now):
    state = {**defaults(config), **state}
    if not state["active"]:
        state["active"], state["startedAt"] = True, now
    actions = []
    for symbol, position in list(state["positions"].items()):
        data = market.get(symbol)
        if not data:
            continue
        change = data["price"] / position["entryPrice"] - 1
        should_sell = change <= -config["stopLoss"] or change >= config["takeProfit"] or not data["bullish"]
        if not should_sell:
            continue
        reason = "stop loss" if change <= -config["stopLoss"] else "take profit" if change >= config["takeProfit"] else "trend reversal"
        gross = position["units"] * data["price"]
        fee = gross * config["feeRate"]
        state["cash"] += gross - fee
        trade = {"at": now, "side": "SELL", "symbol": symbol, "price": data["price"], "units": position["units"], "fee": round(fee, 2), "reason": reason}
        state["trades"].append(trade)
        actions.append(trade)
        del state["positions"][symbol]

    if config.get("riskControls") and actions:
        hours = config["stopCooldownHours"] if any(t["reason"] == "stop loss" for t in actions) else 4
        state["entryBlockedUntil"] = (datetime.fromisoformat(now.replace("Z", "+00:00")) + timedelta(hours=hours)).isoformat().replace("+00:00", "Z")
    state["entryBlockReason"] = entry_block(config, state, market, actions, now)

    candidates = sorted(
        (symbol for symbol, data in market.items()
         if data["bullish"] and config.get("rsiMin", 52) <= data["rsi"] <= config.get("rsiMax", 68)
         and symbol not in state["positions"]),
        key=lambda symbol: market[symbol]["rsi"], reverse=True,
    )
    while candidates and len(state["positions"]) < config["maxPositions"]:
        if state.get("entryBlockReason"):
            break
        symbol = candidates.pop(0)
        budget = min(state["cash"], state["initialCash"] * config["allocation"])
        if config.get("riskControls"):
            invested = sum(p["units"] * market[s]["price"] for s, p in state["positions"].items())
            equity_now = state["cash"] + invested
            # Limit new spending without liquidating inherited positions. Using gross
            # budget for headroom conservatively accounts for entry fees.
            headroom = max(0, config["maxExposure"] * equity_now - invested)
            budget = min(budget, equity_now * config["allocation"], headroom)
            if budget < 10:
                state["entryBlockReason"] = "exposure cap or cash below minimum entry"
        if budget < 10:
            break
        data = market[symbol]
        fee = budget * config["feeRate"]
        units = (budget - fee) / data["price"]
        state["cash"] -= budget
        state["positions"][symbol] = {"units": units, "entryPrice": data["price"], "openedAt": now}
        trade = {"at": now, "side": "BUY", "symbol": symbol, "price": data["price"], "units": units, "fee": round(fee, 2), "reason": "trend + RSI entry"}
        state["trades"].append(trade)
        actions.append(trade)

    positions_value = sum(position["units"] * market[symbol]["price"] for symbol, position in state["positions"].items() if symbol in market)
    equity = round(state["cash"] + positions_value, 2)
    decisions = [{"symbol": symbol, "decision": decision_reason(symbol, market, state, actions, config)[1], "reason": decision_reason(symbol, market, state, actions, config)[0]} for symbol in config["symbols"]]
    report = {
        "portfolio": name, "experiment": config["experiment"], "version": config["version"], "generatedAt": now,
        "coverage": {"configured": len(config["symbols"]), "eligible": len(market), "skipped": skipped},
        "cash": round(state["cash"], 2), "positionsValue": round(positions_value, 2), "equity": equity,
        "pnl": round(equity - state["initialCash"], 2), "openPositions": state["positions"],
        "actions": actions, "decisions": decisions, "market": market,
    }
    state["lastRun"], state["lastReport"] = now, report
    state["equityHistory"] = (state.get("equityHistory", []) + [{"at": now, "equity": equity, "cash": round(state["cash"], 2), "positionsValue": round(positions_value, 2), "btcPrice": market.get("BTC", {}).get("price")}])[-500:]
    state.setdefault("benchmarks", {"recordedAt": now, "btcPrice": market.get("BTC", {}).get("price")})
    return state, report

def main():
    now_dt = datetime.now(timezone.utc).replace(microsecond=0)
    now = now_dt.isoformat().replace("+00:00", "Z")
    configs = [(name, json.loads(config_path.read_text()), state_path, report_path) for name, config_path, state_path, report_path in PORTFOLIOS]
    current_states = {
        name: json.loads(state_path.read_text()) if state_path.exists() else defaults(config)
        for name, config, state_path, _ in configs
    }
    for name, config, state_path, report_path in configs:
        if config.get("parentPortfolio") and not state_path.exists():
            current_states[name] = fork_state(current_states[config["parentPortfolio"]], name, config, now)
            save(current_states[name], state_path)
            save(current_states[name]["lastReport"], report_path)
    due = {name: cycle_status(state, now_dt) for name, state in current_states.items()}
    print(json.dumps({"at": now, "event": "cycle_due_check", "portfolios": due}))
    if not any(status["due"] for status in due.values()):
        print(json.dumps({"at": now, "skipped": True, "reason": "next cycle not due", "portfolios": due}))
        return

    universe = configs[0][1]["symbols"]
    requested, skipped = [], []
    for symbol in universe:
        pair_id = KRAKEN_EUR_PAIRS.get(symbol)
        if pair_id:
            requested.append((symbol, pair_id))
        else:
            skipped.append({"symbol": symbol, "reason": "EUR pair unavailable on Kraken"})
    candles = {}
    with ThreadPoolExecutor(max_workers=16) as executor:
        futures = {executor.submit(closed_candles, pair_id): symbol for symbol, pair_id in requested}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                candles[symbol] = future.result()
            except Exception as exc:
                skipped.append({"symbol": symbol, "reason": str(exc)})

    reports = {}
    for name, config, state_path, report_path in configs:
        state, report = run_portfolio(name, config, current_states[name], market_for(config, candles), skipped, now)
        save(state, state_path)
        save(report, report_path)
        reports[name] = report
    save({"updatedAt": now, "portfolios": reports}, ROOT / "data" / "portfolio_summary.json")
    print(json.dumps({"at": now, "portfolios": {name: {"equity": report["equity"], "actions": len(report["actions"])} for name, report in reports.items()}}))

if __name__ == "__main__":
    main()
