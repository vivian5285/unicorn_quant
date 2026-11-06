# -*- coding: utf-8 -*-
"""实盘 vs 回测 复刻归因(2026-11-06 Claude)。只读。每小时 :29 跑。

问题：现有 planf_track 量的是"实盘 vs 擂台目标"(很小)；但擂台本身和回测之间的差距没被单独量出来。
这里每天直接拿回测同口径收益当基准，把 实盘 − 回测 拆成四块(四块之和 = 总差距)：
  信号时机 = 擂台目标仓位的收益 − 回测收益        (擂台开平仓时间/价格和回测不同、比例漂移口径差)
  持仓取整 = 实际持仓的收益 − 目标仓位的收益      (最小一手、漏单、晚成交)
  资金费   = 实盘实收资金费                        (回测曲线不含资金费)
  执行成本 = 实际 − 实际持仓的收益 − 资金费        (手续费、滑点、成交价与标记价差)
回测同口径 = F_no_btc_2026(账户<5000U) 或 F 的日收益 + 突破叠加(实盘参数、只算 11-01 上实盘后开仓、小账户不含BTC)的日收益。
对齐：回测第 k 天的值 ≈ 当天 16:00 UTC(币圈8h最后一根收盘)的净值，所以实盘按 (k-1 16:00, k 16:00] 归到第 k 天；
      美股收盘(约21:00)会错开约5小时，按月累计基本抵消。资金费按币安流水的自然日归。
只统计"回测已算完"的日子(回测 asof 晚于当天 22:00 UTC)。实盘快照(planf_track_hist.jsonl)从 2026-10-30 起。
合理范围(告警)：当月或已结束月份 |实盘−回测| > 1.5%；或连续两个完整月同方向且每月 > 0.5%。告警同一问题 12 小时提醒一次。
输出 optimal_data/replication.json + static/replication.json，页面 /static/overlay_recon.html。
"""
import json
import os
import sys
import time
from collections import defaultdict

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from engine import overlay_core as C  # noqa: E402

OUT = os.path.join(HERE, "optimal_data")
STATIC = os.path.join(HERE, "static")
D = 86400000
INCOME_E = "/var/lib/planf/income_E.json"
STATUS_E = "/var/lib/planf/live_E_status.json"
OVERLAY_LIVE_MS = 1793491200000          # 2026-11-01 00:00 UTC
BAND_MONTH, BAND_STREAK = 1.5, 0.5       # %
REMIND_SEC = 12 * 3600


def _rd(p, d=None):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return d


def _wr(p, obj):
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, p)


def vol_scale(opt):
    """按 engine/optimal.vol_control 同口径重算每天的组合波动率缩放(截至昨天 60 天实际波动，年化 40% 目标，只降不加)。"""
    import math
    cv = lambda k: {int(t) // D: v for t, v in opt["series"][k]["series"]}
    cs = [cv("crypto"), cv("stock")]; days = sorted(set().union(*[set(c) for c in cs])); last = [None, None]; hist = []; out = {}
    for d in days:
        r = 0.0
        for j, c in enumerate(cs):
            if d in c:
                if last[j]:
                    r += c[d] / last[j] - 1
                last[j] = c[d]
        s = 1.0
        if len(hist) >= 60:
            w = hist[-60:]; m = sum(w) / 60; sd = math.sqrt(sum((x - m) ** 2 for x in w) / 59) * math.sqrt(365)
            s = min(1.0, 0.40 / sd) if sd > 0 else 1.0
        hist.append(r); out[d] = s
    return out


def bt_fracs_factory(opt, use_btc):
    """回测在任一时刻每个品种应持的净仓位/净值(和发布器、执行器同口径，按回测开仓价漂移)。"""
    year = time.gmtime().tm_year
    W = {int(y): dict(w) for y, w in (opt.get("crypto_weights") or {}).items()}
    if not use_btc:
        for y, w in W.items():
            t = sum(v for s, v in w.items() if s != "BTCUSDT")
            W[y] = {s: v / t for s, v in w.items() if s != "BTCUSDT"} if t else {}
    VS = vol_scale(opt)
    scv = {int(t) // D: float(v) for t, v in opt["series"]["stock"]["series"]}
    sk = sorted(scv)
    stock_eq = lambda day: scv.get(day) or next((scv[k] for k in reversed(sk) if k <= day), None)
    trades = [t for t in opt["trades"] if t.get("entry_t")]

    def fr(ts, px):
        ms = ts * 1000; day = int(ms // D); vs = VS.get(day, VS.get(day - 1, 1.0)); out = defaultdict(float)
        for t in trades:
            if not (t["entry_t"] <= ms and (t.get("open") or (t.get("exit_t") and ms < t["exit_t"]))):
                continue
            side = 1 if t["side"] == "LONG" else -1
            if t["symbol"].endswith("USDT"):
                sym = t["symbol"]
                if sym == "BTCUSDT" and not use_btc:
                    continue
                p = px.get(sym)
                if not p:
                    continue
                g = p / float(t["entry"]); a = float(t.get("alloc") or 1)
                den = 1 + side * a * (g - 1)                     # 子腿净值相对开仓时的变化
                w = W.get(time.gmtime(ms / 1000).tm_year, {}).get(sym, 0.0)
                out[sym] += side * w * float(t.get("w") or 1 / 3) * a * g / (den if den > 0.05 else 1.0) * vs
            else:
                sym = t["symbol"] + "USDT"; p = px.get(sym)
                if not p:
                    continue
                e0 = stock_eq(int(t["entry_t"] // D) - 1) or 1.0; e1 = stock_eq(day) or e0
                out[sym] += side * float(t.get("alloc") or 0) * (p / float(t["entry"])) / (e1 / e0) * vs
        return out
    return fr


def main():
    now = time.time()
    opt = _rd(os.path.join(OUT, "optimal.json"), {})
    use_btc = bool((_rd(STATUS_E, {}) or {}).get("use_btc"))
    key = "F" if use_btc else "F_no_btc_2026"
    F = {int(t) // D: float(v) for t, v in opt["series"][key]["series"]}
    asof = float(opt.get("asof") or 0)
    # 叠加：实盘参数，只算上实盘后开仓，小账户不含 BTC
    kc = os.path.join(OUT, "overlay_kcache")
    b4 = {s: C.klines(s, "4h", kc, C.DATA_START_MS) for s in C.SYMS}
    b8 = {s: C.klines(s, "8h", kc, C.DATA_START_MS) for s in C.SYMS}
    OV = defaultdict(float)
    for (strat, sym), trs in C.live_trades(b4, b8, OVERLAY_LIVE_MS).items():
        if sym == "BTCUSDT" and not use_btc:
            continue
        w = C.LIVE_W["brk_tp" if strat.endswith("tp_4h") else "brk_trail" if strat.endswith("trail_4h") else "mio"]
        for t in trs:
            C.add_daily(OV, t, w)
    # 实盘快照：相邻两次之间的 目标/持仓/实际 收益
    H = []
    for line in open(os.path.join(OUT, "planf_track_hist.jsonl"), encoding="utf-8"):
        try:
            H.append(json.loads(line))
        except Exception:
            pass
    H.sort(key=lambda h: h["ts"])
    inc = _rd(INCOME_E, {}) or {}
    transfers = [(float(e.get("time") or 0) / 1000, float(e.get("amount") or e.get("income") or 0)) for e in (inc.get("transfer_events") or [])]
    seg = []
    bt_fr = bt_fracs_factory(opt, use_btc)
    sym_timing = defaultdict(float)                      # 每个品种累计的"信号时机"贡献(占净值)
    for a, b in zip(H, H[1:]):
        if not a.get("eq") or b["ts"] - a["ts"] > 3 * 3600:
            continue
        tm = pm = 0.0
        pxa = {s: r.get("px") for s, r in (a.get("rows") or {}).items() if r.get("px")}
        bfa = bt_fr(a["ts"], pxa)                         # 回测此刻每个品种应持比例(只算有实盘价格的品种)
        for s, r in (a.get("rows") or {}).items():
            rb = (b.get("rows") or {}).get(s)
            if not rb or not r.get("px") or not rb.get("px"):
                continue
            ch = rb["px"] / r["px"] - 1
            tm += r["tu"] * ch; pm += r["pu"] * ch
            sym_timing[s] += (r["tu"] / a["eq"] - bfa.get(s, 0.0)) * ch
        tr = sum(x for t, x in transfers if a["ts"] < t <= b["ts"])
        seg.append({"ts": b["ts"], "target": tm / a["eq"], "held": pm / a["eq"], "actual": (b["eq"] - a["eq"] - tr) / a["eq"]})
    fund_day = {k: float(v.get("FUNDING_FEE") or 0) for k, v in (inc.get("by_day") or {}).items()}
    eq_by_day = {}
    for h in H:
        eq_by_day.setdefault(time.strftime("%Y-%m-%d", time.gmtime(h["ts"])), h["eq"])
    # 按回测日 k 归集：(k-1 16:00, k 16:00]
    first_seg = seg[0]["ts"] if seg else now
    days = []
    for k in sorted(F):
        if k - 1 not in F:
            continue
        w0, w1 = (k - 1) * 86400 + 16 * 3600, k * 86400 + 16 * 3600
        if w0 < first_seg - 3600 or asof < k * 86400 + 22 * 3600 or (H and H[-1]["ts"] < w1 - 1800):
            continue
        x = [s for s in seg if w0 < s["ts"] <= w1]
        if len(x) < 20:
            continue
        tgt = sum(s["target"] for s in x); held = sum(s["held"] for s in x); act = sum(s["actual"] for s in x)
        dstr = time.strftime("%Y-%m-%d", time.gmtime(k * 86400))
        eq0 = eq_by_day.get(dstr) or (H[0]["eq"] if H else 1)
        fund = fund_day.get(dstr, 0.0) / eq0
        bt = F[k] / F[k - 1] - 1 + OV.get(k, 0.0)
        days.append({"day": dstr, "hours": len(x), "actual": act, "bt": bt, "gap": act - bt,
                     "timing": tgt - bt, "rounding": held - tgt, "funding": fund, "exec": act - held - fund})
    def agg(rows):
        s = {k: sum(r[k] for r in rows) for k in ("actual", "bt", "gap", "timing", "rounding", "funding", "exec")}
        return {k: round(100 * v, 3) for k, v in s.items()} | {"days": len(rows)}
    months = defaultdict(list)
    for r in days:
        months[r["day"][:7]].append(r)
    cur_m = time.strftime("%Y-%m", time.gmtime(now))
    mrows = [{"month": m, "partial": m == cur_m, **agg(rs)} for m, rs in sorted(months.items())]
    # 此刻每个品种：擂台目标 vs 回测应持(用最新快照的价格)；回测有而擂台目标里没有的品种用回测日线收盘价
    last = H[-1] if H else {"rows": {}, "eq": 1}
    pxn = {s: r.get("px") for s, r in (last.get("rows") or {}).items() if r.get("px")}
    for t in opt["trades"]:
        if t.get("open"):
            s = t["symbol"] if t["symbol"].endswith("USDT") else t["symbol"] + "USDT"
            if s not in pxn:
                bp = os.path.join(OUT, f"bars_{t['symbol'].replace('USDT', '')}.json") if not t["symbol"].endswith("USDT") else os.path.join(OUT, f"bars_{t['symbol']}.json")
                bb = _rd(bp, [])
                if bb:
                    pxn[s] = float(bb[-1][4])
    bfn = bt_fr(last.get("ts", now), pxn)
    # 回测已经发出、"下一个交易日开盘"执行的美股平仓：过了执行时间就按已平(回测要等当晚收盘才记下)
    for p in ((opt.get("stock") or {}).get("pending") or []):
        if "平仓" not in str(p.get("reason") or ""):
            continue
        tk = p.get("symbol"); bb = _rd(os.path.join(OUT, f"bars_{tk}.json"), [])
        if bb and last.get("ts", now) * 1000 > int(bb[-1][0]) + 24 * 3600 * 1000:
            bfn.pop(f"{tk}USDT", None)
    cmp_rows = []
    for s in sorted(set(bfn) | {s for s, r in (last.get("rows") or {}).items() if abs(r.get("tu") or 0) > 0}):
        arena = (last["rows"].get(s, {}).get("tu") or 0) / last["eq"]
        cmp_rows.append({"symbol": s, "arena_pct": round(100 * arena, 2), "bt_pct": round(100 * bfn.get(s, 0.0), 2),
                         "diff_pct": round(100 * (arena - bfn.get(s, 0.0)), 2), "timing_cum_pct": round(100 * sym_timing.get(s, 0.0), 3)})
    cmp_rows.sort(key=lambda r: -abs(r["diff_pct"]))
    top_timing = sorted(({"symbol": s, "timing_cum_pct": round(100 * v, 3)} for s, v in sym_timing.items()), key=lambda r: r["timing_cum_pct"])[:8]
    res_sym = {"asof_snapshot": int(last.get("ts", now)), "rows": cmp_rows, "timing_by_symbol": top_timing,
               "timing_sum_pct": round(100 * sum(sym_timing.values()), 3),
               "note": "按品种的信号时机 = Σ(擂台目标比例 − 回测应持比例) × 每小时涨跌，自 2026-10-30 起；只覆盖擂台目标里有价格的品种(回测持有而擂台没有的品种算不出逐小时贡献)，所以合计小于总的信号时机。"
                       "此刻对比里，回测已发出、下一交易日开盘执行的美股平仓过了执行时间按已平。"}
    big = [r for r in cmp_rows if abs(r["diff_pct"]) >= 1.0]
    if big:
        res_sym["note"] += " 此刻仓位差 ≥1% 的品种：" + "、".join(f"{r['symbol'].replace('USDT', '')} 擂台{r['arena_pct']:+.1f}%/回测{r['bt_pct']:+.1f}%" for r in big)
    res = {"asof": int(now), "basis": key, "use_btc": use_btc, "since": days[0]["day"] if days else None, "by_symbol": res_sym,
           "total": agg(days) if days else None, "last7": agg(days[-7:]) if days else None, "months": mrows,
           "days": [{k: (round(100 * v, 3) if isinstance(v, float) else v) for k, v in r.items()} for r in days][-60:],
           "band": {"month_pct": BAND_MONTH, "streak_pct": BAND_STREAK},
           "note": "实盘 − 回测同口径 = 信号时机 + 持仓取整 + 资金费 + 执行成本。回测同口径：" + ("方案F含BTC" if use_btc else "方案F不含BTC(账户<5000U)")
                   + " + 突破叠加(实盘参数，11-01 后开仓)。实盘按前一天16:00~当天16:00 UTC归到回测的一天(美股收盘错开约5小时，按月累计基本抵消)；"
                   "只统计回测已算完的日子；实盘快照从 2026-10-30 起。"}
    # 合理范围检查
    bad = []
    for r in mrows:
        if abs(r["gap"]) > BAND_MONTH:
            bad.append(f"{r['month']}{'(至今)' if r['partial'] else ''} 实盘−回测 {r['gap']:+.2f}%(信号时机 {r['timing']:+.2f} / 取整 {r['rounding']:+.2f} / 资金费 {r['funding']:+.2f} / 执行 {r['exec']:+.2f})，超出 ±{BAND_MONTH}%")
    full = [r for r in mrows if not r["partial"]]
    if len(full) >= 2 and all(abs(r["gap"]) > BAND_STREAK for r in full[-2:]) and (full[-1]["gap"] > 0) == (full[-2]["gap"] > 0):
        bad.append(f"连续两个月({full[-2]['month']}、{full[-1]['month']})实盘都{'跑赢' if full[-1]['gap'] > 0 else '跑输'}回测超过 {BAND_STREAK}%")
    res["band"]["ok"] = not bad; res["band"]["problems"] = bad
    _wr(os.path.join(OUT, "replication.json"), res); _wr(os.path.join(STATIC, "replication.json"), res)
    sp = os.path.join(OUT, "replication_alert_state.json"); st = _rd(sp, {}) or {}
    pp = os.path.join(OUT, "alerts_pending.json"); pend = _rd(pp, []) or []
    if bad:
        if not st.get("bad") or now - float(st.get("last") or 0) > REMIND_SEC:
            pend.append("【复刻归因·⚠️】" + "；".join(bad)[:500] + "。详情 http://187.77.130.144:8890/static/overlay_recon.html")
            st = {"bad": True, "last": now}
    elif st.get("bad"):
        pend.append("【复刻归因·✅恢复】实盘和回测的差距回到合理范围内"); st = {"bad": False, "last": now}
    _wr(sp, st); _wr(pp, pend)
    t = res["total"] or {}
    print(time.strftime("%F %T"), f"复刻归因 {res['since']}起{t.get('days', 0)}天 实盘{t.get('actual')}% 回测{t.get('bt')}% 差{t.get('gap')}% "
          f"= 时机{t.get('timing')} 取整{t.get('rounding')} 资金费{t.get('funding')} 执行{t.get('exec')} | 范围{'正常' if not bad else '超出'}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
