# -*- coding: utf-8 -*-
"""最优方案复盘(2026-10-18)：方案F = 币圈腿 0.75 + 美股腿 0.75(总名义≤1.5倍)，每天按权重再平衡。

币圈腿：主流5个(BTC/ETH/BNB/SOL/XRP)，币安现货日线，EMA8/30 多空，按状态持仓，收盘出信号、次日开盘成交；
        每个币分 1/5 资金、各自复利，名义 = 该币资金 × min(1, 3% ÷ 日ATR%)；
        山寨币只在 BTC 日线 EMA8/30 同方向时开新仓；BTC 减半周期：减半后0–18个月只做多、18–30个月只做空、其余都做。
美股腿：13个行业78只正股(Yahoo日线)按"上月末 ÷ 6个月前月末"算行业等权涨幅，每月取前5个行业；
        只在这些行业里、且币安有美股永续的股票上做多；正股 EMA8>EMA30 持有，EMA8<EMA30 或行业掉出前5 离场，3×ATR 硬止损；
        共用一个账户复利，单只名义 = 净值 × min(1, 3% ÷ 日ATR%) ÷ max(当月可交易只数, 5)，总名义≤1倍。
两腿每边手续费 0.08%，不含资金费与滑点。逻辑与研究脚本一致：
  /root/research/crypto_dd_optimization_20261016.py、harness/combo2026.py、rot_tradable.py、lev_combo.py
每笔交易都记录开仓/平仓原因，供页面复盘。由 optimal_run.py(cron 每天美股收盘后)生成 optimal_data/optimal.json。
"""
from __future__ import annotations

import bisect
import calendar
import collections
import json
import math
import os
import time
import urllib.request
from typing import Dict, List

from . import bottom, stocks

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, "optimal_data")
FEE = 0.0008
DAY = 86400000
W_CRYPTO, W_STOCK = 1.0, 1.0       # 2026-10-17 宝贝定案：币圈1倍+美股1倍(总2倍)+组合波动率目标40%；原1.5倍方案F留作对比
START_DAY = 17410            # 2017-09-01，五个币里 BTC/ETH 数据齐的起点
M5 = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]
VOL_TGT = 0.03
HALVINGS = [calendar.timegm((2016, 7, 9, 0, 0, 0)), calendar.timegm((2020, 5, 11, 0, 0, 0)), calendar.timegm((2024, 4, 19, 0, 0, 0))]
SECTORS: Dict[str, List[str]] = {
    "存储": ["MU", "WDC", "STX", "SNDK", "NTAP"],
    "半导体": ["NVDA", "AMD", "AVGO", "QCOM", "TXN", "ADI", "MRVL", "INTC", "ARM", "TSM"],
    "半导体设备": ["AMAT", "LRCX", "KLAC", "TER", "ASML"],
    "AI算力/网络": ["SMCI", "DELL", "HPE", "ANET", "CIEN", "GLW", "VRT", "CSCO", "COHR"],
    "软件": ["MSFT", "ORCL", "ADBE", "CRM", "NOW", "INTU"],
    "互联网": ["GOOGL", "META", "AMZN", "NFLX", "UBER"],
    "金融": ["JPM", "BAC", "GS", "MS", "WFC"],
    "能源": ["XOM", "CVX", "COP", "SLB", "OXY"],
    "医药": ["JNJ", "UNH", "PFE", "MRK", "LLY", "ABBV"],
    "消费": ["WMT", "COST", "HD", "MCD", "KO", "PG", "NKE"],
    "工业军工": ["CAT", "DE", "GE", "HON", "LMT", "RTX", "BA"],
    "材料矿业": ["FCX", "NEM", "NUE", "AA"],
    "加密相关": ["COIN", "MSTR", "MARA", "HOOD"],
}
SEC_OF = {t: k for k, v in SECTORS.items() for t in v}
LOOKBACK_M, TOP_K, MIN_SLOTS, STOP_ATR = 6, 5, 5, 3.0
# 币安美股永续(TRADIFI_PERPETUAL)里有的；拉不到 exchangeInfo 时用这份(2026-10-17核对)
TRADABLE_FALLBACK = set("MU WDC SNDK NVDA AMD AVGO QCOM TXN MRVL INTC ARM TSM AMAT LRCX KLAC TER ASML SMCI DELL HPE ANET "
                        "CIEN GLW VRT CSCO COHR MSFT ORCL ADBE CRM NOW GOOGL META AMZN NFLX UBER JPM GS XOM UNH MRK LLY "
                        "WMT COST HD MCD KO NKE CAT COIN MSTR MARA HOOD".split())


# ---------------- 工具 ----------------
def ema(xs, n):
    k = 2 / (n + 1); o = []; e = None
    for x in xs:
        e = x if e is None else e + k * (x - e); o.append(e)
    return o


def atr(b, n=14):
    o = []; a = None
    for i, x in enumerate(b):
        pc = b[i - 1]["c"] if i else x["c"]
        t = max(x["h"] - x["l"], abs(x["h"] - pc), abs(x["l"] - pc))
        a = t if a is None else (a * (n - 1) + t) / n; o.append(a)
    return o


def dstr(day_or_ms):
    ms = day_or_ms * DAY if day_or_ms < 10 ** 8 else day_or_ms
    return time.strftime("%Y-%m-%d", time.gmtime(ms / 1000))


def phase_of(t_ms):
    t = t_ms / 1000
    last = max(h for h in HALVINGS if h <= t)
    m = (t - last) / (86400 * 30.44)
    return ("牛" if m < 18 else ("熊" if m < 30 else "筑底")), m


PHASE_RULE = {"牛": "只做多", "熊": "只做空", "筑底": "多空都做"}


def _cache(name, ttl, fn, fresh=None):
    """fresh(v)=False 时即使没过 ttl 也重拉(2026-10-29：00:20 那次重算一直用 22:50 的缓存，漏掉 00:00 刚收盘的日线/8h K线)"""
    os.makedirs(OUT, exist_ok=True)
    p = os.path.join(OUT, name)
    if os.path.exists(p) and time.time() - os.path.getmtime(p) < ttl:
        with open(p, "r", encoding="utf-8") as f:
            v = json.load(f)
        if fresh is None or fresh(v):
            return v
    v = fn()
    with open(p + ".tmp", "w", encoding="utf-8") as f:
        json.dump(v, f)
    os.replace(p + ".tmp", p)
    return v


def spot_daily(sym):
    def fetch():
        out = []; s = 1500000000000
        while True:
            r = json.load(urllib.request.urlopen(
                f"https://api.binance.com/api/v3/klines?symbol={sym}&interval=1d&startTime={s}&limit=1000", timeout=20))
            if not r:
                break
            out += [{"t": int(x[0]), "o": float(x[1]), "h": float(x[2]), "l": float(x[3]), "c": float(x[4]), "v": float(x[5])} for x in r]
            if len(r) < 1000:
                break
            s = int(r[-1][0]) + 1; time.sleep(0.25)
        today = int(time.time() // 86400)
        return [b for b in out if b["t"] // DAY < today]          # 只要已收盘日线
    return _cache(f"spot2_{sym}_1d.json", 6 * 3600, fetch,
                  fresh=lambda v: bool(v) and v[-1]["t"] // DAY >= int(time.time() // 86400) - 1)


def stock_daily(tk):
    def fetch():
        b = stocks.get_klines(tk, "1d", 3000, closed_only=True)
        return [{"t": x["t"], "o": x["o"], "h": x["h"], "l": x["l"], "c": x["c"]} for x in b]
    return _cache(f"yf_{tk}_1d.json", 6 * 3600, fetch)


def tradable_set():
    try:
        info = json.load(urllib.request.urlopen("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=30))
        listed = {s["symbol"] for s in info["symbols"] if s.get("contractType") == "TRADIFI_PERPETUAL" and s.get("status") == "TRADING"}
        got = {t for t in SEC_OF if t + "USDT" in listed}
        return got or TRADABLE_FALLBACK
    except Exception:
        return TRADABLE_FALLBACK


# ---------------- 币圈腿 ----------------
def crypto_leg():
    D = {s: spot_daily(s) for s in M5}
    btc = D["BTCUSDT"]; bc = [x["c"] for x in btc]; b8, b30 = ema(bc, 8), ema(bc, 30)
    btc_idx = {x["t"]: i for i, x in enumerate(btc)}

    def btc_up(t):
        i = btc_idx.get(t)
        return None if i is None or i < 31 else b8[i] > b30[i]

    curves, trades, state = {}, [], {}
    for sym in M5:
        b = D[sym]; c = [x["c"] for x in b]; e8, e30 = ema(c, 8), ema(c, 30); A = atr(b)
        eq = 1.0; qty = 0.0; pos = 0; pend = None; cur = None; curve = {}; stop = None; order = None
        H = [x["h"] for x in b]; L = [x["l"] for x in b]
        for i in range(1, len(b)):
            t, op, cl = b[i]["t"], b[i]["o"], b[i]["c"]; pc = c[i - 1]
            if pend is not None:
                act, newpos, why = pend[:3]; ctr_in = len(pend) > 3 and pend[3]; pend = None; order = None
                if pos and act in ("exit", "flip"):
                    pnl = qty * (op - pc); eq += pnl; eq -= abs(qty) * op * FEE
                    cur.update(exit_t=t, exit=op, exit_reason=why[0] if act == "flip" else why,
                               ret=round(100 * (pos * (op / cur["entry"] - 1) - 2 * FEE), 2),
                               pnl_pct=round(100 * (eq / cur["_eq0"] - 1), 2), days=(t - cur["entry_t"]) // DAY)
                    trades.append(cur); cur = None; qty = 0; pos = 0; pc = op
                if act in ("enter", "flip") and newpos:
                    atrp = A[i - 1] / c[i - 1]; alloc = min(1.0, VOL_TGT / atrp)
                    eq0 = eq; pos = newpos; qty = eq * alloc / op * pos; eq -= abs(qty) * op * FEE; pc = op
                    cur = {"leg": "币圈", "symbol": sym, "side": "LONG" if pos > 0 else "SHORT", "entry_t": t, "entry": op,
                           "entry_reason": (why[1] if act == "flip" else why) + f"；ATR%={100 * atrp:.1f}，仓位=该币资金×{alloc:.2f}",
                           "alloc": round(alloc, 3), "_eq0": eq0}
                    stop = op - 3.0 * A[i - 1] if ctr_in else None
                    if ctr_in:
                        cur["stop"] = round(stop, 6); cur["counter"] = True
            if order is not None and not pos:                 # 盘中突破单(前一天收盘后挂的)
                o_side, lvl, why_o = order; order = None
                if (o_side > 0 and b[i]["h"] >= lvl) or (o_side < 0 and b[i]["l"] <= lvl):
                    px = max(op, lvl) if o_side > 0 else min(op, lvl)
                    atrp = A[i - 1] / c[i - 1]; alloc = min(1.0, VOL_TGT / atrp); eq0 = eq
                    pos = o_side; qty = eq * alloc / px * pos; eq -= abs(qty) * px * FEE; pc = px
                    stop = px - pos * 3.0 * A[i - 1]
                    cur = {"leg": "币圈", "symbol": sym, "side": "LONG" if pos > 0 else "SHORT", "entry_t": t, "entry": px,
                           "entry_reason": why_o + f"；盘中触价成交 {px:.6g}；ATR%={100 * atrp:.1f}，仓位=该币资金×{alloc:.2f}；均线跟上前靠3×ATR硬止损保护",
                           "alloc": round(alloc, 3), "_eq0": eq0, "stop": round(stop, 6), "brk": True}
            if pos and stop is not None and ((pos > 0 and b[i]["l"] <= stop) or (pos < 0 and b[i]["h"] >= stop)):
                px = min(op, stop) if pos > 0 else max(op, stop)
                if cur and cur["entry_t"] == t:
                    px = stop                                  # 当天突破进场又打到止损：按止损价
                eq += qty * (px - pc) - abs(qty) * px * FEE
                cur.update(exit_t=t, exit=px, exit_reason=f"{'跌破' if pos > 0 else '涨破'}3×ATR硬止损 {stop:.6g}" + ("(跳空，按开盘价成交)" if px == op and op != stop else ""),
                           ret=round(100 * (pos * (px / cur["entry"] - 1) - 2 * FEE), 2), pnl_pct=round(100 * (eq / cur["_eq0"] - 1), 2), days=(t - cur["entry_t"]) // DAY)
                trades.append(cur); cur = None; qty = 0; pos = 0; stop = None; pend = None
                curve[t // DAY] = max(eq, 0)
                continue
            if pos:
                eq += qty * (cl - pc)
            curve[t // DAY] = max(eq, 0)
            if i < 32:
                continue
            up = e8[i] > e30[i]; want = 1 if up else -1; raw = want
            ph, mo = phase_of(t)
            ema_txt = f"{dstr(t)}收盘 EMA8{'>' if up else '<'}EMA30"
            ph_txt = f"减半后第{mo:.0f}个月·{ph}市({PHASE_RULE[ph]})"
            blocked_by_phase = (ph == "牛" and want < 0) or (ph == "熊" and want > 0)
            if blocked_by_phase:
                want = 0
            ctr = False; br_txt = ""
            if ph == "熊" and raw > 0:                    # 熊市段唯一例外：极端底部反转(见 engine/bottom.py)
                if pos > 0 and cur and cur.get("counter"):
                    want = 1; blocked_by_phase = False   # 已持有的底部反转多单：均线还是多头就继续拿
                elif pos <= 0:
                    ok_b, br_txt = bottom.check(b[max(0, i - 599): i + 1])
                    if ok_b:
                        want = 1; blocked_by_phase = False; ctr = True
            if pos and cur and cur.get("brk"):           # 突破单：均线跟上就转成普通趋势单，跟上之前继续持有(靠3×ATR止损)
                if raw == pos:
                    cur["brk"] = False; stop = None
                    cur["entry_reason"] += f"；{dstr(t)} 均线跟上，转为趋势单(撤掉硬止损)"
                continue
            new_ok = True; btc_txt = ""
            if want and pos != want and sym != "BTCUSDT":
                bu = btc_up(t)
                btc_txt = f"BTC日线{'多头' if bu else '空头'}"
                if bu is None or (want > 0 and not bu) or (want < 0 and bu):
                    new_ok = False
            side_txt = lambda w: "多" if w > 0 else "空"
            ex_txt = f"；熊市段底部反转例外【{br_txt}】；3×ATR硬止损" if ctr else ""
            if pos and want != pos:
                if want and new_ok:
                    pend = ("flip", want, (f"{ema_txt}，反手做{side_txt(want)}",
                                           f"{ema_txt} → 开{side_txt(want)}(反手)；{ph_txt}" + (f"；{btc_txt}同向" if btc_txt else "") + ex_txt), ctr)
                elif blocked_by_phase and raw != pos:
                    pend = ("exit", 0, f"{ema_txt}，均线翻{side_txt(raw)}；{ph_txt}，不开{side_txt(raw)}，只平{side_txt(pos)}")
                elif blocked_by_phase:
                    pend = ("exit", 0, f"进入{ph_txt}，不再持{side_txt(pos)}，平仓")
                elif want and not new_ok:
                    pend = ("exit", 0, f"{ema_txt}，均线翻{side_txt(want)}，但{btc_txt}不同向，只平仓不反手")
                else:
                    pend = ("exit", 0, f"{ema_txt}，平{side_txt(pos)}")
            elif not pos and want and new_ok:
                pend = ("enter", want, f"{ema_txt} → 开{side_txt(want)}；{ph_txt}" + (f"；{btc_txt}同向" if btc_txt else "") + ex_txt, ctr)
            if not pos and pend is None and i >= BRK_N:     # 2026-10-17 盘中突破单：挂明天的单，不等均线交叉
                bu = btc_up(t) if sym != "BTCUSDT" else None
                if raw < 0 and (ph != "熊" or bottom.check(b[max(0, i - 599): i + 1])[0]) and (sym == "BTCUSDT" or bu):
                    lvl = max(H[i - BRK_N + 1: i + 1])
                    order = (1, lvl, f"{dstr(t)}收盘后挂突破单：明天涨破前{BRK_N}日最高 {lvl:.6g} 就开多(不等EMA8上穿EMA30)；{ph_txt}")
                elif raw > 0 and ph != "牛" and (sym == "BTCUSDT" or bu is False):
                    lvl = min(L[i - BRK_N + 1: i + 1])
                    order = (-1, lvl, f"{dstr(t)}收盘后挂突破单：明天跌破前{BRK_N}日最低 {lvl:.6g} 就开空(不等EMA8下穿EMA30)；{ph_txt}")
        curves[sym] = curve
        last = b[-1]; up = e8[-1] > e30[-1]; ph, mo = phase_of(last["t"])
        state[sym] = {"date": dstr(last["t"]), "close": last["c"], "ema8": round(e8[-1], 6), "ema30": round(e30[-1], 6),
                      "trend": "多头" if up else "空头", "atr_pct": round(100 * A[-1] / last["c"], 2),
                      "pending": pend[0] if pend else ("order" if order else None),
                      # 2026-10-30 对账：空仓时"下一根开盘开仓"也要带方向，recon 按执行后的状态比(否则擂台已在收盘开仓、回测还空仓，被误判后自动修复反复开平)
                      "pending_side": ("LONG" if pend[1] > 0 else "SHORT") if (pend and pend[0] == "enter") else None,
                      # 2026-10-19 实盘：今天挂着的盘中突破单(方向、触发价、开仓时仓位系数)，实盘执行器提前挂交易所条件单，触价即成交(同回测)
                      "brk_order": ({"side": "LONG" if order[0] > 0 else "SHORT", "level": order[1], "for_day": dstr(last["t"] + DAY),
                                     "alloc": round(min(1.0, VOL_TGT / (A[-1] / last["c"])), 4)} if (order and not pend) else None),
                      "pending_reason": (pend[2] if isinstance(pend[2], str) else pend[2][1]) if pend else (order[2] if order else None)}
        if cur:
            fr = round(100 * (pos * (last["c"] / cur["entry"] - 1) - FEE), 2)
            trades.append({**cur, "exit_t": None, "exit": last["c"], "exit_reason": "持仓中", "ret": fr,
                           "pnl_pct": round(100 * (eq / cur["_eq0"] - 1), 2), "days": (last["t"] - cur["entry_t"]) // DAY, "open": True})
            state[sym]["position"] = {"side": cur["side"], "entry": cur["entry"], "entry_t": cur["entry_t"], "ret": fr}
    days = sorted(set().union(*[set(v) for v in curves.values()]))
    last_v = {s: 1.0 for s in M5}; port = {}
    for d in days:
        for s in M5:
            if d in curves[s]:
                last_v[s] = curves[s][d]
        port[d] = sum(last_v.values()) / len(M5)
    ph, mo = phase_of(time.time() * 1000)
    bu = btc_up(btc[-1]["t"])
    meta = {"phase": ph, "phase_month": round(mo, 1), "phase_rule": PHASE_RULE[ph], "btc_trend": "多头" if bu else "空头"}
    for t in trades:
        t.pop("_eq0", None)
    CURVES["日线EMA8/30"] = curves
    return port, trades, state, meta, D


# ---------------- 美股腿 ----------------
def stock_leg():
    D = {}
    for tk in SEC_OF:
        try:
            b = stock_daily(tk)
            if b:                    # 2026-10-19 前视检验(截断法)发现：原来"整段历史>200根"才纳入，用到了未来(UBER/ARM 上市初期)；改为有数据就纳入，是否交易由当天已有K线数(i>=32)决定
                D[tk] = b
        except Exception:
            pass
    TR = tradable_set()
    mclose = collections.defaultdict(dict)
    for s, b in D.items():
        for x in b:
            g = time.gmtime(x["t"] / 1000); mclose[s][(g.tm_year, g.tm_mon)] = x["c"]
    months = sorted({m for s in mclose for m in mclose[s]})
    sched, scores_by_m = {}, {}
    for i, m in enumerate(months):
        if i < LOOKBACK_M + 1:
            continue
        prev, base = months[i - 1], months[i - 1 - LOOKBACK_M]
        sc = {}
        for sec, lst in SECTORS.items():
            r = [mclose[s][prev] / mclose[s][base] - 1 for s in lst if s in mclose and prev in mclose[s] and base in mclose[s]]
            if len(r) >= 2:
                sc[sec] = sum(r) / len(r)
        rk = sorted(sc, key=sc.get, reverse=True)
        sched[m] = rk[:TOP_K]; scores_by_m[m] = [(k, round(100 * sc[k], 1)) for k in rk]
    # 当前月(还没出现在 months 里时)也按上月末算一次
    g = time.gmtime(); cur_m = (g.tm_year, g.tm_mon)
    if cur_m not in sched and len(months) > LOOKBACK_M:
        prev, base = months[-1], months[-1 - LOOKBACK_M]
        sc = {}
        for sec, lst in SECTORS.items():
            r = [mclose[s][prev] / mclose[s][base] - 1 for s in lst if s in mclose and prev in mclose[s] and base in mclose[s]]
            if len(r) >= 2:
                sc[sec] = sum(r) / len(r)
        rk = sorted(sc, key=sc.get, reverse=True)
        sched[cur_m] = rk[:TOP_K]; scores_by_m[cur_m] = [(k, round(100 * sc[k], 1)) for k in rk]

    def mkey(d):
        g = time.gmtime(d * 86400); return (g.tm_year, g.tm_mon)

    PRE = {}
    for s, b in D.items():
        c = [x["c"] for x in b]; f, sl = ema(c, 8), ema(c, 30)
        PRE[s] = {"day": {x["t"] // DAY: i for i, x in enumerate(b)}, "b": b, "dir": [1 if f[i] > sl[i] else -1 for i in range(len(b))],
                  "A": atr(b), "e8": f, "e30": sl, "first": b[0]["t"] // DAY}

    def active(d):
        secs = sched.get(mkey(d), ())
        return {s for s in PRE if SEC_OF[s] in secs and PRE[s]["first"] <= d and s in TR}

    alldays = sorted({d for s in PRE for d in PRE[s]["day"]})
    E = 1.0; pos = {}; pend = {}; blocked = {}; curve = {}; trades = []; openinfo = {}
    for d in alldays:
        if d < 17167:
            continue
        for s, act in list(pend.items()):
            P = PRE[s]; i = P["day"].get(d)
            if i is None:
                continue
            op = P["b"][i]["o"]; del pend[s]
            if s in pos and act[0] == "exit":
                q, last, _ = pos.pop(s); E += q * (op - last) - abs(q) * op * FEE
                tr = openinfo.pop(s); tr.update(exit_t=d * DAY, exit=op, exit_reason=act[2],
                                                ret=round(100 * (op / tr["entry"] - 1 - 2 * FEE), 2), days=d - tr["entry_t"] // DAY,
                                                pnl_pct=round(100 * q * (op - tr["entry"]) / tr["_E0"], 2))
                trades.append(tr)
            if act[0] == "enter" and act[1] > 0:
                if act[2] > 0:
                    q = act[2] / op; E -= act[2] * FEE
                    pos[s] = [q, op, op - STOP_ATR * P["A"][i - 1]]
                    openinfo[s] = {"leg": "美股", "symbol": s, "side": "LONG", "entry_t": d * DAY, "entry": op, "stop": round(pos[s][2], 4),
                                   "entry_reason": act[3], "alloc": round(act[2] / E, 4), "_E0": E}
        for s in list(pos):
            P = PRE[s]; i = P["day"].get(d)
            if i is None:
                continue
            x = P["b"][i]; q, last, stp = pos[s]
            if x["l"] <= stp:
                px = min(x["o"], stp); E += q * (px - last) - abs(q) * px * FEE; del pos[s]; blocked[s] = 1; pend.pop(s, None)
                tr = openinfo.pop(s)
                tr.update(exit_t=d * DAY, exit=px, ret=round(100 * (px / tr["entry"] - 1 - 2 * FEE), 2), days=d - tr["entry_t"] // DAY,
                          pnl_pct=round(100 * q * (px - tr["entry"]) / tr["_E0"], 2),
                          exit_reason=f"跌破3×ATR硬止损 {stp:.2f}" + ("(跳空低开，按开盘价成交)" if x["o"] < stp else ""))
                trades.append(tr)
                continue
            E += q * (x["c"] - last); pos[s][1] = x["c"]
        curve[d] = E
        gross = sum(abs(q) * l for q, l, _ in pos.values())
        act_set = active(d); k = max(1, len(act_set)); m = mkey(d); secs = sched.get(m, [])
        for s in sorted(act_set | set(pos)):          # 2026-10-19 固定顺序：集合遍历顺序随哈希种子变，碰到1倍上限时结果不可复现(独立研究发现)
            P = PRE[s]; i = P["day"].get(d)
            if i is None or i < 32:
                continue
            raw = P["dir"][i]; want = raw if s in act_set else 0
            if want < 0:
                want = 0
            if blocked.get(s) and want != blocked[s]:
                blocked.pop(s, None)
            if blocked.get(s) == want:
                want = 0
            cur = 1 if s in pos else 0
            if want == cur:
                continue
            ds = dstr(d)
            if cur and not want:
                if s not in act_set:
                    why = f"{m[0]}-{m[1]:02d} {SEC_OF[s]}掉出行业前{TOP_K}(新名单：{'、'.join(secs)})，次日开盘平仓"
                else:
                    why = f"{ds}收盘 正股EMA8<EMA30，次日开盘平仓"
                pend[s] = ("exit", 0, why)
                continue
            atrp = P["A"][i] / P["b"][i]["c"]; alloc = min(1.0, VOL_TGT / atrp)
            notional = E * alloc / max(k, MIN_SLOTS)
            room = max(0.0, 1.0 * E - gross)
            notional = min(notional, room)
            rank = secs.index(SEC_OF[s]) + 1 if SEC_OF[s] in secs else 0
            sc = dict(scores_by_m.get(m, [])).get(SEC_OF[s])
            why = (f"{m[0]}-{m[1]:02d}行业前{TOP_K}：{'、'.join(secs)}；{SEC_OF[s]}排第{rank}(近6个月{sc:+.1f}%)；"
                   f"{ds}收盘 正股EMA8>EMA30 → 次日开盘做多；ATR%={100 * atrp:.1f}，仓位=净值×{alloc:.2f}÷{max(k, MIN_SLOTS)}只"
                   + ("(总仓位已到1倍上限，缩量)" if notional < E * alloc / max(k, MIN_SLOTS) - 1e-12 else ""))
            pend[s] = ("enter", 1, notional, why)
            gross += notional
    state_pos = []
    for s, tr in openinfo.items():
        P = PRE[s]; lastc = P["b"][-1]["c"]
        trades.append({**tr, "exit_t": None, "exit": lastc, "exit_reason": "持仓中", "ret": round(100 * (lastc / tr["entry"] - 1 - FEE), 2),
                       "days": P["b"][-1]["t"] // DAY - tr["entry_t"] // DAY, "pnl_pct": None, "open": True})
        state_pos.append({"symbol": s, "sector": SEC_OF[s], "entry": tr["entry"], "entry_t": tr["entry_t"], "stop": tr["stop"],
                          "close": lastc, "ret": round(100 * (lastc / tr["entry"] - 1), 2)})
    for t in trades:
        t.pop("_E0", None); t["sector"] = SEC_OF[t["symbol"]]
    m = cur_m if cur_m in sched else max(sched)
    pend_list = [{"symbol": s, "action": "开多" if a[0] == "enter" else "平仓", "reason": a[3] if a[0] == "enter" else a[2]} for s, a in pend.items()]
    rot = {"month": f"{m[0]}-{m[1]:02d}", "selected": sched[m], "scores": scores_by_m[m],
           "tradable": {sec: [t for t in SECTORS[sec] if t in TR] for sec in SECTORS},
           "active": sorted(t for t in SEC_OF if SEC_OF[t] in sched[m] and t in TR),
           "history": [{"month": f"{k[0]}-{k[1]:02d}", "selected": v} for k, v in sorted(sched.items())[-24:]],
           "months": {f"{k[0]}-{k[1]:02d}": v for k, v in sorted(sched.items())}}
    stk_state = {}
    for s in PRE:
        P = PRE[s]; b = P["b"][-1]
        stk_state[s] = {"date": dstr(b["t"]), "close": b["c"], "trend": "多头" if P["dir"][-1] > 0 else "空头", "sector": SEC_OF[s],
                        "tradable": s in TR, "active": s in rot["active"]}
    return curve, trades, {"rotation": rot, "positions": state_pos, "pending": pend_list, "symbols": stk_state}, D


# ---------------- 汇总 ----------------
def stats(curve):
    ks = sorted(k for k in curve if k >= START_DAY); v = [curve[k] for k in ks]
    pk = 0; dd = 0; dds = []
    for x in v:
        pk = max(pk, x); dd = max(dd, 1 - x / pk); dds.append(round(-100 * (1 - x / pk), 2))
    yrs = (ks[-1] - ks[0]) / 365.25; cagr = (v[-1] / v[0]) ** (1 / yrs) - 1
    yr = {}
    for k, x in zip(ks, v):
        y = time.gmtime(k * 86400).tm_year; yr.setdefault(y, [x, x]); yr[y][1] = x
    ys = sorted(yr); yret = {str(y): round(100 * (yr[y][1] / (yr[ys[i - 1]][1] if i else yr[y][0]) - 1), 1) for i, y in enumerate(ys)}
    k12 = [k for k in ks if k >= ks[-1] - 365]; r12 = curve[k12[-1]] / curve[k12[0]] - 1
    pk = 0; d12 = 0
    for k in k12:
        pk = max(pk, curve[k]); d12 = max(d12, 1 - curve[k] / pk)
    return {"cagr": round(100 * cagr, 1), "max_dd": round(100 * dd, 1), "ratio": round(cagr / dd, 2) if dd else None,
            "ytd": yret.get(str(ys[-1])), "last12m": round(100 * r12, 1), "last12m_dd": round(100 * d12, 1), "yearly": yret,
            "series": [[k * DAY, round(x / v[0], 4)] for k, x in zip(ks, v)], "dd_series": [[k * DAY, d] for k, d in zip(ks, dds)]}


def combine(cs, ws):
    days = sorted(set().union(*[set(c) for c in cs])); last = [None] * len(cs); eq = 1.0; out = {}
    for d in days:
        r = 0.0
        for j, c in enumerate(cs):
            if d in c:
                if last[j]:
                    r += ws[j] * (c[d] / last[j] - 1)
                last[j] = c[d]
        eq *= 1 + r; out[d] = eq
    return out


def spot_8h(sym):
    def fetch():
        out = []; s = 1500000000000
        while True:
            r = json.load(urllib.request.urlopen(
                f"https://api.binance.com/api/v3/klines?symbol={sym}&interval=8h&startTime={s}&limit=1000", timeout=20))
            if not r:
                break
            out += [{"t": int(x[0]), "o": float(x[1]), "h": float(x[2]), "l": float(x[3]), "c": float(x[4])} for x in r]
            if len(r) < 1000:
                break
            s = int(r[-1][0]) + 1; time.sleep(0.25)
        now = int(time.time() * 1000)
        return [b for b in out if b["t"] + 8 * 3600000 <= now]          # 只要已收盘
    H8 = 8 * 3600000
    return _cache(f"spot_{sym}_8h.json", 3 * 3600, fetch,
                  fresh=lambda v: bool(v) and v[-1]["t"] >= int(time.time() * 1000) // H8 * H8 - H8)


def _st_dirs(b, n=10, m=3.0):
    A = atr(b, n); d = [1] * len(b); ub = lb = None
    for i, x in enumerate(b):
        hl = (x["h"] + x["l"]) / 2; u = hl + m * A[i]; l = hl - m * A[i]
        if i == 0:
            ub, lb = u, l; continue
        pc = b[i - 1]["c"]
        ub = u if (u < ub or pc > ub) else ub; lb = l if (l > lb or pc < lb) else lb
        d[i] = 1 if x["c"] > ub else (-1 if x["c"] < lb else d[i - 1])
    return d


def _bb_states(b, n=25, k=1.25):          # 2026-10-19 宝贝采纳：布林(20,2)→(25,1.25)，/tmp/bb_grid.py+bb_fine.py 周期20~35×倍数1.0~1.5 整片稳定区12/12过关，取区中心；比值5.39→5.57
    c = [x["c"] for x in b]; d = [0] * len(b); st = 0
    for i in range(len(b)):
        if i >= n - 1:
            w = c[i - n + 1: i + 1]; m = sum(w) / n; sd = (sum((v - m) ** 2 for v in w) / n) ** .5
            if c[i] > m + k * sd: st = 1
            elif c[i] < m - k * sd: st = -1
            elif (st > 0 and c[i] < m) or (st < 0 and c[i] > m): st = 0
        d[i] = st
    return d


# 2026-10-19 宝贝同意改等权(/tmp/dd_overlays.py、dd_f_nbhd.py)：50/25/25 → 各1/3，比值5.19→5.39、回撤20.1→18.7%，全程/前后半都更好
SUB_W = {"日线EMA8/30": 1 / 3, "8h超级趋势": 1 / 3, "8h布林突破": 1 / 3}
# 2026-10-19 宝贝采纳"稳健版币种权重"(/root/research weight_coins2 / yearly_compare_20261019.py)：
#   每年1月1日，按过去3年每个币(三条子腿组合)在本策略上的收益回撤比给权重，单币≤40%，历史不够就等权；
#   并且币圈腿按"权重×总资金、每天平衡"汇总——和擂台/实盘按总资金下单的方式一致(原来是各币独立滚动、不平衡)。
#   2017起主方案：各币独立滚动 4.43 → 等权每天平衡 4.86 → 稳健版 5.02(前后两半都更好，逐年 5/10 年收益更高、6/10 年回撤更小)。
CURVES: dict = {}
WEIGHT_LOOKBACK, WEIGHT_CAP = 1095, 0.4


def _win_ratio(cv, a, z):
    ks = [k for k in sorted(cv) if a <= k <= z]
    if len(ks) < 2: return None
    v = [cv[k] for k in ks]; pk = 0.0; dd = 0.0
    for x in v:
        pk = max(pk, x); dd = max(dd, 1 - x / pk if pk else 0)
    yrs = (ks[-1] - ks[0]) / 365.25
    if yrs <= 0 or v[0] <= 0 or v[-1] <= 0: return None
    cagr = (v[-1] / v[0]) ** (1 / yrs) - 1
    return cagr / dd if dd else None


def _capw(raw, cap):
    w = {s: x for s, x in raw.items() if x > 0}
    if not w: return {}
    for _ in range(20):
        t_ = sum(w.values()); w = {s: x / t_ for s, x in w.items()}
        over = [s for s, x in w.items() if x > cap + 1e-9]
        if not over: break
        rest = {s: x for s, x in w.items() if s not in over}; rt = sum(rest.values()); left = 1 - cap * len(over)
        w = {**{s: cap for s in over}, **({s: x / rt * left for s, x in rest.items()} if rt > 0 else {})}
    return w


def coin_weights(coin_curves, years):
    """{年份: {币: 权重}}：只用该年1月1日之前 WEIGHT_LOOKBACK 天的数据"""
    out = {}
    for y in years:
        y0 = calendar.timegm((y, 1, 1, 0, 0, 0)) // 86400; y1 = calendar.timegm((y + 1, 1, 1, 0, 0, 0)) // 86400
        avail = [s for s, cv in coin_curves.items() if any(y0 <= k < y1 for k in cv)]
        sc = {}
        for s in avail:
            ks = [k for k in coin_curves[s] if y0 - WEIGHT_LOOKBACK <= k < y0]
            if len(ks) >= 300:
                r = _win_ratio(coin_curves[s], y0 - WEIGHT_LOOKBACK, y0 - 1)
                if r is not None: sc[s] = max(0.0, r)
        w = _capw(sc, WEIGHT_CAP)
        out[y] = {s: round(x, 4) for s, x in (w or {s: 1 / len(avail) for s in avail}).items()} if avail else {}
    return out


def weighted_basket(coin_curves, wy):
    days = sorted(set().union(*[set(v) for v in coin_curves.values()])); prev = {}; v = 1.0; out = {}
    for d in days:
        w = wy.get(time.gmtime(d * 86400).tm_year, {}); r = 0.0
        for s, cv in coin_curves.items():
            if d in cv:
                if s in prev: r += w.get(s, 0.0) * (cv[d] / prev[s] - 1)
                prev[s] = cv[d]
        v *= 1 + r; out[d] = v
    return out   # 2026-10-17 B方案：收益/回撤 3.09→3.52


def crypto_8h_leg(kind, btc_daily):
    """8h 超级趋势 / 8h 布林突破 + 严格减半周期 + 山寨跟BTC日线同向 + 波动率调仓(3%×√(8/24))。逻辑同 /root/research/mtf_grid_20261017.py。"""
    name = "8h超级趋势" if kind == "st" else "8h布林突破"
    bt = [x["t"] + DAY for x in btc_daily]; bc = [x["c"] for x in btc_daily]; b8, b30 = ema(bc, 8), ema(bc, 30)

    def btc_dir(close_ms):
        j = bisect.bisect_right(bt, close_ms) - 1
        return 0 if j < 31 else (1 if b8[j] > b30[j] else -1)
    vt = VOL_TGT * math.sqrt(8 / 24); P8 = 8 * 3600000
    curves, trades, opens = {}, [], {}
    for sym in M5:
        b = spot_8h(sym); A = atr(b); D = _st_dirs(b) if kind == "st" else _bb_states(b)
        eq = 1.0; qty = 0.0; pos = 0; pend = None; cur = None; curve = {}
        for i in range(1, len(b)):
            t, op, cl = b[i]["t"], b[i]["o"], b[i]["c"]; pc = b[i - 1]["c"]
            if pend is not None:
                newpos, why_out, why_in = pend; pend = None
                if pos:
                    eq += qty * (op - pc) - abs(qty) * op * FEE
                    cur.update(exit_t=t, exit=op, exit_reason=why_out, ret=round(100 * (pos * (op / cur["entry"] - 1) - 2 * FEE), 2),
                               pnl_pct=round(100 * (eq / cur["_eq0"] - 1), 2), days=round((t - cur["entry_t"]) / DAY, 1))
                    trades.append(cur); cur = None; qty = 0; pos = 0; pc = op
                if newpos:
                    atrp = A[i - 1] / b[i - 1]["c"]; alloc = min(1.0, vt / atrp); eq0 = eq
                    pos = newpos; qty = eq * alloc / op * pos; eq -= abs(qty) * op * FEE; pc = op
                    cur = {"leg": "币圈", "sub": name, "w": SUB_W[name], "symbol": sym, "side": "LONG" if pos > 0 else "SHORT", "entry_t": t, "entry": op,
                           "entry_reason": why_in + f"；ATR%(8h)={100 * atrp:.1f}，仓位=该币资金×{alloc:.2f}", "alloc": round(alloc, 3), "_eq0": eq0}
            if pos:
                eq += qty * (cl - pc)
            curve[t // DAY] = max(eq, 0)
            if i < 60:
                continue
            close = t + P8; ts = time.strftime("%Y-%m-%d %H:%M", time.gmtime(close / 1000))
            want = D[i]; ph, mo = phase_of(close); ph_txt = f"减半后第{mo:.0f}个月·{ph}市({PHASE_RULE[ph]})"
            if (ph == "牛" and want < 0) or (ph == "熊" and want > 0):
                want = 0; blk = True
            else:
                blk = False
            if pos == want:
                continue
            word = lambda x: "多" if x > 0 else "空"
            if want and sym != "BTCUSDT" and btc_dir(close) != want:
                bd = btc_dir(close); want = 0
                why_btc = f"；BTC日线{'多头' if bd > 0 else '空头'}不同向"
            else:
                why_btc = ""
            sig_txt = (f"{ts} 8h收盘 超级趋势{'转多' if D[i] > 0 else '转空'}" if kind == "st" else
                       f"{ts} 8h收盘 " + ({1: "突破布林上轨", -1: "跌破布林下轨", 0: "回到布林中轨"}[D[i]]))
            if want == pos:
                continue
            why_out = (sig_txt + (f"；{ph_txt}，只平不反手" if blk else why_btc + ("，只平不反手" if why_btc else ""))) if pos else ""
            why_in = f"{sig_txt} → 开{word(want)}；{ph_txt}" + ("；BTC日线同向" if sym != "BTCUSDT" else "") if want else ""
            pend = (want, why_out, why_in)
        curves[sym] = curve
        if cur:
            last = b[-1]
            trades.append({**cur, "exit_t": None, "exit": last["c"], "exit_reason": "持仓中", "ret": round(100 * (pos * (last["c"] / cur["entry"] - 1) - FEE), 2),
                           "pnl_pct": round(100 * (eq / cur["_eq0"] - 1), 2), "days": round((last["t"] - cur["entry_t"]) / DAY, 1), "open": True})
            opens[sym] = {"side": cur["side"], "entry": cur["entry"], "entry_t": cur["entry_t"],
                          # 2026-10-22 修复：暴露"下一根开盘才执行"的挂单状态给对账(engine/recon.py)。
                          # recon.py 读 crypto.state[sym]["subs"][sub]["pending"] 判断"回测已发平/反手信号、下一根开盘执行"，
                          # 日线腿(crypto_leg)一直有这个字段，8h 腿此前没暴露，导致对账把"即将平仓"当"仍持仓"，
                          # 自动修复器据此反复开/平形成震荡。这里 pend=(want,...)：want=0 是平仓、非0是反手。
                          "pending": ("flip" if pend[0] else "exit") if pend else None}
        elif pend and pend[0]:
            # 2026-10-30 空仓时"下一根开盘开仓"同样暴露给对账(只有 recon 读 subs)，否则擂台在信号K线收盘已开仓会被判成"回测空仓"
            opens[sym] = {"side": None, "pending": "enter", "pending_side": "LONG" if pend[0] > 0 else "SHORT"}
    days = sorted(set().union(*[set(v) for v in curves.values()]))
    last_v = {s: 1.0 for s in M5}; port = {}
    for d in days:
        for s in M5:
            if d in curves[s]:
                last_v[s] = curves[s][d]
        port[d] = sum(last_v.values()) / len(M5)
    for t in trades:
        t.pop("_eq0", None)
    CURVES["8h超级趋势" if kind == "st" else "8h布林突破"] = curves
    return port, trades, opens


BRK_N = 5                              # 2026-10-17 盘中突破单：突破前5日高/低就进场(/root/research/breakout_stop_20261017.py：收益/回撤3.02→3.23)
VC_TARGET, VC_LOOKBACK = 0.40, 60      # 2026-10-17 组合波动率控制：年化波动目标40%、回看60天、只降不加


def vol_control(curves, ws, gross):
    """组合按权重每天再平衡；用截至昨天的60日实际波动率算缩放 s=min(1, 40%÷波动)，只降不加，调仓成本 |Δs|×总名义×0.08%。
    回测(/root/research/port_volctl_20261017.py)：方案F 77.5%/回撤21.2%/3.65 → 76.1%/17.5%/4.34；2倍 111%/27.9% → 97%/20.7%。"""
    days = sorted(set().union(*[set(c) for c in curves])); last = [None] * len(curves); eq = 1.0; out = {}; hist = []; s_prev = 1.0; scale = {}
    for d in days:
        r = 0.0
        for j, c in enumerate(curves):
            if d in c:
                if last[j]:
                    r += ws[j] * (c[d] / last[j] - 1)
                last[j] = c[d]
        s = 1.0
        if len(hist) >= VC_LOOKBACK:
            w = hist[-VC_LOOKBACK:]; m = sum(w) / len(w)
            sd = math.sqrt(sum((x - m) ** 2 for x in w) / (len(w) - 1)) * math.sqrt(365)
            s = min(1.0, VC_TARGET / sd) if sd > 0 else 1.0
        eq *= 1 + s * r - abs(s - s_prev) * gross * FEE; s_prev = s; hist.append(r); out[d] = eq; scale[d] = round(s, 3)
    return out, scale


def build() -> dict:
    t0 = time.time()
    cdaily, ctrades, cstate, cmeta, CD = crypto_leg()
    for t in ctrades:
        t["sub"] = "日线EMA8/30"; t["w"] = SUB_W["日线EMA8/30"]
    cst, tst, ost = crypto_8h_leg("st", CD["BTCUSDT"])
    cbb, tbb, obb = crypto_8h_leg("bb", CD["BTCUSDT"])
    for sym in M5:
        cstate[sym]["subs"] = {"8h超级趋势": ost.get(sym), "8h布林突破": obb.get(sym)}
    coin_cv = {s: combine([CURVES["日线EMA8/30"][s], CURVES["8h超级趋势"][s], CURVES["8h布林突破"][s]],
                          [SUB_W["日线EMA8/30"], SUB_W["8h超级趋势"], SUB_W["8h布林突破"]]) for s in M5}
    cur_y = time.gmtime().tm_year
    WY = coin_weights(coin_cv, range(2017, cur_y + 1))
    cport = weighted_basket(coin_cv, WY)
    with open(os.path.join(OUT, "crypto_weights.json"), "w", encoding="utf-8") as f:      # 擂台三条币圈子腿按这个下单
        json.dump({"year": cur_y, "current": WY.get(cur_y, {}), "by_year": {str(k): v for k, v in WY.items()},
                   "rule": f"每年1月1日按过去{WEIGHT_LOOKBACK // 365}年每个币的收益回撤比定权重，单币≤{int(WEIGHT_CAP * 100)}%", "asof": int(time.time())}, f, ensure_ascii=False)
    ctrades = ctrades + tst + tbb
    scurve, strades, sstate, SD = stock_leg()
    F = combine([cport, scurve], [W_CRYPTO, W_STOCK]); A = combine([cport, scurve], [0.5, 0.5])
    F0 = F
    F, F_scale = vol_control([cport, scurve], [W_CRYPTO, W_STOCK], W_CRYPTO + W_STOCK)
    E2, _ = vol_control([cport, scurve], [0.75, 0.75], 1.5)      # 对比：原方案F 1.5倍+波动率控制
    # 小资金E账户低于BTC最小下单门槛时使用的同口径基准：只把2026币权重中的BTC份额重分给其余币。
    WY_small = {y: dict(w) for y, w in WY.items()}
    _ws = WY_small.get(cur_y, {})
    _wt = sum(v for s, v in _ws.items() if s != "BTCUSDT")
    WY_small[cur_y] = {s: v / _wt for s, v in _ws.items() if s != "BTCUSDT"} if _wt else {}
    cport_small = weighted_basket(coin_cv, WY_small)
    F_small, _ = vol_control([cport_small, scurve], [W_CRYPTO, W_STOCK], W_CRYPTO + W_STOCK)
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "rotation.json"), "w", encoding="utf-8") as f:     # 给工作台的 plan_f_stock 用
        json.dump({"months": sstate["rotation"]["months"], "asof": int(time.time())}, f, ensure_ascii=False)
    for s, b in list(CD.items()) + list(SD.items()):          # 每个品种的日线 + EMA8/30，给复盘K线图用
        c = [x["c"] for x in b]; e8, e30 = ema(c, 8), ema(c, 30)
        rows = [[x["t"], x["o"], x["h"], x["l"], x["c"], round(e8[i], 6), round(e30[i], 6)] for i, x in enumerate(b) if x["t"] // DAY >= START_DAY - 60]
        with open(os.path.join(OUT, f"bars_{s}.json"), "w", encoding="utf-8") as f:
            json.dump(rows, f)
    res = {
        "asof": int(time.time()), "build_sec": round(time.time() - t0, 1),
        "data_last_bar": {s: {"1d": CD[s][-1]["t"], "8h": spot_8h(s)[-1]["t"]} for s in M5},   # 对账器用来判断回测是否已含最新收盘K线
        "plan": {"name": "方案F", "w_crypto": W_CRYPTO, "w_stock": W_STOCK, "gross_max": W_CRYPTO + W_STOCK,
                 "fee": FEE, "crypto_symbols": M5, "vol_target": VOL_TGT, "lookback_m": LOOKBACK_M, "top_k": TOP_K},
        "series": {"F": stats(F), "F_no_btc_2026": stats(F_small), "F0": stats(F0), "E2": stats(E2), "A": stats(A), "crypto": stats(cport), "stock": stats(scurve),
                   "crypto_daily": stats(cdaily), "crypto_st8h": stats(cst), "crypto_bb8h": stats(cbb)},
        "crypto_mix": SUB_W,
        "crypto_weights": {str(k): v for k, v in WY.items()}, "crypto_weights_current": WY.get(cur_y, {}),
        "vol_control": {"target": VC_TARGET, "lookback": VC_LOOKBACK, "scale_now": F_scale[max(F_scale)]},
        "crypto": {"meta": cmeta, "state": cstate},
        "stock": sstate,
        "trades": sorted(ctrades + strades, key=lambda t: t["entry_t"], reverse=True),
    }
    return res


def write(res: dict):
    os.makedirs(OUT, exist_ok=True)
    p = os.path.join(OUT, "optimal.json")
    with open(p + ".tmp", "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False)
    os.replace(p + ".tmp", p)
