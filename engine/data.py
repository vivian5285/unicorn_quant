# -*- coding: utf-8 -*-
"""行情数据：币安合约公开K线（带短缓存）+ 品种列表。无需API Key。"""
from __future__ import annotations

import os
import sys
import threading
import time
from typing import List, Optional

import requests

# 复用已有的 strategy_engine（只读K线/策略纯函数，不碰任何实盘监督进程）
REPO = os.environ.get("UQ_REPO") or os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

FAPI = "https://fapi.binance.com"
INTERVALS = ["1m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "1w"]
_lock = threading.Lock()
_cache: dict = {}


def _cached(key, ttl, fn):
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    val = fn()
    with _lock:
        _cache[key] = (now, val)
    return val


def _to_bars(raw) -> List[dict]:
    out = []
    for r in raw or []:
        try:
            out.append({"t": int(r[0]), "o": float(r[1]), "h": float(r[2]), "l": float(r[3]),
                        "c": float(r[4]), "v": float(r[5])})
        except (TypeError, ValueError, IndexError):
            continue
    out.sort(key=lambda b: b["t"])
    return out


def _raw(symbol, interval, limit, end=None):
    p = {"symbol": symbol, "interval": interval, "limit": min(limit, 1500)}
    if end:
        p["endTime"] = end
    last = None
    for i in range(3):
        try:
            r = requests.get(FAPI + "/fapi/v1/klines", params=p, timeout=10)
            # 2026-10-18 搬到实盘VPS：本IP币安权重(含实盘)超过900就等到下一分钟，给实盘让路
            if int(r.headers.get('X-MBX-USED-WEIGHT-1M') or 0) >= 900:
                time.sleep(61 - time.time() % 60)
            if r.status_code == 200:
                return r.json()
            if r.status_code < 500:
                raise ValueError(f"币安返回 {r.status_code}: {r.text[:120]}")
            last = r.text
        except requests.RequestException as e:
            last = str(e)
        time.sleep(0.5 * (i + 1))
    raise RuntimeError(f"行情拉取失败: {last}")


def get_klines(symbol: str, interval: str, limit: int = 500, closed_only: bool = False) -> List[dict]:
    symbol = symbol.upper()
    from . import stocks
    if stocks.is_stock(symbol):
        return stocks.get_klines(symbol, interval, limit, closed_only)
    if interval not in INTERVALS:
        raise ValueError(f"不支持的周期 {interval}")
    limit = max(10, min(int(limit), 30000))
    ttl = 2 if limit <= 5 else 20

    def load():
        remaining, end, raw_all = limit, None, []
        while remaining > 0:
            batch = min(1500, remaining)
            raw = _raw(symbol, interval, batch, end)
            if not raw:
                break
            raw_all = raw + raw_all
            remaining -= len(raw)
            end = int(raw[0][0]) - 1
            if len(raw) < batch:
                break
        return _to_bars(raw_all)

    bars = _cached(("k", symbol, interval, limit), ttl, load)
    if closed_only and bars:
        bars = bars[:-1]
    return bars


def symbols() -> List[dict]:
    def load():
        r = requests.get(FAPI + "/fapi/v1/exchangeInfo", timeout=15)
        r.raise_for_status()
        out = []
        for s in r.json().get("symbols", []):
            if s.get("status") == "TRADING" and s.get("quoteAsset") == "USDT" and s.get("contractType") in (
                    "PERPETUAL", "TRADIFI_PERPETUAL"):
                out.append({"symbol": s["symbol"], "base": s["baseAsset"],
                            "tradfi": s.get("contractType") == "TRADIFI_PERPETUAL"})
        return sorted(out, key=lambda x: x["symbol"])
    from . import stocks
    stk = [{"symbol": k, "base": k, "tradfi": False, "stock": True, "name": v["name"], "sector": v["sector"]}
           for k, v in {**stocks.SYMBOLS, **stocks.EXTRA_META}.items()]
    return _cached("symbols", 3600, load) + stk


def last_price(symbol: str) -> float:
    def load():
        r = requests.get(FAPI + "/fapi/v1/ticker/price", params={"symbol": symbol.upper()}, timeout=8)
        r.raise_for_status()
        return float(r.json()["price"])
    return _cached(("p", symbol.upper()), 1.5, load)


def tickers_24h() -> dict:
    def load():
        r = requests.get(FAPI + "/fapi/v1/ticker/24hr", timeout=15)
        r.raise_for_status()
        return {x["symbol"]: {"price": float(x["lastPrice"]), "chg": float(x["priceChangePercent"]),
                              "vol": float(x["quoteVolume"]), "hi": float(x["highPrice"]), "lo": float(x["lowPrice"])} for x in r.json()}
    return _cached("t24", 20, load)
