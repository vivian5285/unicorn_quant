# -*- coding: utf-8 -*-
"""唐奇安 + 双均线趋势过滤 + ATR止损 + 吊灯移动止盈 的参数研究（币圈/黄金/美股分别找参数）。

用法: python donchian_study.py SET   SET ∈ crypto4h crypto1d gold4h gold1d stock1d tradfi4h
快速回测器的成交口径与 engine.backtest 完全一致：信号K线收盘价成交、硬止损按K线高低触发、单边0.05%手续费。
方法论（防过拟合）：
  1) 先网格：EMA对 × 突破周期 × 反向通道周期 × ADX门槛 × 止损ATR × 吊灯ATR（1728组）
  2) 按"资产组中位卡玛"打分，并对相邻参数取平均(高原分)，选"一片都好"的参数而不是尖峰
  3) 只用前半段选参数，看后半段表现(样本外)；与宝贝原始设定(50/200,20,10,ADX25,2ATR,3ATR)对照
  4) 再在入选参数上叠加开关：EMA离场、放量、布林过滤、做空
"""
import itertools
import json
import math
import os
import statistics as st
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
if hasattr(os, "nice"):
    try:
        os.nice(15)
    except OSError:
        pass
from engine import data  # noqa: E402

FEE = 0.0005
SETS = {
    # 名称: (周期, 每年K线数, [(符号, 是否主调)])
    "crypto4h": ("4h", 2190, [("ZECUSDT", 1), ("ETHUSDT", 1), ("BTCUSDT", 0), ("SOLUSDT", 0), ("BNBUSDT", 0), ("XRPUSDT", 0),
                              ("LINKUSDT", 0), ("DOGEUSDT", 0), ("XMRUSDT", 0)]),
    "crypto1d": ("1d", 365, [("ZEC-USD", 1), ("ETH-USD", 1), ("BTC-USD", 0), ("SOL-USD", 0), ("BNB-USD", 0), ("XRP-USD", 0),
                             ("LINK-USD", 0), ("DOGE-USD", 0), ("XMR-USD", 0)]),
    "gold4h": ("4h", 2190, [("XAUUSDT", 1), ("PAXGUSDT", 0)]),
    "gold1d": ("1d", 252, [("GC=F", 1), ("GLD", 0)]),
    "stock1d": ("1d", 252, [("SNDK", 1), ("NVDA", 0), ("AAPL", 0), ("MSFT", 0), ("TSLA", 0), ("AMD", 0), ("MU", 0), ("AVGO", 0),
                            ("META", 0), ("GOOGL", 0), ("AMZN", 0), ("PLTR", 0), ("COIN", 0), ("ASML", 0), ("TSM", 0)]),
    "tradfi4h": ("4h", 2190, [("SNDKUSDT", 1), ("MUUSDT", 0), ("NVDAUSDT", 0), ("TSLAUSDT", 0), ("AMDUSDT", 0), ("ASMLUSDT", 0)]),
}
EMA_PAIRS = [(20, 100), (30, 120), (50, 200)]
DON_N = [10, 20, 30, 55]
EXIT_N = [5, 10, 20]
ADX_MIN = [0, 20, 25, 30]
STOP_ATR = [1.5, 2.0, 3.0]
TRAIL_ATR = [2.0, 3.0, 4.0, 5.0]
DEFAULT = dict(pair=(50, 200), don=20, exit=10, adx=25, stop=2.0, trail=3.0, ema_exit=False, vol=0.0, bb=False, short=False)


# ---------------- 指标 ----------------
def ema_s(v, n):
    out = [None] * len(v)
    if len(v) < n:
        return out
    k = 2.0 / (n + 1)
    p = sum(v[:n]) / n
    out[n - 1] = p
    for i in range(n, len(v)):
        p = v[i] * k + p * (1 - k)
        out[i] = p
    return out


def wilder_atr_s(h, l, c, n=14):
    out = [None] * len(c)
    trs = [h[0] - l[0]] + [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) for i in range(1, len(c))]
    if len(c) <= n:
        return out
    a = sum(trs[1:n + 1]) / n
    out[n] = a
    for i in range(n + 1, len(c)):
        a = (a * (n - 1) + trs[i]) / n
        out[i] = a
    return out


def adx_s(h, l, c, n=14):
    """Wilder ADX 序列，口径同 indicators.wilder_adx"""
    N = len(c)
    out = [None] * N
    if N < 2 * n + 2:
        return out
    pdm, mdm, trs = [], [], []
    for i in range(1, N):
        up, dn = h[i] - h[i - 1], l[i - 1] - l[i]
        pdm.append(up if up > dn and up > 0 else 0.0)
        mdm.append(dn if dn > up and dn > 0 else 0.0)
        trs.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])))
    str_, sp, sm = sum(trs[:n]), sum(pdm[:n]), sum(mdm[:n])
    dxs = []

    def dx(sp_, sm_, st_):
        if st_ <= 0:
            return 0.0
        pdi, mdi = 100 * sp_ / st_, 100 * sm_ / st_
        d = pdi + mdi
        return 100 * abs(pdi - mdi) / d if d > 0 else 0.0

    dxs.append(dx(sp, sm, str_))
    idx_of_dx = [n]  # dx[k] 对应 bars 下标 n+k  (trs下标t对应bar t+1)
    for t in range(n, len(trs)):
        str_ = str_ - str_ / n + trs[t]
        sp = sp - sp / n + pdm[t]
        sm = sm - sm / n + mdm[t]
        dxs.append(dx(sp, sm, str_))
    # dxs[k] 对应 bar 下标 n+k
    adx = sum(dxs[:n]) / n
    out[n + n - 1] = adx
    for k in range(n, len(dxs)):
        adx = (adx * (n - 1) + dxs[k]) / n
        out[n + k] = adx
    return out


def roll_ext(arr, n, fn):
    """不含当前根的过去n根 max/min"""
    out = [None] * len(arr)
    for i in range(n, len(arr)):
        out[i] = fn(arr[i - n:i])
    return out


def prep(bars):
    c = [b["c"] for b in bars]; h = [b["h"] for b in bars]; l = [b["l"] for b in bars]; v = [b["v"] for b in bars]
    a = {"c": c, "h": h, "l": l, "v": v, "n": len(c)}
    a["ema"] = {n: ema_s(c, n) for n in (20, 30, 50, 100, 120, 200)}
    a["atr"] = wilder_atr_s(h, l, c, 14)
    a["adx"] = adx_s(h, l, c, 14)
    a["dh"] = {n: roll_ext(h, n, max) for n in (5, 10, 20, 30, 55)}
    a["dl"] = {n: roll_ext(l, n, min) for n in (5, 10, 20, 30, 55)}
    # 量均线(不含当前)
    vm = [None] * len(v)
    for i in range(20, len(v)):
        vm[i] = sum(v[i - 20:i]) / 20
    a["vma"] = vm
    # 布林
    mid = [None] * len(c); bw = [None] * len(c)
    for i in range(19, len(c)):
        w = c[i - 19:i + 1]
        m = sum(w) / 20
        sd = math.sqrt(sum((x - m) ** 2 for x in w) / 20)
        mid[i] = m
        bw[i] = 4 * sd / m if m else None
    a["mid"], a["bw"] = mid, bw
    return a


# ---------------- 快速回测 ----------------
def sim(a, cfg, per_year, lo=0, hi=None):
    c, h, l = a["c"], a["h"], a["l"]
    n = a["n"] if hi is None else hi
    ef, es = a["ema"][cfg["pair"][0]], a["ema"][cfg["pair"][1]]
    atr, adx = a["atr"], a["adx"]
    dh, dl = a["dh"][cfg["don"]], a["dl"][cfg["don"]]
    xh, xl = a["dh"][cfg["exit"]], a["dl"][cfg["exit"]]
    stop_m, trail_m, adx_min = cfg["stop"], cfg["trail"], cfg["adx"]
    ema_exit, vol_m, bbf, shorts = cfg["ema_exit"], cfg["vol"], cfg["bb"], cfg["short"]
    start = max(lo, cfg["pair"][1] + 5, 60)
    pos = 0
    entry = stop = ext = 0.0
    eq = 1.0
    trades = []
    curve = []
    in_bars = 0
    for i in range(start, n):
        px = c[i]
        if pos:
            in_bars += 1
            x = None
            if pos == 1 and l[i] <= stop:
                x = stop
            elif pos == -1 and h[i] >= stop:
                x = stop
            if x is not None:
                r = pos * (x / entry - 1) - FEE * (1 + x / entry)
                eq *= 1 + r
                trades.append(r)
                pos = 0
        if pos:
            if pos == 1:
                ext = max(ext, h[i])
                lvl = ext - trail_m * atr[i]
                if px < lvl or (xl[i] is not None and px < xl[i]) or (ema_exit and px < ef[i]):
                    r = (px / entry - 1) - FEE * (1 + px / entry)
                    eq *= 1 + r
                    trades.append(r)
                    pos = 0
            else:
                ext = min(ext, l[i])
                lvl = ext + trail_m * atr[i]
                if px > lvl or (xh[i] is not None and px > xh[i]) or (ema_exit and px > ef[i]):
                    r = -(px / entry - 1) - FEE * (1 + px / entry)
                    eq *= 1 + r
                    trades.append(r)
                    pos = 0
        elif ef[i] is not None and es[i] is not None and atr[i] and dh[i] is not None:
            side = 0
            if ef[i] > es[i] and px > ef[i] and px > dh[i]:
                side = 1
            elif shorts and ef[i] < es[i] and px < ef[i] and px < dl[i]:
                side = -1
            if side:
                ok = True
                if adx_min > 0 and (adx[i] is None or adx[i] < adx_min):
                    ok = False
                if ok and vol_m > 0 and (a["vma"][i] is None or a["v"][i] < vol_m * a["vma"][i]):
                    ok = False
                if ok and bbf:
                    m0, m1, b0, b3 = a["mid"][i], a["mid"][i - 1], a["bw"][i], a["bw"][i - 3]
                    if None in (m0, m1, b0, b3) or not ((m0 > m1 if side == 1 else m0 < m1) and b0 > b3):
                        ok = False
                if ok:
                    pos, entry = side, px
                    stop = px - side * atr[i] * stop_m
                    ext = h[i] if side == 1 else l[i]
        mark = eq * (1 + pos * (px / entry - 1)) if pos else eq
        curve.append(mark)
    if pos:  # 期末按市价估值，不计入成交
        pass
    if len(curve) < 30:
        return None
    years = len(curve) / per_year
    pk, mdd = curve[0], 0.0
    for v in curve:
        pk = max(pk, v)
        mdd = max(mdd, 1 - v / pk)
    final = curve[-1]
    cagr = (final ** (1 / years) - 1) * 100 if final > 0 else -100
    gp = sum(r for r in trades if r > 0)
    gl = -sum(r for r in trades if r <= 0)
    m = len(curve) // 2
    return {"ret": (final - 1) * 100, "cagr": cagr, "dd": mdd * 100, "calmar": cagr / (mdd * 100) if mdd > 0.001 else 0.0,
            "n": len(trades), "win": 100 * sum(r > 0 for r in trades) / len(trades) if trades else 0,
            "pf": gp / gl if gl > 0 else (9.9 if gp > 0 else 0.0), "exp": in_bars / max(len(curve), 1) * 100,
            "h1": (curve[m - 1] / curve[0] - 1) * 100, "h2": (curve[-1] / curve[m] - 1) * 100,
            "dd1": _mdd(curve[:m]), "dd2": _mdd(curve[m:]), "years": years}


def _mdd(cv):
    pk, m = cv[0], 0.0
    for v in cv:
        pk = max(pk, v)
        m = max(m, 1 - v / pk)
    return m * 100


def cal_half(r, k):  # 单半段卡玛：半段收益年化/半段回撤
    ret = r["h%d" % k]
    dd = r["dd%d" % k]
    yrs = r["years"] / 2
    ann = ((1 + ret / 100) ** (1 / yrs) - 1) * 100 if ret > -99 else -100
    return ann / dd if dd > 0.5 else 0.0


# ---------------- 主流程 ----------------
def main():
    name = sys.argv[1]
    itv, per_year, assets = SETS[name]
    t0 = time.time()
    A, info = {}, []
    for sym, prim in assets:
        try:
            lim = 20000 if itv == "4h" else 3000
            bars = data.get_klines(sym, itv, lim, closed_only=True)
            if len(bars) < 300:
                info.append(f"{sym}:仅{len(bars)}根(跳过)")
                continue
            A[sym] = prep(bars)
            A[sym]["prim"] = prim
            t_a = time.strftime("%Y-%m-%d", time.gmtime(bars[0]["t"] / 1000))
            A[sym]["bh"] = (bars[-1]["c"] / bars[min(205, len(bars) - 1)]["c"] - 1) * 100
            info.append(f"{sym}:{len(bars)}根 {t_a}~")
        except Exception as e:
            info.append(f"{sym}:失败 {str(e)[:40]}")
    print(f"[{name} {itv}] 数据 {', '.join(info)}  ({time.time()-t0:.0f}s)")
    syms = list(A)
    if len(syms) < 1:
        return

    def run_all(cfg):
        return {s: sim(A[s], cfg, per_year) for s in syms}

    def score(res, key="calmar"):
        v = [(r[key] if r and r["n"] >= 8 else 0.0) for r in res.values()]
        return st.median(v)

    # ---- 阶段1：网格 ----
    grid = list(itertools.product(EMA_PAIRS, DON_N, EXIT_N, ADX_MIN, STOP_ATR, TRAIL_ATR))
    R = {}
    for pr, dn, xn, ax, sp, tr in grid:
        cfg = dict(DEFAULT, pair=pr, don=dn, exit=xn, adx=ax, stop=sp, trail=tr)
        R[(pr, dn, xn, ax, sp, tr)] = run_all(cfg)
    print(f"  网格 {len(grid)} 组 × {len(syms)} 资产 完成 {time.time()-t0:.0f}s")

    keys = list(R)
    axes = [EMA_PAIRS, DON_N, EXIT_N, ADX_MIN, STOP_ATR, TRAIL_ATR]

    def neigh(k):
        out = [k]
        for ax_i, ax in enumerate(axes):
            j = ax.index(k[ax_i])
            for d in (-1, 1):
                if 0 <= j + d < len(ax):
                    kk = list(k); kk[ax_i] = ax[j + d]; out.append(tuple(kk))
        return out

    base = {k: score(R[k]) for k in keys}
    plateau = {k: st.mean(base[x] for x in neigh(k)) for k in keys}
    # 前半段选、后半段验
    def half_score(res, hk):
        return st.median([(cal_half(r, hk) if r and r["n"] >= 4 else 0.0) for r in res.values()])
    h1s = {k: half_score(R[k], 1) for k in keys}
    h1p = {k: st.mean(h1s[x] for x in neigh(k)) for k in keys}

    def line_cfg(k):
        return f"EMA{k[0][0]}/{k[0][1]} 突破{k[1]} 反向{k[2]} ADX{k[3]} 止损{k[4]}ATR 吊灯{k[5]}ATR"

    def show(k, title):
        res = R[k]
        print(f"  {title}: {line_cfg(k)}  | 组中位卡玛 {base[k]:.2f} 高原分 {plateau[k]:.2f}")
        for s in syms:
            r = res[s]
            if not r:
                continue
            print(f"      {s:9s}{'★' if A[s]['prim'] else ' '} 总{r['ret']:8.1f}% 年化{r['cagr']:6.1f}% 回撤{r['dd']:5.1f}% 卡玛{r['calmar']:5.2f} 笔{r['n']:4d} 胜{r['win']:3.0f}% PF{r['pf']:4.2f} 仓位{r['exp']:3.0f}% | 前半{r['h1']:7.1f}% 后半{r['h2']:7.1f}% | 买入持有{A[s]['bh']:8.1f}%")

    dk = (DEFAULT["pair"], DEFAULT["don"], DEFAULT["exit"], DEFAULT["adx"], DEFAULT["stop"], DEFAULT["trail"])
    show(dk, "【宝贝原始设定】")
    best_p = max(keys, key=lambda k: plateau[k])
    show(best_p, "【高原分最优(全样本)】")
    best_h1 = max(keys, key=lambda k: h1p[k])
    print(f"  【仅用前半段选参】{line_cfg(best_h1)}  前半高原卡玛 {h1p[best_h1]:.2f}")
    r2 = [(s, R[best_h1][s]) for s in syms if R[best_h1][s]]
    print("      → 后半段(样本外)：" + "；".join(f"{s} {r['h2']:.0f}%(撤{r['dd2']:.0f}%)" for s, r in r2))
    print("      → 同期原始设定后半段：" + "；".join(f"{s} {R[dk][s]['h2']:.0f}%" for s in syms if R[dk][s]))

    # ---- 边际分析 ----
    print("  【参数边际：固定该参数取值，对其余参数取平均的组中位卡玛 / 平均回撤】")
    names = ["EMA对", "突破周期", "反向通道", "ADX门槛", "止损ATR", "吊灯ATR"]
    for ai, (nm, ax) in enumerate(zip(names, axes)):
        parts = []
        for val in ax:
            ks = [k for k in keys if k[ai] == val]
            cm = st.mean(base[k] for k in ks)
            dd = st.mean(st.mean(r["dd"] for r in R[k].values() if r) for k in ks)
            ret = st.mean(st.mean(r["cagr"] for r in R[k].values() if r) for k in ks)
            parts.append(f"{val}:卡{cm:.2f}/年化{ret:.0f}%/撤{dd:.0f}%")
        print(f"      {nm}: " + "  ".join(parts))

    # ---- 阶段2：开关 ----
    top = sorted(keys, key=lambda k: -plateau[k])[:10]
    sw = []
    for k in top:
        for ee, vol, bb, sh in itertools.product([False, True], [0.0, 1.2], [False, True], [False, True]):
            cfg = dict(DEFAULT, pair=k[0], don=k[1], exit=k[2], adx=k[3], stop=k[4], trail=k[5], ema_exit=ee, vol=vol, bb=bb, short=sh)
            res = run_all(cfg)
            sw.append((score(res), k, ee, vol, bb, sh, res))
    print("  【开关消融：对 Top10 高原参数分别开关，平均组中位卡玛 / 平均回撤】")
    for nm, ix in (("EMA离场", 2), ("放量1.2x", 3), ("布林过滤", 4), ("允许做空", 5)):
        for val in (False, True) if ix != 3 else (0.0, 1.2):
            rs = [x for x in sw if x[ix] == val]
            print(f"      {nm}={val}: 卡玛 {st.mean(x[0] for x in rs):.2f}  年化 {st.mean(st.mean(r['cagr'] for r in x[6].values() if r) for x in rs):.0f}%  回撤 {st.mean(st.mean(r['dd'] for r in x[6].values() if r) for x in rs):.0f}%  笔数 {st.mean(st.mean(r['n'] for r in x[6].values() if r) for x in rs):.0f}")
    bestsw = max(sw, key=lambda x: x[0])
    print(f"  【含开关最优】{line_cfg(bestsw[1])} EMA离场={bestsw[2]} 放量={bestsw[3]} 布林={bestsw[4]} 做空={bestsw[5]} 组中位卡玛 {bestsw[0]:.2f}")
    for s in syms:
        r = bestsw[6][s]
        if r:
            print(f"      {s:9s}{'★' if A[s]['prim'] else ' '} 总{r['ret']:8.1f}% 年化{r['cagr']:6.1f}% 回撤{r['dd']:5.1f}% 卡玛{r['calmar']:5.2f} 笔{r['n']:4d} 胜{r['win']:3.0f}% | 前半{r['h1']:7.1f}% 后半{r['h2']:7.1f}%")
    # ---- 主调资产各自最优(过拟合风险) vs 组参数 ----
    print("  【主调资产：自己单独最优 vs 用组高原参数（差距=过拟合空间）】")
    for s in syms:
        if not A[s]["prim"]:
            continue
        own = max(keys, key=lambda k: (R[k][s]["calmar"] if R[k][s] and R[k][s]["n"] >= 8 else -9))
        ro, rg = R[own][s], R[best_p][s]
        print(f"      {s}: 自选 {line_cfg(own)} → 卡玛{ro['calmar']:.2f} 年化{ro['cagr']:.0f}% 撤{ro['dd']:.0f}% 前半{ro['h1']:.0f}%/后半{ro['h2']:.0f}%；组参数 → 卡玛{rg['calmar']:.2f} 年化{rg['cagr']:.0f}% 撤{rg['dd']:.0f}% 前半{rg['h1']:.0f}%/后半{rg['h2']:.0f}%")
    out = os.path.join(HERE, "sweeps", f"donchian_{name}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump({"set": name, "best_plateau": list(map(str, best_p)), "best_switch": [str(x) for x in bestsw[1:6]],
               "plateau_top": [[str(k), round(plateau[k], 3)] for k in sorted(keys, key=lambda k: -plateau[k])[:15]]},
              open(out, "w"), ensure_ascii=False)
    print(f"  总耗时 {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
