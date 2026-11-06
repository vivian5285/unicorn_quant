# -*- coding: utf-8 -*-
"""指标库（纯Python，输出与K线等长、前段不足处为None，前端直接对齐画线）。"""
from __future__ import annotations

import math
from typing import List, Optional

Series = List[Optional[float]]


def sma(v: List[float], n: int) -> Series:
    out: Series = [None] * len(v)
    s = 0.0
    for i, x in enumerate(v):
        s += x
        if i >= n:
            s -= v[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def ema(v: List[float], n: int) -> Series:
    out: Series = [None] * len(v)
    if len(v) < n:
        return out
    k = 2.0 / (n + 1)
    prev = sum(v[:n]) / n
    out[n - 1] = prev
    for i in range(n, len(v)):
        prev = v[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def stdev(v: List[float], n: int) -> Series:
    out: Series = [None] * len(v)
    for i in range(n - 1, len(v)):
        w = v[i - n + 1:i + 1]
        m = sum(w) / n
        out[i] = math.sqrt(sum((x - m) ** 2 for x in w) / n)
    return out


def boll(v: List[float], n: int = 20, mult: float = 2.0):
    mid = sma(v, n)
    sd = stdev(v, n)
    up = [None if m is None else m + mult * s for m, s in zip(mid, sd)]
    lo = [None if m is None else m - mult * s for m, s in zip(mid, sd)]
    return up, mid, lo


def rsi(v: List[float], n: int = 14) -> Series:
    out: Series = [None] * len(v)
    if len(v) <= n:
        return out
    gains = losses = 0.0
    for i in range(1, n + 1):
        d = v[i] - v[i - 1]
        gains += max(d, 0)
        losses += max(-d, 0)
    ag, al = gains / n, losses / n
    out[n] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(n + 1, len(v)):
        d = v[i] - v[i - 1]
        ag = (ag * (n - 1) + max(d, 0)) / n
        al = (al * (n - 1) + max(-d, 0)) / n
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def macd(v: List[float], fast=12, slow=26, sig=9):
    ef, es = ema(v, fast), ema(v, slow)
    line: Series = [None if a is None or b is None else a - b for a, b in zip(ef, es)]
    valid = [x for x in line if x is not None]
    sg_valid = ema(valid, sig)
    signal: Series = [None] * len(v)
    j = 0
    for i, x in enumerate(line):
        if x is not None:
            signal[i] = sg_valid[j]
            j += 1
    hist: Series = [None if a is None or b is None else a - b for a, b in zip(line, signal)]
    return line, signal, hist


def atr(bars: List[dict], n: int = 14) -> Series:
    out: Series = [None] * len(bars)
    if len(bars) <= n:
        return out
    trs = [bars[0]["h"] - bars[0]["l"]]
    for i in range(1, len(bars)):
        h, l, pc = bars[i]["h"], bars[i]["l"], bars[i - 1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    a = sum(trs[1:n + 1]) / n
    out[n] = a
    for i in range(n + 1, len(bars)):
        a = (a * (n - 1) + trs[i]) / n
        out[i] = a
    return out


def supertrend(bars: List[dict], n: int = 10, mult: float = 3.0):
    """返回 (线, 方向[1多/-1空])"""
    a = atr(bars, n)
    line: Series = [None] * len(bars)
    dirs: List[int] = [0] * len(bars)
    fu = fl = None
    d = 1
    for i, b in enumerate(bars):
        if a[i] is None:
            continue
        hl2 = (b["h"] + b["l"]) / 2
        bu, bl = hl2 + mult * a[i], hl2 - mult * a[i]
        pc = bars[i - 1]["c"]
        fu = bu if fu is None or bu < fu or pc > fu else fu
        fl = bl if fl is None or bl > fl or pc < fl else fl
        if d == 1 and b["c"] < fl:
            d = -1
        elif d == -1 and b["c"] > fu:
            d = 1
        line[i] = fl if d == 1 else fu
        dirs[i] = d
    return line, dirs


def donchian(bars: List[dict], n: int = 20):
    up: Series = [None] * len(bars)
    lo: Series = [None] * len(bars)
    for i in range(n - 1, len(bars)):
        w = bars[i - n + 1:i + 1]
        up[i] = max(x["h"] for x in w)
        lo[i] = min(x["l"] for x in w)
    return up, lo


def vwap_daily(bars: List[dict]) -> Series:
    out: Series = []
    day = None
    pv = vv = 0.0
    for b in bars:
        d = b["t"] // 86400000
        if d != day:
            day, pv, vv = d, 0.0, 0.0
        tp = (b["h"] + b["l"] + b["c"]) / 3
        pv += tp * b["v"]
        vv += b["v"]
        out.append(pv / vv if vv else None)
    return out


def compute(spec: str, bars: List[dict]) -> List[dict]:
    """spec 形如 'ema:7' 'boll:20:2' 'rsi:14' 'macd:12:26:9' 'st:10:3'。
    返回 [{id,pane,kind,name,data}]，data为 Series 或多条线。"""
    parts = spec.split(":")
    kind = parts[0].lower()
    nums = [float(x) for x in parts[1:] if x]
    c = [b["c"] for b in bars]
    I = lambda i, d: int(nums[i]) if len(nums) > i else d
    F = lambda i, d: float(nums[i]) if len(nums) > i else d
    if kind == "ema":
        n = I(0, 20)
        return [dict(id=spec, pane="main", name=f"EMA({n})", lines={"v": ema(c, n)})]
    if kind == "ma":
        n = I(0, 20)
        return [dict(id=spec, pane="main", name=f"MA({n})", lines={"v": sma(c, n)})]
    if kind == "boll":
        n, m = I(0, 20), F(1, 2.0)
        u, mi, lo = boll(c, n, m)
        return [dict(id=spec, pane="main", name=f"BOLL({n},{m:g})", lines={"up": u, "mid": mi, "lo": lo})]
    if kind == "st":
        n, m = I(0, 10), F(1, 3.0)
        line, dirs = supertrend(bars, n, m)
        return [dict(id=spec, pane="main", name=f"Supertrend({n},{m:g})", lines={"v": line}, dirs=dirs)]
    if kind == "dc":
        n = I(0, 20)
        u, lo = donchian(bars, n)
        return [dict(id=spec, pane="main", name=f"Donchian({n})", lines={"up": u, "lo": lo})]
    if kind == "vwap":
        return [dict(id=spec, pane="main", name="VWAP(日)", lines={"v": vwap_daily(bars)})]
    if kind == "rsi":
        n = I(0, 14)
        return [dict(id=spec, pane="sub", name=f"RSI({n})", lines={"v": rsi(c, n)})]
    if kind == "macd":
        f, s, g = I(0, 12), I(1, 26), I(2, 9)
        line, sig, hist = macd(c, f, s, g)
        return [dict(id=spec, pane="sub", name=f"MACD({f},{s},{g})", lines={"macd": line, "signal": sig}, hist=hist)]
    if kind == "atr":
        n = I(0, 14)
        return [dict(id=spec, pane="sub", name=f"ATR({n})", lines={"v": atr(bars, n)})]
    return []
