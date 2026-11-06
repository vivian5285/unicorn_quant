# -*- coding: utf-8 -*-
"""突破叠加(第四批：JiaSheng 真假突破4h + mio 布林擠壓8h)独立回测核心。2026-11-06 Claude 写。

规则照 strategy_engine/strategies/youtube_candidates_b4.py(擂台实盘用的那份)，但回测口径独立实现：
  - 数据：币安 USDT 永续 4h/8h，只用已收盘K线
  - 信号在K线收盘出，下一根开盘成交；止损/止盈盘中触价，跳空越过按开盘价
  - 成本：吃单+滑点每边 6.5bp，止损单再 +2bp(同研究口径)
  - 仓位：名义 = 权重 × min(1% ÷ 开仓止损距离, 3)，开仓后数量不变，逐日盯市
只用标准库，不依赖 pandas/numpy。
"""
import json
import math
import os
import time
import urllib.request
from collections import defaultdict

H = 3600000
D = 86400000
SYMS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]
FEE = 0.00065
STOPX = 0.0002
STEP = {"4h": 4 * H, "8h": 8 * H}
DATA_START_MS = 1622505600000          # 2021-06-01
# 实盘参数(擂台 comparison_roster.YOUTUBE_CAND_B4_ROSTER)
LIVE_BRK = {"nb": 40, "wmax": 4.0, "big": 1.5}
LIVE_MIO = {"sq_days": 126, "sq_win": 10, "vol": True, "long_only": False}
# 研究里的参数邻域(页面 overlay_series.json 口径)
GRID_BRK = [{"nb": nb, "wmax": w, "big": bg} for nb in (30, 40, 50) for w in (3.5, 4.0, 4.5) for bg in (1.5, 2.5)]
GRID_MIO = [{"sq_days": sd, "sq_win": sw, "vol": v, "long_only": lo}
            for sd in (63, 126) for sw in (5, 10, 20) for v in (True, False) for lo in (False, True)]
# 实盘权重(2%档)：真假突破两半各0.5、布林擠壓1.0
LIVE_W = {"brk_tp": 0.5, "brk_trail": 0.5, "mio": 1.0}


def klines(sym, iv, cache_dir, start_ms):
    """拉永续K线，增量缓存到 cache_dir。只返回已收盘的。"""
    os.makedirs(cache_dir, exist_ok=True)
    p = os.path.join(cache_dir, f"k_{sym}_{iv}.json")
    out = []
    if os.path.exists(p):
        try:
            out = json.load(open(p))
        except Exception:
            out = []
    s = (out[-1]["t"] + 1) if out else min(start_ms, DATA_START_MS)   # 缓存一律从 2021-06 起，各脚本共用
    while True:
        r = json.load(urllib.request.urlopen(
            f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval={iv}&startTime={s}&limit=1500", timeout=30))
        if not r:
            break
        out += [{"t": int(x[0]), "o": float(x[1]), "h": float(x[2]), "l": float(x[3]), "c": float(x[4]), "v": float(x[5])} for x in r]
        if len(r) < 1500:
            break
        s = int(r[-1][0]) + 1
        time.sleep(0.2)
    now = time.time() * 1000
    seen, clean = set(), []
    for b in out:
        if b["t"] in seen or b["t"] + STEP[iv] > now:
            continue
        seen.add(b["t"]); clean.append(b)
    tmp = p + ".tmp"
    json.dump(clean, open(tmp, "w")); os.replace(tmp, p)
    return clean


def _rma(x, n):
    a = 1.0 / n; o = []; p = None
    for v in x:
        p = v if p is None else a * v + (1 - a) * p
        o.append(p)
    return o


def atr(b):
    tr = [b[0]["h"] - b[0]["l"]] + [max(b[i]["h"] - b[i]["l"], abs(b[i]["h"] - b[i - 1]["c"]), abs(b[i]["l"] - b[i - 1]["c"]))
                                    for i in range(1, len(b))]
    return _rma(tr, 14)


def sim_brk(b, mode, start_ms, nb=40, wmax=4.0, big=1.5, rr=1.5, a=None):
    """mode: tp(1.5R止盈那一半) / trail(2×ATR逐根追踪那一半)。返回交易列表，最后一笔可能 open=True。"""
    a = a or atr(b); tr = []; pos = None
    for i in range(nb + 40, len(b) - 1):
        o, h, l, c = b[i]["o"], b[i]["h"], b[i]["l"], b[i]["c"]
        if pos:
            d = pos["d"]; ex = None
            if (d > 0 and l <= pos["stop"]) or (d < 0 and h >= pos["stop"]):
                ex = min(o, pos["stop"]) if d > 0 else max(o, pos["stop"]); why = "止损"; cost = FEE + STOPX
            elif mode == "tp" and ((d > 0 and h >= pos["tp"]) or (d < 0 and l <= pos["tp"])):
                ex = max(o, pos["tp"]) if d > 0 else min(o, pos["tp"]); why = "1.5R止盈"; cost = FEE
            if ex is not None:
                pos.update(exit=ex, exit_t=b[i]["t"], why=why, cost2=cost, open=False); tr.append(pos); pos = None
            else:
                if mode == "trail":
                    ns = c - 2 * a[i] if d > 0 else c + 2 * a[i]
                    pos["stop"] = max(pos["stop"], ns) if d > 0 else min(pos["stop"], ns)
                pos["path"].append((b[i]["t"], c))
            continue
        if b[i]["t"] < start_ms:
            continue
        hi = max(x["h"] for x in b[i - nb:i]); lo = min(x["l"] for x in b[i - nb:i])
        if hi - lo > wmax * a[i]:
            continue
        avgb = sum(abs(x["c"] - x["o"]) for x in b[i - nb:i]) / nb
        body = abs(c - o); mv3 = c - b[i - 2]["o"]
        up3 = all(b[j]["c"] > b[j]["o"] for j in (i, i - 1, i - 2)); dn3 = all(b[j]["c"] < b[j]["o"] for j in (i, i - 1, i - 2))
        if c > hi and ((body >= big * avgb and c > o) or (up3 and mv3 >= big * avgb)):
            d = 1
        elif c < lo and ((body >= big * avgb and c < o) or (dn3 and -mv3 >= big * avgb)):
            d = -1
        else:
            continue
        stop = (hi + lo) / 2; e = b[i + 1]["o"]
        if (d > 0 and stop >= e) or (d < 0 and stop <= e):
            continue
        pos = {"d": d, "sig_t": b[i]["t"], "entry": e, "entry_t": b[i + 1]["t"], "stop": stop, "stop0": stop,
               "rd": abs(e - stop) / e, "tp": e + d * rr * abs(e - stop), "path": []}
    if pos:
        pos.update(exit=b[-1]["c"], exit_t=b[-1]["t"], why="持仓中", cost2=0.0, open=True); tr.append(pos)
    return tr


def mio_bands(b):
    n = 20; c = [x["c"] for x in b]
    mid = [None] * len(b); up = [None] * len(b); dn = [None] * len(b); bw = [None] * len(b)
    for j in range(n - 1, len(b)):
        w = c[j - n + 1:j + 1]; m = sum(w) / n; s = math.sqrt(sum((v - m) ** 2 for v in w) / (n - 1))
        mid[j], up[j], dn[j] = m, m + 2 * s, m - 2 * s; bw[j] = (up[j] - dn[j]) / m
    return mid, up, dn, bw


def mio_squeeze(bw, L, n=20):
    sq = [False] * len(bw)
    for j in range(n - 1 + L // 2, len(bw)):
        lo_ = min(x for x in bw[max(n - 1, j - L + 1):j + 1])
        sq[j] = bw[j] <= lo_ * 1.0001
    return sq


def sim_mio(b, start_ms, sq_days=126, sq_win=10, vol=True, long_only=False, pre=None):
    n = 20; c = [x["c"] for x in b]
    a, (mid, up, dn, bw), sqs = pre if pre else (atr(b), mio_bands(b), {})
    L = int(sq_days * 3)
    sq = sqs.get(L) or mio_squeeze(bw, L)
    tr = []; pos = None
    for i in range(L + n + 5, len(b) - 1):
        o, h, l = b[i]["o"], b[i]["h"], b[i]["l"]
        if pos:
            d = pos["d"]
            if (d > 0 and l <= pos["stop"]) or (d < 0 and h >= pos["stop"]):
                pos.update(exit=min(o, pos["stop"]) if d > 0 else max(o, pos["stop"]), exit_t=b[i]["t"], why="止损", cost2=FEE + STOPX, open=False)
                tr.append(pos); pos = None; continue
            if (d > 0 and c[i] < mid[i] and c[i - 1] < mid[i - 1]) or (d < 0 and c[i] > mid[i] and c[i - 1] > mid[i - 1]):
                pos.update(exit=b[i + 1]["o"], exit_t=b[i + 1]["t"], why="连续两根收回中轨", cost2=FEE, open=False)
                tr.append(pos); pos = None; continue
            pos["path"].append((b[i]["t"], c[i])); continue
        if b[i]["t"] < start_ms:
            continue
        if not any(sq[j] for j in range(i - sq_win + 1, i + 1)):
            continue
        if vol and not b[i]["v"] > 1.5 * sum(x["v"] for x in b[i - 19:i + 1]) / 20:
            continue
        if c[i] > up[i]:
            d, stop = 1, mid[i] - 0.5 * a[i]
        elif c[i] < dn[i] and not long_only:
            d, stop = -1, mid[i] + 0.5 * a[i]
        else:
            continue
        e = b[i + 1]["o"]
        if (d > 0 and stop >= e) or (d < 0 and stop <= e):
            continue
        pos = {"d": d, "sig_t": b[i]["t"], "entry": e, "entry_t": b[i + 1]["t"], "stop": stop, "stop0": stop,
               "rd": abs(e - stop) / e, "path": []}
    if pos:
        pos.update(exit=b[-1]["c"], exit_t=b[-1]["t"], why="持仓中", cost2=0.0, open=True); tr.append(pos)
    return tr


def trade_R(t):
    return (t["d"] * (t["exit"] - t["entry"]) / t["entry"] - FEE - t["cost2"]) / t["rd"]


def add_daily(acc, t, w, eq=None):
    """把一笔交易按天盯市的收益加进 acc[day]。
    仓位按开仓那天的净值定、之后数量不变，所以盈亏要除以"前一天的净值"才是当天收益率：
    eq = {day: 净值}(用方案F曲线代替总净值)，比例 = eq[开仓日] / eq[前一天]；eq=None 时按开仓净值(=1)近似。"""
    f = w * min(0.01 / t["rd"], 3.0)
    d0 = t["entry_t"] // D

    def k(day):
        if not eq:
            return 1.0
        a = eq.get(d0) or eq.get(d0 - 1); b = eq.get(day - 1) or eq.get(day - 2) or eq.get(day - 3) or a
        return a / b if a and b else 1.0
    pts = [(t["entry_t"], t["entry"])] + t["path"] + [(t["exit_t"], t["exit"])]
    for (t0, p0), (t1, p1) in zip(pts, pts[1:]):
        acc[t1 // D] += f * t["d"] * (p1 - p0) / t["entry"] * k(t1 // D)
    acc[d0] -= f * FEE
    if not t.get("open"):
        acc[t["exit_t"] // D] -= f * t["cost2"] * k(t["exit_t"] // D)


def grid_series(bars4, bars8, start_ms, symbols=SYMS, eq=None):
    """研究口径：真假突破18组平均(每组 tp/trail 各0.5 = 1%风险) 和 布林擠壓24组平均(1%风险)，返回 {day: ret}。
    eq：净值曲线 {day: 值}，用来把"按开仓净值定的固定数量"的盈亏换算成当天收益率(见 add_daily)。"""
    brk = defaultdict(float); mio = defaultdict(float)
    for s in symbols:
        b4 = bars4[s]; a4 = atr(b4)
        for g in GRID_BRK:
            for mode in ("tp", "trail"):
                for t in sim_brk(b4, mode, start_ms, a=a4, **g):
                    add_daily(brk, t, 0.5 / len(GRID_BRK), eq)
        b8 = bars8[s]; a8 = atr(b8); bands = mio_bands(b8)
        sqs = {L: mio_squeeze(bands[3], L) for L in {int(g["sq_days"] * 3) for g in GRID_MIO}}
        for g in GRID_MIO:
            for t in sim_mio(b8, start_ms, pre=(a8, bands, sqs), **g):
                add_daily(mio, t, 1.0 / len(GRID_MIO), eq)
    return brk, mio


def live_trades(bars4, bars8, start_ms, symbols=SYMS):
    """实盘参数的逐笔信号：{(strategy, symbol): [trade...]}，strategy 用擂台名字。"""
    out = {}
    for s in symbols:
        b4 = bars4[s]; a4 = atr(b4)
        out[("jiasheng_brk_tp_4h", s)] = sim_brk(b4, "tp", start_ms, a=a4, **LIVE_BRK)
        out[("jiasheng_brk_trail_4h", s)] = sim_brk(b4, "trail", start_ms, a=a4, **LIVE_BRK)
        out[("mio_bb_squeeze_8h", s)] = sim_mio(bars8[s], start_ms, **LIVE_MIO)
    return out
