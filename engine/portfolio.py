# -*- coding: utf-8 -*-
"""美股组合回测：多只股票共用一份资金，"底部拐头买入 / 盈利才卖 / 满仓换手"。

约定：
  - 信号在 T 日收盘后计算，T+1 日开盘价成交（不偷看当天价格）
  - 每只股票最多一个仓位；每个仓位的目标金额 = 昨日收盘净值 / 槽位数（资金不足则按剩余现金）
  - 卖出规则：浮盈 ≥ tp 才卖（可选：止损 stop）；"换手"只允许把已盈利 ≥ rot_min 的仓位换成新机会
  - 手续费单边 fee（含滑点的粗略估计）
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional

from . import ta

DAY = 86400000


def _aligned(bars: List[dict], cal: List[int]):
    """把一只股票的指标/价格对齐到公共日历，缺失为None。返回 (O,H,L,C, sig_inputs dict)"""
    n = len(bars)
    c = [b["c"] for b in bars]
    o = [b["o"] for b in bars]
    h = [b["h"] for b in bars]
    l = [b["l"] for b in bars]
    e10 = ta.ema(c, 10)
    rs = ta.rsi(c, 14)
    ml, _, mh = ta.macd(c, 12, 26, 9)
    maxc = [None] * n
    dd = [None] * n
    for i in range(119, n):
        maxc[i] = max(c[i - 119:i + 1])
        dd[i] = c[i] / maxc[i] - 1
    ddmin = [None] * n
    for i in range(119 + 19, n):
        ddmin[i] = min(dd[i - 19:i + 1])
    sig = {"turn": [False] * n, "rsi": [False] * n, "macd": [False] * n, "hl": [False] * n}
    for i in range(160, n):
        dm = ddmin[i]
        if dm is None:
            continue
        if dm <= -0.20 and e10[i] is not None and e10[i - 1] is not None and c[i - 1] <= e10[i - 1] and c[i] > e10[i] and c[i] > o[i]:
            sig["turn"][i] = True
        if dm <= -0.15 and rs[i] is not None and rs[i - 1] is not None and rs[i - 1] < 30 <= rs[i]:
            sig["rsi"][i] = True
        if dm <= -0.15 and mh[i] is not None and mh[i - 1] is not None and mh[i - 1] < 0 <= mh[i] and ml[i] is not None and ml[i] < 0:
            sig["macd"][i] = True
        if dm <= -0.20 and i >= 20 and min(l[i - 9:i + 1]) > min(l[i - 19:i - 9]) and c[i] > max(h[i - 5:i]):
            sig["hl"][i] = True
    idx = {b["t"] // DAY: k for k, b in enumerate(bars)}
    O, H, L, C, DM = [], [], [], [], []
    S = {k: [] for k in sig}
    for d in cal:
        k = idx.get(d)
        if k is None:
            O.append(None); H.append(None); L.append(None); C.append(None); DM.append(None)
            for kk in S:
                S[kk].append(False)
        else:
            O.append(o[k]); H.append(h[k]); L.append(l[k]); C.append(c[k]); DM.append(ddmin[k])
            for kk in S:
                S[kk].append(sig[kk][k])
    return {"O": O, "H": H, "L": L, "C": C, "DM": DM, "S": S}


def prepare(data: Dict[str, List[dict]], cal_sym: str = "SPY", min_bars: int = 400):
    cal = [b["t"] // DAY for b in data[cal_sym]]
    arr = {s: _aligned(b, cal) for s, b in data.items() if len(b) >= min_bars}
    return cal, arr


def simulate(cal, arr, signal: str = "turn", tp: Optional[float] = 0.20, stop: Optional[float] = None, slots: int = 10,
             rotate: bool = False, trail: Optional[float] = None, trail_on: float = 0.15, rot_min: float = 0.05, fee: float = 0.0005, start: int = 170, equity0: float = 100000.0,
             universe: Optional[List[str]] = None) -> dict:
    syms = [s for s in (universe or list(arr)) if s in arr]
    cash = equity0
    pos: Dict[str, dict] = {}
    pend_sell: Dict[str, str] = {}
    pend_buy: List[str] = []
    eq: List[float] = []
    exposure: List[float] = []
    closed: List[dict] = []
    last_close = {s: None for s in syms}
    peak, maxdd = equity0, 0.0
    deep_under: List[float] = []
    n = len(cal)
    for t in range(start, n):
        # ---- 1) 开盘执行昨日决定 ----
        eq_prev = eq[-1] if eq else equity0
        for s, why in list(pend_sell.items()):
            px = arr[s]["O"][t]
            if px is None:
                continue
            p = pos.pop(s)
            val = p["sh"] * px
            cash += val * (1 - fee)
            closed.append({"s": s, "ret": (px * (1 - fee)) / p["cost"] - 1, "days": t - p["t0"], "why": why})
            del pend_sell[s]
        for s in pend_buy:
            px = arr[s]["O"][t]
            if px is None or s in pos:
                continue
            amt = min(cash, eq_prev / slots)
            if amt < eq_prev / slots * 0.3:
                continue
            sh = amt * (1 - fee) / px
            cash -= amt
            pos[s] = {"sh": sh, "cost": amt / sh, "t0": t}
        pend_buy = []
        # ---- 2) 收盘估值 ----
        val = 0.0
        under = 0.0
        for s in syms:
            c = arr[s]["C"][t]
            if c is not None:
                last_close[s] = c
        for s, p in pos.items():
            c = last_close[s] or p["cost"]
            v = p["sh"] * c
            p["pk"] = max(p.get("pk", p["cost"]), c)
            val += v
            if c / p["cost"] - 1 < -0.20:
                under += v
        e = cash + val
        eq.append(e)
        exposure.append(val / e if e else 0)
        deep_under.append(under / e if e else 0)
        peak = max(peak, e)
        maxdd = max(maxdd, 1 - e / peak)
        # ---- 3) 决定明日卖出 ----
        for s, p in pos.items():
            if s in pend_sell:
                continue
            c = last_close[s]
            if c is None:
                continue
            g = c / p["cost"] - 1
            if tp is not None and g >= tp:
                pend_sell[s] = "止盈"
            elif stop is not None and g <= -stop:
                pend_sell[s] = "止损"
            elif trail is not None and p.get("pk", p["cost"]) / p["cost"] - 1 >= trail_on and c <= p["pk"] * (1 - trail):
                pend_sell[s] = "追踪"
        # ---- 4) 今日信号 → 明日买入 ----
        free = slots - (len(pos) - len(pend_sell))
        cands = [(arr[s]["DM"][t] or 0, s) for s in syms if arr[s]["S"][signal][t] and s not in pos]
        cands.sort()  # 回撤越深越靠前
        if cands and free < len(cands) and rotate:
            # 满仓：把"已盈利>=rot_min 且最赚钱"的仓位换成新机会
            need = len(cands) - max(free, 0)
            ranked = sorted([(last_close[s] / p["cost"] - 1, s) for s, p in pos.items() if s not in pend_sell and last_close[s]], reverse=True)
            for g, s in ranked:
                if need <= 0 or g < rot_min:
                    break
                pend_sell[s] = "换手"
                need -= 1
            free = slots - (len(pos) - len(pend_sell))
        pend_buy = [s for _, s in cands[:max(free, 0)]]
    # ---- 统计 ----
    years = max((n - start) / 252, 1e-9)
    final = eq[-1]
    open_unreal = [(s, last_close[s] / p["cost"] - 1, n - 1 - p["t0"]) for s, p in pos.items() if last_close[s]]
    wins = [c for c in closed if c["ret"] > 0]
    h = len(eq) // 2

    def seg(a, b):
        return (eq[b - 1] / eq[a] - 1) * 100 if b - a > 2 else 0.0

    def mdd(a, b):
        pk, m = eq[a], 0.0
        for x in eq[a:b]:
            pk = max(pk, x)
            m = max(m, 1 - x / pk)
        return m * 100

    return {
        "final": round(final), "ret": round((final / equity0 - 1) * 100, 1), "cagr": round(((final / equity0) ** (1 / years) - 1) * 100, 1),
        "maxdd": round(maxdd * 100, 1), "calmar": round(((final / equity0) ** (1 / years) - 1) / maxdd, 2) if maxdd > 0 else None,
        "exposure": round(sum(exposure) / len(exposure) * 100), "deep_under_max": round(max(deep_under) * 100, 1),
        "trades": len(closed), "win": round(len(wins) / len(closed) * 100) if closed else 0,
        "avg_ret": round(sum(c["ret"] for c in closed) / len(closed) * 100, 1) if closed else 0,
        "avg_days": round(sum(c["days"] for c in closed) / len(closed)) if closed else 0,
        "stops": sum(1 for c in closed if c["why"] == "止损"), "rotations": sum(1 for c in closed if c["why"] == "换手"),
        "open_n": len(open_unreal), "open_under": sum(1 for _, g, _ in open_unreal if g < 0),
        "open_worst": round(min((g for _, g, _ in open_unreal), default=0) * 100, 1),
        "open_longest": max((d for _, _, d in open_unreal), default=0),
        "h1": round(seg(0, h), 1), "h2": round(seg(h, len(eq)), 1), "dd1": round(mdd(0, h), 1), "dd2": round(mdd(h, len(eq)), 1),
        "years": round(years, 1), "eq": eq, "open": sorted(open_unreal, key=lambda x: x[1])[:6],
    }


def buy_hold(cal, arr, syms, start=170, equity0=100000.0):
    """等权买入持有（起点一次性买入，之后不再平衡）"""
    n = len(cal)
    ok = [s for s in syms if s in arr and arr[s]["C"][start] is not None]
    w = equity0 / len(ok)
    sh = {s: w / arr[s]["O"][start + 1] for s in ok}
    last = {s: arr[s]["C"][start] for s in ok}
    eq = []
    for t in range(start, n):
        for s in ok:
            if arr[s]["C"][t] is not None:
                last[s] = arr[s]["C"][t]
        eq.append(sum(sh[s] * last[s] for s in ok))
    pk, m = eq[0], 0.0
    for x in eq:
        pk = max(pk, x)
        m = max(m, 1 - x / pk)
    years = (n - start) / 252
    return {"ret": round((eq[-1] / equity0 - 1) * 100, 1), "cagr": round(((eq[-1] / equity0) ** (1 / years) - 1) * 100, 1),
            "maxdd": round(m * 100, 1), "eq": eq}
