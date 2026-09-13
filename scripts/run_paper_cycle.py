#!/usr/bin/env python3
"""Deterministic, spot-only paper-trading cycle using public Kraken OHLC data."""
import json
import os
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "config.json").read_text())
STATE_PATH = ROOT / "data" / "state.json"
REPORT_PATH = ROOT / "data" / "latest_report.json"
KRAKEN = "https://api.kraken.com/0/public/"

KRAKEN_EUR_PAIRS = {
    "BTC": "XBTEUR", "ETH": "ETHEUR", "SOL": "SOLEUR", "XRP": "XRPEUR",
    "ADA": "ADAEUR", "DOGE": "XDGEUR", "DOT": "DOTEUR", "AVAX": "AVAXEUR",
    "LINK": "LINKEUR", "LTC": "LTCEUR", "XLM": "XLMEUR", "ATOM": "ATOMEUR",
    "ALGO": "ALGOEUR", "UNI": "UNIEUR", "AAVE": "AAVEEUR", "FIL": "FILEUR",
    "NEAR": "NEAREUR", "ICP": "ICPEUR", "ETC": "ETCEUR", "BCH": "BCHEUR",
    "MKR": "MKREUR", "SAND": "SANDEUR", "MANA": "MANAEUR", "EOS": "EOSEUR",
    "XTZ": "XTZEUR", "FLOW": "FLOWEUR", "KSM": "KSMEUR", "KAVA": "KAVAEUR",
    "SNX": "SNXEUR", "COMP": "COMPEUR"
}

def api(method, **params):
    url = KRAKEN + method + "?" + urlencode(params)
    with urlopen(url, timeout=8) as response:
        payload = json.load(response)
    if payload.get("error"):
        raise RuntimeError("; ".join(payload["error"]))
    return payload["result"]

def rsi(values, period=14):
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains = [max(0, x) for x in changes[-period:]]
    losses = [max(0, -x) for x in changes[-period:]]
    avg_gain, avg_loss = sum(gains) / period, sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def price_and_signal(pair_id):
    raw = api("OHLC", pair=pair_id, interval=240)
    rows = next(v for k, v in raw.items() if k != "last")
    closes = [float(row[4]) for row in rows[:-1]]  # never decide from an open candle
    if len(closes) < 51:
        raise RuntimeError("not enough closed candles")
    current = closes[-1]
    fast = statistics.mean(closes[-20:])
    slow = statistics.mean(closes[-50:])
    return current, fast > slow, rsi(closes)

def save(obj, path):
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")

def main():
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    state = json.loads(STATE_PATH.read_text())
    if not state["active"]:
        state["active"] = True
        state["startedAt"] = now
    market, skipped = {}, []
    requested = []
    for symbol in CONFIG["symbols"]:
        pair_id = KRAKEN_EUR_PAIRS.get(symbol)
        if not pair_id:
            skipped.append({"symbol": symbol, "reason": "EUR pair unavailable on Kraken"})
            continue
        requested.append((symbol, pair_id))

    # Public requests are independent: parallelism keeps a 30-asset cycle well below
    # GitHub Actions' runtime limits without changing any decision rule.
    with ThreadPoolExecutor(max_workers=16) as executor:
        futures = {executor.submit(price_and_signal, pair_id): (symbol, pair_id) for symbol, pair_id in requested}
        for future in as_completed(futures):
            symbol, pair_id = futures[future]
            try:
                price, bullish, momentum = future.result()
                market[symbol] = {"pair": pair_id, "price": price, "bullish": bullish, "rsi": round(momentum, 2)}
            except Exception as exc:
                skipped.append({"symbol": symbol, "reason": str(exc)})

    actions = []
    # Sell positions first, so risk stays bounded if signals change together.
    for symbol, position in list(state["positions"].items()):
        data = market.get(symbol)
        if not data:
            continue
        change = data["price"] / position["entryPrice"] - 1
        reason = "stop loss" if change <= -CONFIG["stopLoss"] else "take profit" if change >= CONFIG["takeProfit"] else "trend reversal"
        if change <= -CONFIG["stopLoss"] or change >= CONFIG["takeProfit"] or not data["bullish"]:
            gross = position["units"] * data["price"]
            fee = gross * CONFIG["feeRate"]
            proceeds = gross - fee
            state["cash"] += proceeds
            trade = {"at": now, "side": "SELL", "symbol": symbol, "price": data["price"], "units": position["units"], "fee": round(fee, 2), "reason": reason}
            state["trades"].append(trade)
            actions.append(trade)
            del state["positions"][symbol]

    candidates = sorted((s for s, d in market.items() if d["bullish"] and 52 <= d["rsi"] <= 68 and s not in state["positions"]), key=lambda s: market[s]["rsi"], reverse=True)
    while candidates and len(state["positions"]) < CONFIG["maxPositions"]:
        symbol = candidates.pop(0)
        budget = min(state["cash"], state["initialCash"] * CONFIG["allocation"])
        if budget < 10:
            break
        data = market[symbol]
        fee = budget * CONFIG["feeRate"]
        units = (budget - fee) / data["price"]
        state["cash"] -= budget
        state["positions"][symbol] = {"units": units, "entryPrice": data["price"], "openedAt": now}
        trade = {"at": now, "side": "BUY", "symbol": symbol, "price": data["price"], "units": units, "fee": round(fee, 2), "reason": "trend + RSI entry"}
        state["trades"].append(trade)
        actions.append(trade)

    positions_value = sum(p["units"] * market[s]["price"] for s, p in state["positions"].items() if s in market)
    equity = round(state["cash"] + positions_value, 2)
    action_symbols = {a["symbol"] for a in actions}
    decisions = []
    for symbol in CONFIG["symbols"]:
        data = market.get(symbol)
        if not data:
            decisions.append({"symbol": symbol, "decision": "NO DATA", "reason": "market data unavailable"})
        elif symbol in action_symbols:
            action = next(a for a in actions if a["symbol"] == symbol)
            decisions.append({"symbol": symbol, "decision": action["side"], "reason": action["reason"]})
        elif symbol in state["positions"]:
            decisions.append({"symbol": symbol, "decision": "HOLD", "reason": "position remains within exit rules"})
        elif not data["bullish"]:
            decisions.append({"symbol": symbol, "decision": "SKIP", "reason": "trend not positive"})
        elif data["rsi"] < 52:
            decisions.append({"symbol": symbol, "decision": "SKIP", "reason": "RSI below entry range"})
        elif data["rsi"] > 68:
            decisions.append({"symbol": symbol, "decision": "SKIP", "reason": "RSI above entry range"})
        else:
            decisions.append({"symbol": symbol, "decision": "SKIP", "reason": "position limit or cash constraint"})
    report = {"experiment": CONFIG["experiment"], "version": CONFIG["version"], "generatedAt": now, "coverage": {"configured": len(CONFIG["symbols"]), "eligible": len(market), "skipped": skipped}, "cash": round(state["cash"], 2), "positionsValue": round(positions_value, 2), "equity": equity, "pnl": round(equity - state["initialCash"], 2), "openPositions": state["positions"], "actions": actions, "decisions": decisions, "market": market}
    state.setdefault("equityHistory", []).append({"at": now, "equity": equity, "cash": round(state["cash"], 2), "positionsValue": round(positions_value, 2), "btcPrice": market.get("BTC", {}).get("price")})
    state["equityHistory"] = state["equityHistory"][-500:]
    state.setdefault("benchmarks", {"recordedAt": now, "btcPrice": market.get("BTC", {}).get("price")})
    state["lastRun"] = now
    state["lastReport"] = report
    save(state, STATE_PATH)
    save(report, REPORT_PATH)
    print(json.dumps({"at": now, "equity": report["equity"], "actions": len(actions), "eligible": len(market)}))

if __name__ == "__main__":
    main()