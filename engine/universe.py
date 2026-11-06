# -*- coding: utf-8 -*-
"""全品种复盘(2026-10-18 宝贝：我看到的和你回测的必须是同一份东西)。
数据来源只有一个：optimal_run.py 生成的 optimal_data/optimal.json(主方案每一笔真实回测交易) + bars_<SYM>.json(日线+EMA8/30)。
本模块只做汇总，不另跑策略；唯一额外计算的是"单独只做这一只"的对照(同一套规则、整条腿资金只给它)，用来回答"只做MU够不够"。
输出：optimal_data/universe.json(总览) + optimal_data/u/<SYM>.json(单品种详情)。"""
from __future__ import annotations

import json
import math
import os
import time
from collections import defaultdict

from .optimal import FEE, M5, OUT, SECTORS, SEC_OF, STOP_ATR, VOL_TGT

DAY = 86400000
W_SUB = {"日线EMA8/30": 1 / 3, "8h超级趋势": 1 / 3, "8h布林突破": 1 / 3}   # 2026-10-19 改等权，跟 optimal.SUB_W 一致


def _load(name):
    with open(os.path.join(OUT, name), "r", encoding="utf-8") as f:
        return json.load(f)


def _atr(rows, n=14):
    out, a = [], None
    for i, r in enumerate(rows):
        pc = rows[i - 1][4] if i else r[4]
        tr = max(r[2] - r[3], abs(r[2] - pc), abs(r[3] - pc))
        a = tr if a is None else (a * (n - 1) + tr) / n
        out.append(a)
    return out


def _stats(curve):
    """curve: [[day, v], ...] → 年化/最大回撤/比值"""
    if len(curve) < 30:
        return None
    pk, dd = 0.0, 0.0
    for _, v in curve:
        pk = max(pk, v); dd = max(dd, 1 - v / pk if pk else 0)
    yrs = (curve[-1][0] - curve[0][0]) / 365.25
    if yrs <= 0.2 or curve[-1][1] <= 0:
        return None
    cagr = (curve[-1][1] / curve[0][1]) ** (1 / yrs) - 1
    return {"cagr": round(100 * cagr, 1), "max_dd": round(100 * dd, 1), "ratio": round(cagr / dd, 2) if dd else None,
            "mult": round(curve[-1][1] / curve[0][1], 2), "years": round(yrs, 1)}


def _years(curve):
    by = {}
    for d, v in curve:
        y = time.gmtime(d * 86400).tm_year
        by.setdefault(y, [v, v]); by[y][1] = v
    ys = sorted(by); out = {}
    for i, y in enumerate(ys):
        base = by[ys[i - 1]][1] if i else by[y][0]
        out[y] = round(100 * (by[y][1] / base - 1), 1)
    return out


def solo_stock(rows, months_sel=None, sector=None):
    """单独只做这一只美股：日线EMA8>EMA30做多、3×ATR止损(止损后要等均线翻空再翻多才重进)、仓位=min(1,3%÷ATR%)、次日开盘成交。
    months_sel 给了就只在该行业当月入选前5时持有(和主方案一样)。"""
    A = _atr(rows); eq = 1.0; q = 0.0; last = None; stop = None; pend = None; blocked = False
    curve, trades = [], []
    ent = None
    for i in range(1, len(rows)):
        t, o, h, l, c, e8, e30 = rows[i][:7]; d = t // DAY
        if pend == "in" and q == 0:
            alloc = min(1.0, VOL_TGT / (A[i - 1] / rows[i - 1][4])); q = eq * alloc / o; eq -= q * o * FEE; last = o
            stop = o - STOP_ATR * A[i - 1]; ent = (t, o)
        elif pend == "out" and q:
            eq += q * (o - last) - q * o * FEE; trades.append((ent[0], ent[1], t, o)); q = 0.0
        pend = None
        if q and l <= stop:
            px = min(o, stop); eq += q * (px - last) - q * px * FEE; trades.append((ent[0], ent[1], t, px)); q = 0.0; blocked = True
        elif q:
            eq += q * (c - last); last = c
        curve.append([d, eq])
        if i < 32:
            continue
        up = rows[i][5] > rows[i][6]
        ok = up
        if months_sel is not None:
            g = time.gmtime(t / 1000); ok = ok and sector in months_sel.get(f"{g.tm_year}-{g.tm_mon:02d}", ())
        if blocked and not up:
            blocked = False
        if blocked:
            ok = False
        if ok and not q:
            pend = "in"
        elif not ok and q:
            pend = "out"
    return curve, trades


def _thin(cv):
    """隔一个点取一个，保留最后一点；同一天只留一个(图表库要求时间严格递增)"""
    out = cv[::2]
    if cv and (not out or out[-1][0] != cv[-1][0]):
        out = out + cv[-1:]
    dd = []
    for x in out:
        if dd and dd[-1][0] == x[0]:
            dd[-1] = x
        else:
            dd.append(x)
    return dd


def _bh(rows, start_day):
    rs = [r for r in rows if r[0] // DAY >= start_day]
    return [[r[0] // DAY, r[4] / rs[0][4]] for r in rs] if rs else []


def build():
    opt = _load("optimal.json")
    try:
        rot = _load("rotation.json")["months"]
    except Exception:
        rot = {}
    cur_m = opt["stock"]["rotation"]["month"]; cur_sel = set(opt["stock"]["rotation"]["selected"])
    tradable = set(opt["stock"]["rotation"].get("active", [])) | {s for s, v in opt["stock"]["symbols"].items() if v.get("tradable")}
    trades_by = defaultdict(list)
    for t in opt["trades"]:
        trades_by[t["symbol"]].append(t)
    w_c, w_s = opt["plan"]["w_crypto"], opt["plan"]["w_stock"]
    os.makedirs(os.path.join(OUT, "u"), exist_ok=True)
    start_day = 17410
    syms = list(M5) + sorted(SEC_OF)
    out = []
    for sym in syms:
        p = os.path.join(OUT, f"bars_{sym}.json")
        if not os.path.exists(p):
            continue
        rows = _load(f"bars_{sym}.json")
        if len(rows) < 60:
            continue
        kind = "crypto" if sym in M5 else "stock"
        sector = "主流币" if kind == "crypto" else SEC_OF[sym]
        ts = sorted(trades_by.get(sym, []), key=lambda t: t["entry_t"])
        for t in ts:
            sub = t.get("sub") or "日线EMA8/30"
            t["sub"] = sub
            cw = (opt.get("crypto_weights") or {}).get(str(time.gmtime(t["entry_t"] / 1000).tm_year), {}).get(sym, 0.2)
            t["contrib"] = None if t.get("pnl_pct") is None else round(t["pnl_pct"] * (w_c * cw * W_SUB.get(sub, 1) if kind == "crypto" else w_s), 3)
        closed = [t for t in ts if not t.get("open")]
        wins = [t for t in closed if (t.get("ret") or 0) > 0]
        first_d = max(start_day, rows[0][0] // DAY)
        yrs_span = max(0.25, (rows[-1][0] // DAY - first_d) / 365.25)
        legs = {}
        for t in ts:
            L = legs.setdefault(t["sub"] if kind == "crypto" else "日线EMA8/30", {"n": 0, "w": 0, "contrib": 0.0})
            L["n"] += 1; L["w"] += (t.get("ret") or 0) > 0 and not t.get("open"); L["contrib"] += t["contrib"] or 0
        for L in legs.values():
            L["contrib"] = round(L["contrib"], 2)
        years = defaultdict(lambda: {"n": 0, "w": 0, "contrib": 0.0})
        for t in ts:
            if t.get("open"):
                continue                                  # 2026-11-06 逐年按平仓年份统计：持仓中的不算(原来算进笔数、拉低胜率)
            y = time.gmtime(t["exit_t"] / 1000).tm_year
            Y = years[y]; Y["n"] += 1; Y["w"] += (t.get("ret") or 0) > 0 and not t.get("open"); Y["contrib"] += t["contrib"] or 0
        # 单独只做这一只
        if kind == "stock":
            solo_cv, solo_tr = solo_stock(rows)
            solo_rot_cv, solo_rot_tr = solo_stock(rows, rot, sector)
        else:
            eq = 1.0; solo_cv = []; pts = sorted((t for t in ts if t["sub"] == "日线EMA8/30" and t.get("pnl_pct") is not None and not t.get("open")),
                                                   key=lambda t: t["exit_t"])
            k = 0
            for r in rows:
                d = r[0] // DAY
                while k < len(pts) and pts[k]["exit_t"] // DAY <= d:
                    eq *= 1 + pts[k]["pnl_pct"] / 100; k += 1
                solo_cv.append([d, eq])
            solo_tr = pts; solo_rot_cv = None; solo_rot_tr = []
        solo_cv = [x for x in solo_cv if x[0] >= first_d]
        if solo_cv and solo_cv[0][1]:
            b0 = solo_cv[0][1]; solo_cv = [[d, v / b0] for d, v in solo_cv]
        if solo_rot_cv:
            solo_rot_cv = [x for x in solo_rot_cv if x[0] >= first_d]
            if solo_rot_cv and solo_rot_cv[0][1]:
                b0 = solo_rot_cv[0][1]; solo_rot_cv = [[d, v / b0] for d, v in solo_rot_cv]
        bh = _bh(rows, first_d)
        last = rows[-1]; c30 = next((r[4] for r in reversed(rows) if r[0] <= last[0] - 30 * DAY), rows[0][4])
        c365 = next((r[4] for r in reversed(rows) if r[0] <= last[0] - 365 * DAY), rows[0][4])
        if kind == "crypto":
            st = opt["crypto"]["state"].get(sym, {})
            pos = st.get("position"); trend = st.get("trend"); in_rot = True
            pend = st.get("pending_reason")
        else:
            st = opt["stock"]["symbols"].get(sym, {})
            pos = next((x for x in opt["stock"]["positions"] if x["symbol"] == sym), None)
            if pos:
                pos = {**pos, "side": "LONG"}            # 2026-11-06 美股腿只做多；回测持仓记录没有 side，页面原来显示成"持空"
            trend = st.get("trend") or ("多头" if last[5] > last[6] else "空头"); in_rot = sector in cur_sel
            pend = next((x["reason"] for x in opt["stock"]["pending"] if x["symbol"] == sym), None)
        opens = [t for t in ts if t.get("open")]
        spark_rows = [r for r in rows if r[0] >= last[0] - 400 * DAY]
        step = max(1, len(spark_rows) // 160)
        spark = [[r[0], r[4]] for r in spark_rows[::step]]
        if spark and spark[-1][0] != last[0]:
            spark.append([last[0], last[4]])
        marks = [[t["entry_t"], 1 if t["side"] == "LONG" else -1, t["entry"], t["sub"][:2]] for t in ts if t["entry_t"] >= spark_rows[0][0]] + \
                [[t["exit_t"], 0, t["exit"], t["sub"][:2]] for t in ts if t.get("exit_t") and not t.get("open") and t["exit_t"] >= spark_rows[0][0]]
        rot_months = sorted(m for m, v in rot.items() if sector in v) if kind == "stock" else []
        summ = {
            "sym": sym, "kind": kind, "sector": sector, "tradable": kind == "crypto" or sym in tradable,
            "price": last[4], "t": last[0], "chg30": round(100 * (last[4] / c30 - 1), 1), "chg365": round(100 * (last[4] / c365 - 1), 1),
            "trend": trend, "in_rot": in_rot, "pos": pos or (opens[-1] and {"side": opens[-1]["side"], "entry": opens[-1]["entry"], "entry_t": opens[-1]["entry_t"],
                                                            "ret": opens[-1].get("ret"), "sub": opens[-1]["sub"]} if opens else None),
            "n_open": len(opens), "pending": pend,
            "n": len(ts), "n_year": round(len(ts) / yrs_span, 1), "win": round(100 * len(wins) / len(closed), 0) if closed else None,
            "avg_ret": round(sum(t.get("ret") or 0 for t in closed) / len(closed), 2) if closed else None,
            "contrib": round(sum(t["contrib"] or 0 for t in ts), 2),
            "best": max((t.get("ret") or 0 for t in closed), default=None), "worst": min((t.get("ret") or 0 for t in closed), default=None),
            "avg_days": round(sum(t.get("days") or 0 for t in closed) / len(closed), 1) if closed else None,
            "last": ts[-1] and {"entry_t": ts[-1]["entry_t"], "side": ts[-1]["side"], "ret": ts[-1].get("ret"), "open": bool(ts[-1].get("open")), "sub": ts[-1]["sub"]} if ts else None,
            "legs": legs, "years": {y: {"n": v["n"], "w": v["w"], "contrib": round(v["contrib"], 2)} for y, v in sorted(years.items())},
            "solo": _stats(solo_cv), "solo_years": _years(solo_cv), "solo_n": len(solo_tr),
            "solo_rot": _stats([x for x in solo_rot_cv if x[0] >= first_d]) if solo_rot_cv else None,
            "bh": _stats(bh), "bh_years": _years(bh), "first_day": first_d,
            "rot_share": round(100 * len([m for m in rot_months]) / max(1, len(rot)), 0) if kind == "stock" else None,
            "spark": spark, "marks": marks,
        }
        out.append(summ)
        detail = {**{k: v for k, v in summ.items() if k not in ("spark", "marks")}, "rows": rows, "trades": ts, "rot_months": rot_months,
                  "solo_curve": _thin(solo_cv), "bh_curve": _thin(bh), "solo_rot_curve": _thin(solo_rot_cv) if solo_rot_cv else None}
        with open(os.path.join(OUT, "u", f"{sym}.json.tmp"), "w", encoding="utf-8") as f:
            json.dump(detail, f, ensure_ascii=False)
        os.replace(os.path.join(OUT, "u", f"{sym}.json.tmp"), os.path.join(OUT, "u", f"{sym}.json"))
    try:
        research = _load("research.json")
    except Exception:
        research = []
    res = {"asof": opt["asof"], "plan": opt["series"]["F"], "series": {k: opt["series"][k] for k in ("F", "crypto", "stock")},
           "rotation": {"month": cur_m, "selected": sorted(cur_sel, key=lambda s: opt["stock"]["rotation"]["selected"].index(s))},
           "sectors": list(SECTORS), "symbols": out, "research": research,
           "n_trades": sum(s["n"] for s in out)}
    with open(os.path.join(OUT, "universe.json.tmp"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False)
    os.replace(os.path.join(OUT, "universe.json.tmp"), os.path.join(OUT, "universe.json"))
    return res


if __name__ == "__main__":
    t0 = time.time(); r = build()
    print(f"universe: {len(r['symbols'])} 个品种, {r['n_trades']} 笔, {time.time() - t0:.1f}s")
