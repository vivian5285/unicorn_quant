# -*- coding: utf-8 -*-
"""趋势均线 + 大级别支撑压力 + 盈利换手 的多股票组合回测（只做多现货）。

结构：
  base  = 触发级别K线（阶段1：日线；阶段2：由小时线合成的4小时线）
  htf   = 大级别K线（阶段1：周线；阶段2：日线）—— 用来判断大趋势、画支撑/压力位，只使用"已收盘"的大级别K线
入场(顺趋势，T日收盘判断，T+1开盘成交)：
  cross     快线上穿慢线(收盘在慢线之上)
  cross_sr  同上，且上方最近压力位还有 >= room 的空间
  breakout  收盘突破上方最近压力位(上一根还在压力位之下)，且均线多头
  pullback  均线多头 + 回踩最近支撑位(最低价触及支撑区) 后收阳
  可选过滤：大级别多头(htf_on)、三均线排列(tri：快>慢>EMA100)
出场(收盘判断，次日开盘成交)：
  trend      快线 < 慢线                 （趋势反转）
  slow       收盘 < 慢线                 （更慢的反转）
  trend_rej  trend，或"触及入场时压力位后被打回"(冲高回落收阴)
  止损：none / fix10(-10%) / sr(入场时最近支撑下方，夹在 -5%~-15% 之间)
换手(满仓又出现新信号时，仅允许卖"已盈利"的仓位)：
  none / weak(只卖已盈利且收盘跌破快线的) / active(只要已盈利且相对强度低于新信号)
候选按相对强度(ROC)排序。
"""
from __future__ import annotations

import bisect
from datetime import datetime
from typing import Dict, List, Optional

from . import ta

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover
    _ET = None

PERIOD = {"1d": 86400000, "1w": 604800000, "4h": 14400000}


def resample_4h(bars_1h: List[dict]) -> List[dict]:
    """美股小时线(9:30-16:00，7根) → 每天2根：上午段(前4根 9:30-13:30)、下午段(后3根 13:30-16:00)"""
    days: Dict[str, List[dict]] = {}
    for b in bars_1h:
        d = datetime.fromtimestamp(b["t"] / 1000, _ET).strftime("%Y-%m-%d")
        days.setdefault(d, []).append(b)
    out = []
    for d in sorted(days):
        bs = sorted(days[d], key=lambda x: x["t"])
        for part in (bs[:4], bs[4:]):
            if not part:
                continue
            out.append({"t": part[0]["t"], "o": part[0]["o"], "h": max(x["h"] for x in part), "l": min(x["l"] for x in part),
                        "c": part[-1]["c"], "v": sum(x["v"] for x in part)})
    return out


def _levels(h: List[float], l: List[float], k: int, lookback: int, tol: float = 0.015):
    """每根大级别K线(已确认的分形高低点，聚类) → 该时点可用的支撑/压力价位列表"""
    n = len(h)
    piv = []  # (idx, price)
    for m in range(k, n - k):
        if h[m] >= max(h[m - k:m + k + 1]):
            piv.append((m, h[m]))
        if l[m] <= min(l[m - k:m + k + 1]):
            piv.append((m, l[m]))
    out = []
    ptr = 0
    avail: List[tuple] = []
    for j in range(n):
        while ptr < len(piv) and piv[ptr][0] <= j - k:
            avail.append(piv[ptr])
            ptr += 1
        ps = sorted(p for i, p in avail if i >= j - lookback)
        cl, cur = [], []
        for p in ps:
            if cur and p / (sum(cur) / len(cur)) - 1 > tol:
                cl.append(sum(cur) / len(cur))
                cur = []
            cur.append(p)
        if cur:
            cl.append(sum(cur) / len(cur))
        out.append(cl)
    return out


def prep_symbol(base: List[dict], htf: List[dict], cal: List[int], cfg: dict) -> dict:
    idx = {b["t"]: k for k, b in enumerate(base)}
    c = [b["c"] for b in base]
    ema = {n: ta.ema(c, n) for n in (7, 8, 10, 25, 30, 100)}
    rs_n = cfg["rs_n"]
    # 大级别
    hc = [b["c"] for b in htf]
    hh, hl = [b["h"] for b in htf], [b["l"] for b in htf]
    hf, hs = ta.ema(hc, cfg["htf_fast"]), ta.ema(hc, cfg["htf_slow"])
    htf_up = [hf[j] is not None and hs[j] is not None and hf[j] > hs[j] and hc[j] > hs[j] for j in range(len(htf))]
    lv = _levels(hh, hl, cfg["pivot_k"], cfg["pivot_L"])
    close_t = [b["t"] + PERIOD[cfg["htf"]] for b in htf]
    dn_hi = []  # 唐奇安式：近50根大级别最高 / 近20根最低（不含当前）
    for j in range(len(htf)):
        dn_hi.append((max(hh[max(0, j - 50):j]) if j > 0 else None, min(hl[max(0, j - 20):j]) if j > 0 else None))
    n = len(cal)
    O = [None] * n; H = [None] * n; L = [None] * n; C = [None] * n
    E = {k: [None] * n for k in ema}
    RS = [None] * n
    UP = [False] * n
    RES = [None] * n; SUP = [None] * n; RES2 = [None] * n; SUP2 = [None] * n
    for i, T in enumerate(cal):
        k = idx.get(T)
        if k is None:
            continue
        b = base[k]
        O[i], H[i], L[i], C[i] = b["o"], b["h"], b["l"], b["c"]
        for kk in ema:
            E[kk][i] = ema[kk][k]
        if k >= rs_n:
            RS[i] = c[k] / c[k - rs_n] - 1
        j = bisect.bisect_right(close_t, T) - 1
        if j >= 0:
            UP[i] = htf_up[j]
            p = c[k]
            ups = [x for x in lv[j] if x > p * 1.003]
            dns = [x for x in lv[j] if x < p * 0.997]
            RES[i] = min(ups) if ups else None
            SUP[i] = max(dns) if dns else None
            hi, lo = dn_hi[j]
            RES2[i] = hi if hi is not None and hi > p * 1.003 else None
            SUP2[i] = lo if lo is not None and lo < p * 0.997 else None
    return {"O": O, "H": H, "L": L, "C": C, "E": E, "RS": RS, "UP": UP, "RES": RES, "SUP": SUP, "RES2": RES2, "SUP2": SUP2}


def prepare(base: Dict[str, List[dict]], htf: Dict[str, List[dict]], cfg: dict, cal_sym: str = "SPY", min_bars: int = 300):
    cal = [b["t"] for b in base[cal_sym]]
    pre = {s: prep_symbol(b, htf[s], cal, cfg) for s, b in base.items() if len(b) >= min_bars and s in htf and len(htf[s]) > 60}
    return cal, pre


def entries(pre, sym, pair, mode, tri, htf_on, room, sr="fractal", pull_tol=0.01):
    d = pre[sym]
    f, s = pair
    ef, es, e100 = d["E"][f], d["E"][s], d["E"][100]
    C, O, L = d["C"], d["O"], d["L"]
    RES, SUP = (d["RES"], d["SUP"]) if sr == "fractal" else (d["RES2"], d["SUP2"])
    UP = d["UP"]
    n = len(C)
    out = [False] * n
    for t in range(1, n):
        c = C[t]
        if c is None or ef[t] is None or es[t] is None or ef[t - 1] is None or es[t - 1] is None:
            continue
        if htf_on and not UP[t]:
            continue
        if tri and (e100[t] is None or not es[t] > e100[t]):
            continue
        up = ef[t] > es[t] and c > es[t]
        if not up:
            continue
        if mode == "cross":
            out[t] = ef[t - 1] <= es[t - 1]
        elif mode == "cross_sr":
            out[t] = ef[t - 1] <= es[t - 1] and (RES[t] is None or RES[t] / c - 1 >= room)
        elif mode == "breakout":
            r = RES[t - 1]
            out[t] = r is not None and C[t - 1] is not None and C[t - 1] <= r and c > r * 1.002
        elif mode == "pullback":
            sp = SUP[t - 1]
            out[t] = sp is not None and L[t] is not None and L[t] <= sp * (1 + pull_tol) and c > sp and c > O[t] and (1 - sp / c) <= 0.12
    return out


def simulate(cal, pre, ent: Dict[str, List[bool]], pair, exit_mode="trend", stop_mode="none", rot="none", slots=10,
             rot_min=0.03, fee=0.0005, start=140, equity0=100000.0, sr="fractal", per_year=252) -> dict:
    f, s = pair
    syms = list(ent)
    cash = equity0
    pos: Dict[str, dict] = {}
    pend_sell: Dict[str, str] = {}
    pend_buy: List[str] = []
    eq: List[float] = []
    exposure: List[float] = []
    deep: List[float] = []
    closed: List[dict] = []
    last = {x: None for x in syms}
    peak, maxdd = equity0, 0.0
    n = len(cal)
    for t in range(start, n):
        eq_prev = eq[-1] if eq else equity0
        for x, why in list(pend_sell.items()):
            px = pre[x]["O"][t]
            if px is None:
                continue
            p = pos.pop(x)
            cash += p["sh"] * px * (1 - fee)
            closed.append({"s": x, "ret": px * (1 - fee) / p["cost"] - 1, "days": t - p["t0"], "why": why})
            del pend_sell[x]
        for x in pend_buy:
            px = pre[x]["O"][t]
            if px is None or x in pos:
                continue
            amt = min(cash, eq_prev / slots)
            if amt < eq_prev / slots * 0.3:
                continue
            sh = amt * (1 - fee) / px
            cash -= amt
            sig = pend_meta[x]
            pos[x] = {"sh": sh, "cost": amt / sh, "t0": t, "stop": sig["stop"], "res": sig["res"]}
        pend_buy = []
        val = und = 0.0
        for x in syms:
            c = pre[x]["C"][t]
            if c is not None:
                last[x] = c
        for x, p in pos.items():
            c = last[x] or p["cost"]
            v = p["sh"] * c
            val += v
            if c / p["cost"] - 1 < -0.20:
                und += v
        e = cash + val
        eq.append(e)
        exposure.append(val / e if e else 0)
        deep.append(und / e if e else 0)
        peak = max(peak, e)
        maxdd = max(maxdd, 1 - e / peak)
        # ---- 决定明日卖出 ----
        for x, p in pos.items():
            if x in pend_sell:
                continue
            d = pre[x]
            c = last[x]
            if c is None:
                continue
            ef, es = d["E"][f][t], d["E"][s][t]
            why = None
            if exit_mode in ("trend", "trend_rej") and ef is not None and es is not None and ef < es:
                why = "趋势反转"
            elif exit_mode == "slow" and es is not None and c < es:
                why = "跌破慢线"
            elif stop_mode != "none" and c <= p["stop"]:
                why = "止损"
            elif exit_mode == "trend_rej" and p["res"] is not None:
                if d["H"][t] >= p["res"] * 0.998 and c < p["res"] * 0.995 and c < d["O"][t]:
                    why = "压力位受阻"
                elif c > p["res"] * 1.003:
                    p["res"] = (d["RES"] if sr == "fractal" else d["RES2"])[t]  # 突破后上移压力目标
            if why:
                pend_sell[x] = why
        # ---- 新信号 ----
        cands = [(pre[x]["RS"][t] if pre[x]["RS"][t] is not None else -9, x) for x in syms if ent[x][t] and x not in pos]
        cands.sort(reverse=True)
        free = slots - (len(pos) - len(pend_sell))
        if cands and free < len(cands) and rot != "none":
            need = len(cands) - max(free, 0)
            best_rs = cands[0][0]
            pool = []
            for x, p in pos.items():
                if x in pend_sell or last[x] is None:
                    continue
                g = last[x] / p["cost"] - 1
                if g < rot_min:
                    continue
                d = pre[x]
                rsx = d["RS"][t] if d["RS"][t] is not None else -9
                weak = d["E"][f][t] is not None and last[x] < d["E"][f][t]
                if rot == "weak" and not weak:
                    continue
                if rot == "active" and not (weak or rsx < best_rs):
                    continue
                pool.append((rsx, x))
            pool.sort()
            for _, x in pool[:need]:
                pend_sell[x] = "换手"
            free = slots - (len(pos) - len(pend_sell))
        pend_buy = []
        pend_meta = {} if t == start else pend_meta
        for _, x in cands[:max(free, 0)]:
            d = pre[x]
            c = d["C"][t]
            sup = (d["SUP"] if sr == "fractal" else d["SUP2"])[t]
            if stop_mode == "fix10":
                stp = c * 0.90
            elif stop_mode == "sr":
                dist = 1 - sup * 0.99 / c if sup else 0.08
                stp = c * (1 - min(max(dist, 0.05), 0.15))
            else:
                stp = 0.0
            pend_meta[x] = {"stop": stp, "res": (d["RES"] if sr == "fractal" else d["RES2"])[t]}
            pend_buy.append(x)
    years = max((n - start) / per_year, 1e-9)
    final = eq[-1]
    wins = [c for c in closed if c["ret"] > 0]
    loss = [c for c in closed if c["ret"] <= 0]
    gp, gl = sum(c["ret"] for c in wins), -sum(c["ret"] for c in loss)
    h = len(eq) // 2

    def mdd(a, b):
        pk, m = eq[a], 0.0
        for v in eq[a:b]:
            pk = max(pk, v)
            m = max(m, 1 - v / pk)
        return m * 100

    cagr = ((final / equity0) ** (1 / years) - 1) * 100 if final > 0 else -100
    return {
        "ret": round((final / equity0 - 1) * 100, 1), "cagr": round(cagr, 1), "maxdd": round(maxdd * 100, 1),
        "calmar": round(cagr / (maxdd * 100), 2) if maxdd > 0 else None,
        "exposure": round(sum(exposure) / len(exposure) * 100), "deep": round(max(deep) * 100, 1),
        "trades": len(closed), "win": round(len(wins) / len(closed) * 100) if closed else 0,
        "pf": round(gp / gl, 2) if gl else None, "avg_days": round(sum(c["days"] for c in closed) / len(closed), 1) if closed else 0,
        "avg_ret": round(sum(c["ret"] for c in closed) / len(closed) * 100, 2) if closed else 0,
        "stops": sum(1 for c in closed if c["why"] == "止损"), "rot": sum(1 for c in closed if c["why"] == "换手"),
        "h1": round((eq[h - 1] / eq[0] - 1) * 100, 1), "h2": round((eq[-1] / eq[h] - 1) * 100, 1),
        "dd1": round(mdd(0, h), 1), "dd2": round(mdd(h, len(eq)), 1), "years": round(years, 2),
        "open_n": len(pos), "eq": eq,
    }


def buy_hold(cal, pre, syms, start=140, equity0=100000.0, per_year=252):
    n = len(cal)
    ok = [x for x in syms if x in pre and pre[x]["C"][start] is not None and pre[x]["O"][start + 1] is not None]
    w = equity0 / len(ok)
    sh = {x: w / pre[x]["O"][start + 1] for x in ok}
    last = {x: pre[x]["C"][start] for x in ok}
    eq = []
    for t in range(start, n):
        for x in ok:
            if pre[x]["C"][t] is not None:
                last[x] = pre[x]["C"][t]
        eq.append(sum(sh[x] * last[x] for x in ok))
    pk, m = eq[0], 0.0
    for v in eq:
        pk = max(pk, v)
        m = max(m, 1 - v / pk)
    years = (n - start) / per_year
    h = len(eq) // 2
    return {"ret": round((eq[-1] / equity0 - 1) * 100, 1), "cagr": round(((eq[-1] / equity0) ** (1 / years) - 1) * 100, 1),
            "maxdd": round(m * 100, 1), "h1": round((eq[h - 1] / eq[0] - 1) * 100, 1), "h2": round((eq[-1] / eq[h] - 1) * 100, 1), "eq": eq}
