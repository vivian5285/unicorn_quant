# -*- coding: utf-8 -*-
"""交易所适配层（币安USDT合约）。

安全设计（刻意保守）：
  - 默认只做"验证单"：调用 /fapi/v1/order/test，交易所会校验签名/精度/余额规则，但**绝不成交**。
  - 真实下单必须同时满足：环境变量 UQ_LIVE_ENABLED=1 且 UQ_BINANCE_KEY/UQ_BINANCE_SECRET 已配置，
    且请求带 confirm=true。缺任何一项都会拒绝。
  - API Key 只读环境变量，不落盘、不进日志、不回传前端。
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time
from urllib.parse import urlencode

import requests

from . import data

FAPI = "https://fapi.binance.com"


def status() -> dict:
    has_key = bool(os.environ.get("UQ_BINANCE_KEY") and os.environ.get("UQ_BINANCE_SECRET"))
    live = os.environ.get("UQ_LIVE_ENABLED") == "1"
    return {"key_configured": has_key, "live_enabled": live and has_key,
            "mode": "live" if (live and has_key) else ("test-only" if has_key else "no-key")}


def _signed(method: str, path: str, params: dict):
    key, sec = os.environ.get("UQ_BINANCE_KEY"), os.environ.get("UQ_BINANCE_SECRET")
    if not (key and sec):
        raise PermissionError("未配置交易所API Key（UQ_BINANCE_KEY / UQ_BINANCE_SECRET）")
    params = {**params, "timestamp": int(time.time() * 1000), "recvWindow": 5000}
    q = urlencode(params)
    sig = hmac.new(sec.encode(), q.encode(), hashlib.sha256).hexdigest()
    r = requests.request(method, f"{FAPI}{path}?{q}&signature={sig}", headers={"X-MBX-APIKEY": key}, timeout=10)
    try:
        body = r.json()
    except Exception:
        body = {"raw": r.text[:200]}
    if r.status_code >= 400:
        raise RuntimeError(f"币安拒绝: {body}")
    return body


def _step(symbol: str):
    r = requests.get(FAPI + "/fapi/v1/exchangeInfo", timeout=15).json()
    for s in r["symbols"]:
        if s["symbol"] == symbol:
            for f in s["filters"]:
                if f["filterType"] == "MARKET_LOT_SIZE":
                    return float(f["stepSize"]), float(f["minQty"])
    raise ValueError("未找到品种")


def place_market(symbol: str, side: str, usdt: float, leverage: float, confirm: bool = False) -> dict:
    symbol = symbol.upper()
    px = data.last_price(symbol)
    step, minq = _step(symbol)
    qty = usdt * leverage / px
    qty = max(minq, int(qty / step) * step)
    qty = float(f"{qty:.8f}".rstrip("0").rstrip("."))
    params = {"symbol": symbol, "side": "BUY" if side.upper() == "LONG" else "SELL", "type": "MARKET",
              "quantity": qty}
    st = status()
    if confirm and st["live_enabled"]:
        res = _signed("POST", "/fapi/v1/order", params)
        return {"executed": True, "orderId": res.get("orderId"), "qty": qty, "ref_price": px}
    if confirm and not st["live_enabled"]:
        raise PermissionError("实盘未启用：需 UQ_LIVE_ENABLED=1 且已配置API Key。已拒绝，未下单")
    _signed("POST", "/fapi/v1/order/test", params)
    return {"executed": False, "validated": True, "qty": qty, "ref_price": px,
            "note": "验证单通过（交易所未成交）"}
