# -*- coding: utf-8 -*-
"""2026-11-01 宝贝要的'SNDK单票方案单独K线复盘页'(/sndk)数据。

回测重放逻辑逐行照抄 live/sndk_solo_executor.py 的 replay()(执行器会在 import 时读 C 账户密钥，所以不能直接 import 它)：
1h K线收盘 EMA7>EMA25 持多 / <平多，只做多；开仓价−4×ATR14 止损不动；止损后等 EMA 转空一次再进；
v2 ADX逆向仓位(信号K线 ADX<20 1倍 / 20~30 0.75 / ≥30 0.5)；v3 纽约周六日K线不判断(止损照常)。
这里多记了每笔的止损价、信号K线ADX、仓位倍数、开平仓原因，以及被'周末不判断'跳过的交叉，给复盘用。
结果和执行器 status.json 的 bt_since_listing 对得上(自检见 build() 里 n/收益)。
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, "optimal_data")
SYM = "SNDKUSDT"
FAST, SLOW, ATR_N, STOP_ATR = 7, 25, 14, 4.0
ADX_CUTS, ADX_SIZES = (20, 30), (1.0, 0.75, 0.5)
H1 = 3600000
LIST_START = 1775520000000            # 2026-04-07 SNDKUSDT 上线
ARMED_AT = 1793433362137              # 2026-10-31 07:55 UTC C账户 ARMED(实盘开始)
FEE = 0.0004                          # 回测口径展示用：吃单 0.04%/边(执行器先挂 0 费率 maker)
NY = ZoneInfo("America/New_York")
TTL = 600


def ema(c, n):
    k = 2 / (n + 1); o = [c[0]]
    for x in c[1:]:
        o.append(o[-1] + k * (x - o[-1]))
    return o


def atr_adx(b, n=ATR_N):
    tr = [b[0]["h"] - b[0]["l"]]; pdm = [0.0]; mdm = [0.0]
    for i in range(1, len(b)):
        h, l, pc = b[i]["h"], b[i]["l"], b[i - 1]["c"]
        tr.append(max(h - l, abs(h - pc), abs(l - pc))); up, dn = h - b[i - 1]["h"], b[i - 1]["l"] - l
        pdm.append(up if up > dn and up > 0 else 0.0); mdm.append(dn if dn > up and dn > 0 else 0.0)
    A = [tr[0]]; P = [0.0]; M = [0.0]; X = [0.0]
    for i in range(1, len(b)):
        A.append(A[-1] + (tr[i] - A[-1]) / n); P.append(P[-1] + (pdm[i] - P[-1]) / n); M.append(M[-1] + (mdm[i] - M[-1]) / n)
        pdi, mdi = (P[i] / A[i], M[i] / A[i]) if A[i] else (0, 0)
        X.append(X[-1] + ((abs(pdi - mdi) / (pdi + mdi) * 100 if pdi + mdi else 0) - X[-1]) / n)
    return A, X


def ny_weekend(t_ms):
    return datetime.fromtimestamp(t_ms / 1000, timezone.utc).astimezone(NY).weekday() >= 5


def adx_size(x):
    return ADX_SIZES[0] if x < ADX_CUTS[0] else (ADX_SIZES[1] if x < ADX_CUTS[1] else ADX_SIZES[2])


def nyt(t_ms):
    return datetime.fromtimestamp(t_ms / 1000, timezone.utc).astimezone(NY).strftime("%m-%d %H:%M")


def fetch_bars():
    p = os.path.join(OUT, "sndk_solo_bars.json")
    b = []
    try:
        with open(p, "r", encoding="utf-8") as f:
            b = json.load(f)
    except Exception:
        b = []
    b = b[:-2] if len(b) > 2 else []          # 最后两根重拉(可能当时没收盘)
    s = b[-1]["t"] + 1 if b else LIST_START
    now = int(time.time() * 1000)
    while True:
        r = json.load(urllib.request.urlopen(f"https://fapi.binance.com/fapi/v1/klines?symbol={SYM}&interval=1h&startTime={s}&limit=1500", timeout=20))
        if not r:
            break
        b += [{"t": int(x[0]), "o": float(x[1]), "h": float(x[2]), "l": float(x[3]), "c": float(x[4]), "v": float(x[5])} for x in r]
        if len(r) < 1500:
            break
        s = int(r[-1][0]) + 1
    b = [x for x in b if x["t"] + H1 <= now]                  # 只要已收盘
    seen = set(); b = [x for x in b if not (x["t"] in seen or seen.add(x["t"]))]
    with open(p + ".tmp", "w", encoding="utf-8") as f:
        json.dump(b, f)
    os.replace(p + ".tmp", p)
    return b


def replay_full(b):
    """同 executor.replay()，另外记录每笔完整信息。"""
    c = [x["c"] for x in b]; ef, es = ema(c, FAST), ema(c, SLOW); A, X = atr_adx(b)
    d = 0; pend = None; pend_why = ""; stop = None; lock = 0; ent = None; trades = []; eq = 1.0; curve = []; skipped = []
    for i in range(1, len(b)):
        op, lo = b[i]["o"], b[i]["l"]
        if pend is not None:
            tgt = pend; pend = None
            if d and tgt != d:
                r = op / ent["px"] - 1
                trades.append({**ent, "out_t": b[i]["t"], "out": op, "why_out": pend_why, "kind": "signal", "ret": round(100 * r, 2),
                               "pnl": round(100 * ent["m"] * r, 2), "bars": i - ent["i"]})
                eq *= 1 + ent["m"] * r; d = 0
            if tgt and not d:
                d = 1; stop = op - STOP_ATR * A[i - 1]
                ent = {"t": b[i]["t"], "px": op, "i": i, "adx": round(X[i - 1], 1), "m": adx_size(X[i - 1]), "stop": round(stop, 2),
                       "atr": round(A[i - 1], 2), "why_in": pend_why}
        if d and lo <= stop:
            px = min(op, stop); r = px / ent["px"] - 1
            trades.append({**ent, "out_t": b[i]["t"], "out": px, "kind": "stop", "ret": round(100 * r, 2), "pnl": round(100 * ent["m"] * r, 2),
                           "why_out": f"{nyt(b[i]['t'])}(纽约) 最低 {lo:.2f} 跌破 4×ATR 止损 {stop:.2f}" + ("，跳空低开按开盘价成交" if op < stop else "") + "；之后等 EMA7 先跌回 EMA25 下方再进",
                           "bars": i - ent["i"]})
            eq *= 1 + ent["m"] * r; d = 0; lock = 1
        mark = eq * (1 + ent["m"] * (b[i]["c"] / ent["px"] - 1)) if d else eq
        curve.append([b[i]["t"], round(mark, 5)])
        if i < SLOW:
            continue
        want = 1 if ef[i] > es[i] else 0
        if ny_weekend(b[i]["t"]):
            if want != d and not (lock and want == lock):
                skipped.append({"t": b[i]["t"], "want": want})
            continue
        if lock and want != lock:
            lock = 0
        if lock and want == lock:
            want = 0
        if want != d:
            pend = want
            t_close = nyt(b[i]["t"] + H1)
            if want:
                pend_why = (f"{t_close}(纽约) 1h收盘 EMA7 {ef[i]:.2f} 上穿 EMA25 {es[i]:.2f} → 下一根开盘开多；"
                            f"信号K线 ADX {X[i]:.1f} → 仓位 {adx_size(X[i]):g} 倍；止损 = 开仓价 − 4×ATR({A[i]:.2f})")
            else:
                pend_why = f"{t_close}(纽约) 1h收盘 EMA7 {ef[i]:.2f} 下穿 EMA25 {es[i]:.2f} → 下一根开盘平多"
    last = len(b) - 1
    openp = None
    if d:
        r = c[last] / ent["px"] - 1
        openp = {**ent, "out_t": None, "out": c[last], "kind": "open", "ret": round(100 * r, 2), "pnl": round(100 * ent["m"] * r, 2),
                 "why_out": "持仓中", "bars": last - ent["i"]}
    return {"trades": trades, "open": openp, "eq": eq, "curve": curve, "skipped": skipped, "ef": ef, "es": es, "A": A, "X": X,
            "pend": pend, "pend_why": pend_why if pend is not None else "", "lock": lock, "pos": d, "stop": stop if d else None}


def stats(curve, t0=None):
    v = [x for x in curve if t0 is None or x[0] >= t0]
    if len(v) < 2:
        return {}
    pk = 0; dd = 0
    for _, x in v:
        pk = max(pk, x); dd = max(dd, 1 - x / pk)
    return {"ret": round(100 * (v[-1][1] / v[0][1] - 1), 1), "max_dd": round(100 * dd, 1)}


LIVE_FILE = "/var/lib/sndk_solo/live_C.json"     # live/sndk_fills.py 以 binanceC 身份只读导出的 C 账户真实成交


def merge_live(allt):
    """真实成交配到回测的那一笔：实盘开仓时间落在回测开仓K线开始后 3 小时内(执行器先挂 maker 最多 15 分钟)。
    偏差按回测口径算：开仓 = 实盘均价相对回测开仓价(信号后那根开盘价)，平仓 = 回测平仓价相对实盘均价(正数=实盘更好)。"""
    try:
        with open(LIVE_FILE, "r", encoding="utf-8") as f:
            L = json.load(f)
    except Exception:
        return None
    armed = L.get("armed_at") or ARMED_AT
    used = set(); rows = []
    for lt in L.get("trades", []):
        m = None
        for k, bt in enumerate(allt):
            if k not in used and bt["t"] <= lt["in_t"] < bt["t"] + 3 * H1:
                m = k; break
        r = {"in_t": lt["in_t"], "in_avg": lt["in_avg"], "qty": lt["in_qty"], "out_t": lt.get("out_t"), "out_avg": lt.get("out_avg"),
             "out_type": lt.get("out_type"), "fee": lt["fee"], "pnl": lt["pnl"], "open": lt.get("open"), "bt": m}
        if m is not None:
            used.add(m); bt = allt[m]
            r["in_slip_bp"] = round((lt["in_avg"] / bt["px"] - 1) * 1e4, 1) if lt["in_avg"] else None
            if lt.get("out_avg") and bt.get("out_t"):
                r["out_slip_bp"] = round((lt["out_avg"] / bt["out"] - 1) * 1e4, 1)
            bt["live"] = len(rows)
        rows.append(r)
    missed = [k for k, bt in enumerate(allt) if bt["t"] >= armed and k not in used]
    eq0 = L.get("arm_equity")
    transfer_net = float(L.get("transfer_net") or 0.0)
    equity = float(L.get("equity") or 0.0)
    equity_adj = equity - transfer_net
    return {"ts": L.get("ts"), "equity": equity, "equity_adjusted": round(equity_adj, 4), "transfer_net": round(transfer_net, 6),
            "transfer_count": L.get("transfer_count", 0), "transfer_ts": L.get("transfer_ts"), "transfer_ok": L.get("transfer_ok", False), "arm_equity": eq0, "armed_at": armed,
            "raw_eq_pct": round(100 * (equity / eq0 - 1), 2) if eq0 and equity else None,
            "eq_pct": round(100 * (equity_adj / eq0 - 1), 2) if eq0 and equity_adj else None,
            "position": L.get("position"), "stop_px": L.get("stop_px"), "trades": rows, "missed_bt": missed,
            "unmatched": [i for i, r in enumerate(rows) if r["bt"] is None],
            "fees": round(sum(r["fee"] for r in rows), 4), "foreign": len(L.get("foreign_execs") or [])}


def build():
    b = fetch_bars()
    R = replay_full(b)
    allt = R["trades"] + ([R["open"]] if R["open"] else [])
    for t in allt:
        t.pop("i", None)
    live = merge_live(allt)
    closed = R["trades"]; wins = [t for t in closed if t["ret"] > 0]
    bh0 = b[1]["o"]; bh = [[x["t"], round(x["c"] / bh0, 5)] for x in b[1:]]
    rows = [[x["t"], x["o"], x["h"], x["l"], x["c"], round(R["ef"][i], 3), round(R["es"][i], 3), round(R["X"][i], 1)] for i, x in enumerate(b)]
    last = b[-1]
    res = {
        "asof": int(time.time()), "symbol": SYM, "name": "SNDK单票方案 v3", "armed_at": ARMED_AT, "list_start": LIST_START,
        "rules": "1h K线收盘 EMA7>EMA25 持多、<平多(只做多)；开仓价−4×ATR(14) 交易所止损不动；止损后等EMA转空一次再进；"
                 "仓位按金叉K线ADX：<20 1倍 / 20~30 0.75倍 / ≥30 0.5倍；纽约周六日K线不判断(止损照常)。收盘信号、下一根开盘成交。",
        "rows": rows, "trades": allt, "skipped": R["skipped"],
        "summary": {"n": len(closed), "win": round(100 * len(wins) / len(closed), 1) if closed else None,
                    "bt_ret": round(100 * (R["eq"] - 1), 1), "bt": stats(R["curve"]), "bh": stats(bh),
                    "since_armed": stats(R["curve"], ARMED_AT), "bh_since_armed": stats(bh, ARMED_AT),
                    "n_stop": sum(1 for t in closed if t["kind"] == "stop"),
                    "avg_ret": round(sum(t["pnl"] for t in closed) / len(closed), 2) if closed else None,
                    "best": max((t["pnl"] for t in closed), default=None), "worst": min((t["pnl"] for t in closed), default=None)},
        "state": {"pos": R["pos"], "stop": round(R["stop"], 2) if R["stop"] else None, "pend": R["pend"], "pend_why": R["pend_why"], "lock": R["lock"],
                  "bar_t": last["t"], "close": last["c"], "ema7": round(R["ef"][-1], 2), "ema25": round(R["es"][-1], 2),
                  "atr": round(R["A"][-1], 2), "adx": round(R["X"][-1], 1), "size_now": adx_size(R["X"][-1])},
        "curve": R["curve"], "bh_curve": bh, "live": live,
    }
    p = os.path.join(OUT, "sndk_solo.json")
    with open(p + ".tmp", "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False)
    os.replace(p + ".tmp", p)
    return p


def ensure():
    """页面请求时调用：超过 TTL 才重算(只拉增量K线，一次公开接口请求)。"""
    p = os.path.join(OUT, "sndk_solo.json")
    try:
        live_newer = os.path.getmtime(LIVE_FILE) > os.path.getmtime(p)       # 实盘成交刚导出过就跟着重算
    except OSError:
        live_newer = False
    if not os.path.exists(p) or live_newer or time.time() - os.path.getmtime(p) > TTL:
        try:
            build()
        except Exception:
            if not os.path.exists(p):
                raise
    return p


if __name__ == "__main__":
    print(build())
    d = json.load(open(os.path.join(OUT, "sndk_solo.json"), encoding="utf-8"))
    print(d["summary"], d["state"])
