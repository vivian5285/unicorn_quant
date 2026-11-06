# -*- coding: utf-8 -*-
"""模拟盘：用币安实时价撮合的纸面账户（默认且唯一会真正"成交"的通道）。单账户、JSON落盘。"""
from __future__ import annotations

import json
import os
import threading
import time

from . import data

FEE = 0.0005
PATH = os.environ.get("UQ_PAPER_FILE") or os.path.join(os.path.dirname(__file__), "..", "paper_state.json")
_lock = threading.Lock()
START = 10000.0


def _load():
    try:
        with open(PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"cash": START, "positions": {}, "history": [], "created": int(time.time())}


def _save(st):
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False)
    os.replace(tmp, PATH)


def snapshot():
    with _lock:
        st = _load()
    eq = st["cash"]
    pos_out = []
    for sym, p in st["positions"].items():
        try:
            px = data.last_price(sym)
        except Exception:
            px = p["entry"]
        d = 1 if p["side"] == "LONG" else -1
        pnl = (px - p["entry"]) * p["qty"] * d
        eq += p["margin"] + pnl
        pos_out.append({**p, "symbol": sym, "mark": px, "pnl": round(pnl, 2),
                        "pnl_pct": round(pnl / p["margin"] * 100, 2)})
    return {"cash": round(st["cash"], 2), "equity": round(eq, 2), "start": START, "positions": pos_out,
            "history": st["history"][-100:][::-1]}


def open_pos(symbol: str, side: str, usdt: float, leverage: float):
    symbol, side = symbol.upper(), side.upper()
    if side not in ("LONG", "SHORT"):
        raise ValueError("side 必须是 LONG/SHORT")
    if not (0 < leverage <= 20):
        raise ValueError("杠杆范围 0-20x")
    if usdt <= 0:
        raise ValueError("金额必须>0")
    px = data.last_price(symbol)
    with _lock:
        st = _load()
        if symbol in st["positions"]:
            raise ValueError(f"{symbol} 已有持仓，请先平仓")
        fee = usdt * leverage * FEE
        if usdt + fee > st["cash"]:
            raise ValueError(f"可用资金不足(可用 {st['cash']:.2f} USDT)")
        st["cash"] -= usdt + fee
        st["positions"][symbol] = {"side": side, "entry": px, "qty": usdt * leverage / px, "margin": usdt,
                                   "lev": leverage, "t": int(time.time() * 1000)}
        _save(st)
    return {"symbol": symbol, "side": side, "price": px}


def close_pos(symbol: str):
    symbol = symbol.upper()
    px = data.last_price(symbol)
    with _lock:
        st = _load()
        p = st["positions"].pop(symbol, None)
        if not p:
            raise ValueError("没有该持仓")
        d = 1 if p["side"] == "LONG" else -1
        pnl = (px - p["entry"]) * p["qty"] * d - p["qty"] * px * FEE
        st["cash"] += p["margin"] + pnl
        st["history"].append({"symbol": symbol, "side": p["side"], "entry": p["entry"], "exit": px,
                              "pnl": round(pnl, 2), "t_open": p["t"], "t_close": int(time.time() * 1000)})
        _save(st)
    return {"symbol": symbol, "price": px, "pnl": round(pnl, 2)}


def reset():
    with _lock:
        _save({"cash": START, "positions": {}, "history": [], "created": int(time.time())})
