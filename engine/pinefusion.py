# -*- coding: utf-8 -*-
"""复刻 Pine v5 脚本「ETH融合版 最终最优版 (3H)」并按 TradingView 的成交语义回测。

数据：币安合约15分钟K线 → UTC对齐合成 3H(12根)/1H(4根)。多周期趋势分里的 15m/60m EMA 取"3H K线收盘时刻"的值
      (lookahead_off、低周期请求在历史上返回该根最后一个低周期K线的值)。
成交语义(默认 process_orders_on_close=false)：信号在收盘后算，下一根3H开盘成交；市价/止损单加滑点2tick，限价单无滑点；
      手续费 0.04% 单边；strategy.exit 只在入场那根调用一次，TP1(25%)/TP2(30%)/TP3(45%)各自独立带固定止损，TP2带限价+追踪，TP3仅追踪。
两个需要验证的疑点：
  tick_mode='literal'  trail_points/trail_offset 传的是 atr*倍数(价格单位)，Pine里它们的单位是 tick → 实际追踪距离=atr数值×最小跳动(ETH约几毛钱)
  tick_mode='atr'      按作者"本意"，把追踪距离当作 ATR 倍数的价格距离
path_mode='real'  用15分钟K线还原价格路径(每根15m按 O→H→L→C / O→L→H→C 规则)
path_mode='tv'    TradingView默认：只知道3H的OHLC，按"离开盘近的极值先到"规则
"""
from __future__ import annotations

import json
import os
import time
from typing import Dict, List, Optional

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.environ.get("UQ_FUSION_CACHE") or os.path.join(HERE, "..", "fusion_cache")
FAPI = "https://fapi.binance.com"
H1, H3 = 3600_000, 10800_000


# ------------------------------------------------------------------ 数据
def fetch_15m(symbol: str, n_bars: int, interval: str = "15m") -> List[dict]:
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, f"{symbol}_{interval}_{n_bars}.json")
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < 6 * 3600:
        return json.load(open(path))
    out, end = [], None
    while len(out) < n_bars:
        p = {"symbol": symbol, "interval": interval, "limit": 1500}
        if end:
            p["endTime"] = end
        raw = None
        for k in range(4):
            r = requests.get(FAPI + "/fapi/v1/klines", params=p, timeout=20)
            if r.status_code == 200:
                raw = r.json()
                break
            time.sleep(1 + k)
        if raw is None:
            raise RuntimeError(f"{symbol} {interval} 拉取失败")
        if not raw:
            break
        out = raw + out
        end = int(raw[0][0]) - 1
        if len(raw) < 1500:
            break
        time.sleep(0.12)
    bars = [{"t": int(r[0]), "o": float(r[1]), "h": float(r[2]), "l": float(r[3]), "c": float(r[4]), "v": float(r[5])} for r in out[-n_bars:]]
    bars = bars[:-1]  # 丢掉未收盘的最后一根
    json.dump(bars, open(path, "w"))
    return bars


def tick_size(symbol: str) -> float:
    r = requests.get(FAPI + "/fapi/v1/exchangeInfo", timeout=20).json()
    for s in r["symbols"]:
        if s["symbol"] == symbol:
            for f in s["filters"]:
                if f["filterType"] == "PRICE_FILTER":
                    return float(f["tickSize"])
    return 0.01


# ------------------------------------------------------------------ Pine 风格指标
def ema_pine(v: List[float], n: int) -> List[float]:
    a = 2.0 / (n + 1)
    out, p = [], None
    for x in v:
        p = x if p is None else a * x + (1 - a) * p
        out.append(p)
    return out


def rma_pine(v: List[Optional[float]], n: int) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(v)
    p = None
    buf = []
    for i, x in enumerate(v):
        if x is None:
            continue
        if p is None:
            buf.append(x)
            if len(buf) == n:
                p = sum(buf) / n
                out[i] = p
        else:
            p = (p * (n - 1) + x) / n
            out[i] = p
    return out


def sma_s(v, n):
    out = [None] * len(v)
    s = 0.0
    for i, x in enumerate(v):
        s += x
        if i >= n:
            s -= v[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def build(bars15: List[dict], fine: Optional[List[dict]] = None):
    """15m → 3H 基础K线(带子K线) + 指标/信号"""
    g3: Dict[int, List[dict]] = {}
    for b in bars15:
        g3.setdefault(b["t"] // H3, []).append(b)
    keys = sorted(k for k, v in g3.items() if len(v) == 12)
    base = []
    for k in keys:
        sub = g3[k]
        base.append({"t": sub[0]["t"], "o": sub[0]["o"], "h": max(x["h"] for x in sub), "l": min(x["l"] for x in sub),
                     "c": sub[-1]["c"], "v": sum(x["v"] for x in sub), "sub": sub})
    if fine:
        gf: Dict[int, List[dict]] = {}
        for fb in fine:
            gf.setdefault(fb["t"] // H3, []).append(fb)
        for b in base:
            b["path"] = gf.get(b["t"] // H3)
    # 15m / 1h EMA
    c15 = [b["c"] for b in bars15]
    e15f, e15s = ema_pine(c15, 10), ema_pine(c15, 30)
    idx15 = {b["t"]: i for i, b in enumerate(bars15)}
    g1: Dict[int, List[dict]] = {}
    for b in bars15:
        g1.setdefault(b["t"] // H1, []).append(b)
    k1 = sorted(k for k, v in g1.items() if len(v) == 4)
    c60 = [g1[k][-1]["c"] for k in k1]
    e60f, e60s = ema_pine(c60, 10), ema_pine(c60, 30)
    idx60 = {k: i for i, k in enumerate(k1)}
    n = len(base)
    c = [b["c"] for b in base]; h = [b["h"] for b in base]; l = [b["l"] for b in base]; v = [b["v"] for b in base]
    ef, es = ema_pine(c, 10), ema_pine(c, 30)
    # RSI14
    gain = [None] + [max(c[i] - c[i - 1], 0) for i in range(1, n)]
    loss = [None] + [max(c[i - 1] - c[i], 0) for i in range(1, n)]
    rg, rl = rma_pine(gain, 14), rma_pine(loss, 14)
    rsi = [None if rg[i] is None else (100.0 if rl[i] == 0 else 100 - 100 / (1 + rg[i] / rl[i])) for i in range(n)]
    # stoch K = sma(stoch(close,high,low,14),3)
    st = [None] * n
    for i in range(13, n):
        hh, ll = max(h[i - 13:i + 1]), min(l[i - 13:i + 1])
        st[i] = 100 * (c[i] - ll) / (hh - ll) if hh > ll else 0.0
    stk = [None] * n
    for i in range(15, n):
        stk[i] = sum(st[i - 2:i + 1]) / 3
    # vwma(volume,20)
    vv = [x * x for x in v]
    s1, s2 = sma_s(vv, 20), sma_s(v, 20)
    vwma = [None if s2[i] in (None, 0) else s1[i] / s2[i] for i in range(n)]
    # ATR14 / ADX14
    tr = [h[0] - l[0]] + [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, n)]
    atr = rma_pine(tr, 14)
    pdm, mdm = [None] * n, [None] * n
    for i in range(1, n):
        up, dn = h[i] - h[i - 1], l[i - 1] - l[i]
        pdm[i] = up if (up > dn and up > 0) else 0.0
        mdm[i] = dn if (dn > up and dn > 0) else 0.0
    trr = rma_pine(tr, 14)
    pr, mr = rma_pine(pdm, 14), rma_pine(mdm, 14)
    dx = [None] * n
    for i in range(n):
        if trr[i] and pr[i] is not None and mr[i] is not None:
            pdi, mdi = 100 * pr[i] / trr[i], 100 * mr[i] / trr[i]
            dx[i] = 100 * abs(pdi - mdi) / (pdi + mdi) if (pdi + mdi) > 0 else 0.0
    adx = rma_pine(dx, 14)
    sig = []
    for i, b in enumerate(base):
        last15 = idx15[b["sub"][-1]["t"]]
        hr = idx60.get((b["t"] // H1) + 2)  # 3H内最后一个整点小时
        if hr is None:
            sig.append(None)
            continue
        ts = (1 if ef[i] > es[i] else 0) + (1 if e15f[last15] > e15s[last15] else 0) + (1 if e60f[hr] > e60s[hr] else 0)
        ok = None not in (rsi[i], stk[i], vwma[i], atr[i], adx[i])
        if not ok:
            sig.append(None)
            continue
        total = ts + (1 if adx[i] > 19 else 0) + (1 if rsi[i] > 52 else 0) + (1 if stk[i] > 52 else 0) + (1 if v[i] > vwma[i] * 1.15 else 0)
        sig.append({"ts": ts, "total": total, "atr": atr[i], "long": total >= 4 and ts >= 2 and c[i] > es[i],
                    "short": total <= 1 and ts <= 1 and c[i] < es[i]})
    return base, sig


# ------------------------------------------------------------------ 路径
def _leg_order(o, h, l):
    return (o, h, l) if abs(h - o) < abs(l - o) else (o, l, h)


def path_points(bar, mode):
    """返回 [(价格,是否跳空起点)...]：第一个点是该K线开盘"""
    if mode == "tv":
        o, a, b = _leg_order(bar["o"], bar["h"], bar["l"])
        return [(o, True), (a, False), (b, False), (bar["c"], False)]
    pts = []
    for s in (bar.get("path") or bar["sub"]):
        o, a, b = _leg_order(s["o"], s["h"], s["l"])
        pts += [(o, True), (a, False), (b, False), (s["c"], False)]
    return pts


# ------------------------------------------------------------------ 回测
def simulate(base, sig, mintick, tick_mode="literal", path_mode="real", equity0=1000.0, comm=0.0004, slip_ticks=2,
             risk_pct=1.5, max_daily_loss=5.5, start=60, long_enabled=True, short_enabled=True) -> dict:
    slip = slip_ticks * mintick
    cash = equity0
    pos = 0.0            # 带符号数量
    avg = 0.0
    orders: List[dict] = []
    pend = None
    eq_curve, trades = [], []
    day_start = None
    last_day = None
    cur_trade = None
    last_x = None
    n = len(base)

    def close_portion(o, px_x, s, kind):
        nonlocal cash, pos
        q = o["q"]
        px = s * px_x
        pnl = (px - avg) * q * s
        fee = abs(px) * q * comm
        cash += pnl - fee
        pos -= s * q
        cur_trade["pnl"] += pnl - fee
        cur_trade["exits"].append(kind)
        o["done"] = True

    def walk(x0, x1, gap, s):
        """在"有利方向坐标" x=s*price 上从 x0 走到 x1，触发各出场单。gap=True 表示瞬间跳到 x1(开盘跳空)"""
        for o in orders:
            if o["done"]:
                continue
            if gap:
                if o["lim"] is not None and x1 >= o["lim"]:
                    close_portion(o, x1, s, "limit")
                    continue
                if x1 <= o["stop"]:
                    close_portion(o, x1 - slip, s, "stop")
                    continue
                if o["act"] and x1 <= o["peak"] - o["off"]:
                    close_portion(o, x1 - slip, s, "trail")
                    continue
                if o["trail"] and not o["act"] and x1 >= o["act_x"]:
                    o["act"], o["peak"] = True, x1
                elif o["act"]:
                    o["peak"] = max(o["peak"], x1)
                continue
            if x1 >= x0:  # 向有利方向
                if o["lim"] is not None and x0 <= o["lim"] <= x1:
                    close_portion(o, o["lim"], s, "limit")
                    continue
                if o["trail"] and not o["act"] and x1 >= o["act_x"]:
                    o["act"], o["peak"] = True, x1
                elif o["act"]:
                    o["peak"] = max(o["peak"], x1)
            else:  # 向不利方向
                hit, level = None, None
                if x1 <= o["stop"] <= x0:
                    hit, level = "stop", o["stop"]
                if o["act"]:
                    tl = o["peak"] - o["off"]
                    if x1 <= tl <= x0 and (level is None or tl > level):
                        hit, level = "trail", tl
                if hit:
                    close_portion(o, level - slip, s, hit)

    for i in range(n):
        b = base[i]
        entered_now = False
        # ---- 开盘：执行挂单 ----
        if pend:
            side = pend["side"]
            if pos != 0 and (pos > 0) != (side > 0):
                s_old = 1 if pos > 0 else -1
                px = b["o"] - s_old * slip
                pnl = (px - avg) * abs(pos) * s_old
                fee = px * abs(pos) * comm
                cash += pnl - fee
                cur_trade["pnl"] += pnl - fee
                cur_trade["exits"].append("reverse")
                trades.append(cur_trade)
                cur_trade = None
                pos = 0.0
                orders = []
            if pos == 0:
                entered_now = True
                px = b["o"] + side * slip
                q = pend["qty"]
                cash -= px * q * comm
                pos, avg = side * q, px
                cur_trade = {"t": b["t"], "side": side, "entry": px, "pnl": -px * q * comm, "exits": []}
                a = pend["atr"]
                for pct, lim_m, tr_pts, tr_off in ((0.25, 1.6, None, None), (0.30, 2.8, 1.0, 0.5), (0.45, None, 1.5, 0.75)):
                    o = {"q": q * pct, "done": False, "act": False, "peak": None, "trail": tr_pts is not None, "lim": None,
                         "stop": side * pend["stop"], "act_x": None, "off": None}
                    if lim_m is not None:
                        o["lim"] = side * (pend["ref"] + side * a * lim_m)
                    if tr_pts is not None:
                        mult = mintick if tick_mode == "literal" else 1.0
                        o["act_x"] = side * px + a * tr_pts * mult
                        o["off"] = a * tr_off * mult
                    orders.append(o)
            pend = None
        # ---- 路径内触发 ----
        if pos != 0 and orders:
            s = 1 if pos > 0 else -1
            pts = path_points(b, path_mode)
            prev = last_x if (last_x is not None and not entered_now) else None
            for px, is_open in pts:
                x = s * px
                if prev is None:
                    walk(x, x, True, s)       # 入场那一刻：开盘价若已越过止损/止盈位当场成交
                elif is_open:
                    walk(prev, x, True, s)    # 与前一点之间的开盘跳空
                else:
                    walk(prev, x, False, s)
                prev = x
                if all(o["done"] for o in orders):
                    break
            last_x = prev
            if all(o["done"] for o in orders):
                orders = []
                pos = 0.0
                trades.append(cur_trade)
                cur_trade = None
                last_x = None
        else:
            last_x = None
        # ---- 收盘：估值 + 信号 ----
        mark = cash + (pos * (b["c"] - avg) if pos else 0.0)
        eq_curve.append(mark)
        day = b["t"] // 86400000
        if last_day is not None and day != last_day:
            day_start = mark
        last_day = day
        sg = sig[i]
        if sg is None or i < start or i == n - 1:
            continue
        can = day_start is not None and (day_start - mark) / day_start * 100 < max_daily_loss
        a = sg["atr"]
        dist = max(a * 1.4, mintick)
        qty = max(mark * risk_pct / 100 / dist, 0.001)
        if sg["long"] and long_enabled and pos <= 0 and can:
            pend = {"side": 1, "qty": qty, "atr": a, "stop": b["c"] - a * 1.4, "ref": b["c"]}
        elif sg["short"] and short_enabled and pos >= 0 and can:
            pend = {"side": -1, "qty": qty, "atr": a, "stop": b["c"] + a * 1.4, "ref": b["c"]}
    if cur_trade:
        trades.append(cur_trade)
    # ---- 统计 ----
    eq = eq_curve[start:]
    pk, mdd = eq[0], 0.0
    for v in eq:
        pk = max(pk, v)
        mdd = max(mdd, 1 - v / pk)
    wins = [t for t in trades if t["pnl"] > 0]
    gp, gl = sum(t["pnl"] for t in wins), -sum(t["pnl"] for t in trades if t["pnl"] <= 0)
    m = len(eq) // 2
    years = len(eq) * 3 / 24 / 365
    final = eq[-1]
    return {"ret": (final / equity0 - 1) * 100, "cagr": ((final / equity0) ** (1 / years) - 1) * 100 if final > 0 else -100,
            "dd": mdd * 100, "n": len(trades), "win": 100 * len(wins) / len(trades) if trades else 0,
            "pf": gp / gl if gl > 0 else (9.9 if gp > 0 else 0), "h1": (eq[m - 1] / eq[0] - 1) * 100, "h2": (eq[-1] / eq[m] - 1) * 100,
            "avg_pnl": sum(t["pnl"] for t in trades) / len(trades) if trades else 0, "years": years,
            "bh": (base[-1]["c"] / base[start]["c"] - 1) * 100, "trades": trades}
