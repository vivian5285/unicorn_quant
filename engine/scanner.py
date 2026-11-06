# -*- coding: utf-8 -*-
"""美股趋势突破扫描器 + 纸面跟踪账户。

规则来自 2026-10-07 的美股研究(日线触发 + 周线大级别，51只、9.4年、1296组合)里数据支持的结构：
  · 周线多头过滤：周线 EMA10 > EMA30 且收盘 > EMA30
  · 日线均线多头：EMA7 > EMA25 且收盘 > EMA25
  · 入场：收盘突破"上方最近压力位"(周线分形高低点聚类)，且上一根收盘还在压力位下方
  · 出场：EMA7 跌破 EMA25(趋势反转)；硬止损：入场价 -10%
  · 仓位：10个等权槽位、只做多、不换手；候选按63日相对强度(ROC)排序
研究数字(样本内)：年化约23.7%、最大回撤约18.9%，胜率约41%、PF约2.4。幸存者偏差、不含税/分红/滑点，仅供参考。
2026-10-16 复核(/root/research/uq_*_20261016.py)：同一批52只等权买入持有年化23.8%/回撤38.4%，
  说明23.7%几乎全是选股池的幸存者偏差，择时本身不增加收益；真正的价值是回撤减半(2022年 -11.6% vs 持有 -35%)。
  样本外40只(研究没用过的股票)：策略年化12.2%/回撤21.8%，池持有11.6%/44.4%，SPY 13.2%/34.1%。
  现实预期 ≈ 跟大盘差不多的收益、约2/3的回撤。加"SPY>EMA50/100/200才开仓"的大盘过滤全部更差，否决。
"""
from __future__ import annotations

import json
import os
import time
from typing import Dict, List, Optional

from . import stocks, ta, trendrot as tr

HERE = os.path.dirname(os.path.abspath(__file__))
DIR = os.environ.get("UQ_SCANNER_DIR") or os.path.join(HERE, "..", "scanner_data")

SPEC = {"pair": (7, 25), "mode": "breakout", "tri": False, "htf_on": True, "room": 0.08, "sr": "fractal",
        "stop_pct": 0.10, "slots": 10, "recent_days": 5, "fee": 0.0005, "equity0": 100000.0}
CFG = dict(htf="1w", htf_fast=10, htf_slow=30, pivot_k=2, pivot_L=104, rs_n=63)


def _r(x, d=2):
    return None if x is None else round(float(x), d)


def scan_symbol(sym: str) -> Optional[dict]:
    base = stocks.get_klines(sym, "1d", 1200, closed_only=True)
    htf = stocks.get_klines(sym, "1w", 600, closed_only=True)
    if len(base) < 260 or len(htf) < 80:
        return None
    cal = [b["t"] for b in base]
    pre = {sym: tr.prep_symbol(base, htf, cal, CFG)}
    f, s = SPEC["pair"]
    ent = tr.entries(pre, sym, SPEC["pair"], SPEC["mode"], SPEC["tri"], SPEC["htf_on"], SPEC["room"], SPEC["sr"])
    d = pre[sym]
    t = len(base) - 1
    c = base[t]["c"]
    ef, es = d["E"][f][t], d["E"][s][t]
    if ef is None or es is None:
        return None
    closes = [b["c"] for b in base]
    e20, e100 = ta.ema(closes, 20)[-1], ta.ema(closes, 100)[-1]
    hh55 = max(b["h"] for b in base[-56:-1])
    atr = ta.atr(base, 14)[-1] or 0.0
    res, sup = d["RES"][t], d["SUP"][t]
    days_since = None
    for k in range(0, SPEC["recent_days"] + 1):
        if t - k >= 0 and ent[t - k]:
            days_since = k
            break
    trend_up = ef > es and c > es
    sup_stop = None
    if sup:
        dist = min(max(1 - sup * 0.99 / c, 0.05), 0.15)
        sup_stop = c * (1 - dist)
    if days_since == 0:
        status = "新突破"
    elif days_since is not None and trend_up:
        status = f"近期突破({days_since}日前)"
    elif trend_up and d["UP"][t]:
        status = "多头持有区"
    elif trend_up:
        status = "日线多头(周线未确认)"
    elif ef > es:
        status = "回踩/整理"
    else:
        status = "空头/观望"
    m = stocks.meta(sym)
    prev_close = base[t - 1]["c"]
    return {
        "sym": sym, "name": m["name"], "sector": m["sector"], "date": time.strftime("%Y-%m-%d", time.gmtime(base[t]["t"] / 1000)),
        "close": _r(c), "chg": _r((c / prev_close - 1) * 100), "ema_fast": _r(ef), "ema_slow": _r(es),
        "trend_up": trend_up, "htf_up": bool(d["UP"][t]), "rs": _r(d["RS"][t] * 100 if d["RS"][t] is not None else None, 1),
        "res": _r(res), "res_gap": _r((res / c - 1) * 100, 1) if res else None, "sup": _r(sup), "sup_gap": _r((1 - sup / c) * 100, 1) if sup else None,
        "broken": _r(d["RES"][t - 1]) if days_since == 0 else None,
        "ext": _r((c / es - 1) * 100, 1), "atr_pct": _r(atr / c * 100, 1),
        "stop_fixed": _r(c * (1 - SPEC["stop_pct"])), "stop_struct": _r(sup_stop),
        "don55": bool(c > hh55 and e20 is not None and e100 is not None and e20 > e100 and c > e20),
        "signal": days_since == 0, "days_since": days_since, "status": status, "open": _r(base[t]["o"]), "t": base[t]["t"],
    }


def run_scan(symbols: Optional[List[str]] = None) -> dict:
    rows, errs = [], {}
    for sym in (symbols or stocks.WATCH):
        try:
            r = scan_symbol(sym)
            if r:
                rows.append(r)
            else:
                errs[sym] = "数据不足"
        except Exception as e:
            errs[sym] = str(e)[:60]
    rs = sorted(r["rs"] for r in rows if r["rs"] is not None)
    for r in rows:
        r["rs_pct"] = round(100 * sum(1 for x in rs if x <= r["rs"]) / len(rs)) if (rs and r["rs"] is not None) else None
    rows.sort(key=lambda r: (not r["signal"], -(r["rs"] if r["rs"] is not None else -999)))
    return {"updated": int(time.time()), "bar_date": max((r["date"] for r in rows), default=None), "spec": {**SPEC, "pair": list(SPEC["pair"])},
            "rows": rows, "errors": errs}


# ---------------------------------------------------------------- 纸面跟踪
def _state_path():
    os.makedirs(DIR, exist_ok=True)
    return os.path.join(DIR, "paper_state.json")


def load_paper() -> dict:
    try:
        return json.load(open(_state_path(), encoding="utf-8"))
    except Exception:
        return {"cash": SPEC["equity0"], "pos": {}, "pend_buy": [], "pend_sell": {}, "hist": [], "curve": [], "last_date": None,
                "start": time.strftime("%Y-%m-%d")}


def reset_paper():
    try:
        os.remove(_state_path())
    except OSError:
        pass


def update_paper(scan: dict) -> dict:
    """每个新的日线交易日处理一次：①按当日开盘价执行昨日挂单 ②按收盘价估值 ③决定明日卖出 ④新信号→明日买入"""
    st = load_paper()
    rows = {r["sym"]: r for r in scan["rows"]}
    day = scan["bar_date"]
    if not day or st["last_date"] == day:
        return st
    fee, slots = SPEC["fee"], SPEC["slots"]
    eq_prev = st["curve"][-1]["eq"] if st["curve"] else SPEC["equity0"]
    # ① 执行挂单(价格=今天这根日线的开盘价)
    for sym, why in list(st["pend_sell"].items()):
        r, p = rows.get(sym), st["pos"].get(sym)
        if r and p:
            px = r["open"]
            st["cash"] += p["sh"] * px * (1 - fee)
            st["hist"].append({"sym": sym, "entry": p["entry"], "exit": px, "ret": round((px * (1 - fee) / p["entry"] - 1) * 100, 2),
                               "why": why, "t0": p["date"], "t1": day})
            del st["pos"][sym]
        st["pend_sell"].pop(sym, None)
    for sym in st["pend_buy"]:
        r = rows.get(sym)
        if not r or sym in st["pos"]:
            continue
        amt = min(st["cash"], eq_prev / slots)
        if amt < eq_prev / slots * 0.3:
            continue
        px = r["open"]
        sh = amt * (1 - fee) / px
        st["cash"] -= amt
        st["pos"][sym] = {"sh": sh, "entry": round(amt / sh, 4), "stop": round(amt / sh * (1 - SPEC["stop_pct"]), 4), "date": day}
    st["pend_buy"] = []
    # ② 估值
    val = sum(p["sh"] * rows[s]["close"] for s, p in st["pos"].items() if s in rows)
    eq = st["cash"] + val
    st["curve"].append({"date": day, "eq": round(eq, 2)})
    # ③ 明日卖出：趋势反转(快线<慢线) 或 跌破硬止损
    for sym, p in st["pos"].items():
        r = rows.get(sym)
        if not r:
            continue
        if r["ema_fast"] < r["ema_slow"]:
            st["pend_sell"][sym] = "趋势反转(EMA7<EMA25)"
        elif r["close"] <= p["stop"]:
            st["pend_sell"][sym] = "硬止损(-10%)"
    # ④ 新信号 → 明日买入(按相对强度，不超过空槽)
    free = slots - (len(st["pos"]) - len(st["pend_sell"]))
    cands = sorted([r for r in rows.values() if r["signal"] and r["sym"] not in st["pos"]], key=lambda r: -(r["rs"] if r["rs"] is not None else -999))
    st["pend_buy"] = [r["sym"] for r in cands[:max(free, 0)]]
    st["last_date"] = day
    json.dump(st, open(_state_path(), "w", encoding="utf-8"), ensure_ascii=False)
    return st


def paper_view(scan: Optional[dict]) -> dict:
    st = load_paper()
    rows = {r["sym"]: r for r in (scan or {}).get("rows", [])}
    pos = []
    for s, p in st["pos"].items():
        c = rows.get(s, {}).get("close")
        pos.append({"sym": s, "name": rows.get(s, {}).get("name", ""), "entry": p["entry"], "stop": p["stop"], "date": p["date"], "close": c,
                    "pnl": round((c / p["entry"] - 1) * 100, 2) if c else None, "value": round(p["sh"] * c, 2) if c else None,
                    "exit_flag": st["pend_sell"].get(s)})
    eq = st["curve"][-1]["eq"] if st["curve"] else SPEC["equity0"]
    return {"equity": eq, "equity0": SPEC["equity0"], "cash": round(st["cash"], 2), "start": st.get("start"), "last_date": st["last_date"],
            "positions": pos, "pend_buy": st["pend_buy"], "pend_sell": st["pend_sell"], "hist": st["hist"][-60:][::-1], "curve": st["curve"][-250:]}
