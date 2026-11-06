# -*- coding: utf-8 -*-
"""月度 实盘 vs 回测 对账报告(2026-11-06 Claude)。只读。

每个自然月(从 E 账户上实盘 2026-10-19 起)：
  实盘：月初/月末净值(planf_track_hist.jsonl 快照；上实盘那天用 planf_track.LIVE_START_EQ)，扣除划转后的收益；
        币安流水拆分：已实现盈亏 / 手续费 / 资金费(income_E.json，planf_income.py 每小时拉)，其余 = 浮动盈亏变化
  回测同口径：主方案(账户<5000U 用 F_no_btc_2026，否则 F) 当月收益；叠加 11-01 起实盘，加上 overlay_series 当月日收益；
        回测资金费(funding.json by_month)，扣费后收益
  差距 = 实盘 - 回测(扣资金费)
结果：optimal_data/monthly_report.json + static/monthly_report.json(页面 /static/overlay_recon.html 下半部分)；
每月 1 日(UTC)跑完后把上个月的结论发一次钉钉。cron：每天 01:00 UTC。
"""
import calendar
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, "optimal_data")
STATIC = os.path.join(HERE, "static")
D = 86400000
LIVE_START, LIVE_START_EQ = 1792407600, 659.83        # 同 engine/planf_track.py
OVERLAY_LIVE = "2026-11-01"
INCOME_E = "/var/lib/planf/income_E.json"
STATUS_E = "/var/lib/planf/live_E_status.json"


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


def main():
    now = time.time()
    hist = []
    for line in open(os.path.join(OUT, "planf_track_hist.jsonl"), encoding="utf-8"):
        try:
            h = json.loads(line); hist.append((float(h["ts"]), float(h["eq"])))
        except Exception:
            pass
    hist.sort()
    eq_at = lambda ts: next((e for t, e in reversed(hist) if t <= ts), None)
    opt = _rd(os.path.join(OUT, "optimal.json"), {})
    use_btc = bool((_rd(STATUS_E, {}) or {}).get("use_btc"))
    key = "F" if use_btc else "F_no_btc_2026"
    F = {int(t) // D: v for t, v in opt["series"][key]["series"]}
    # 叠加的回测同口径：实盘参数、只算上实盘(11-01)之后开仓的单、小账户不做BTC —— 和实盘真正能做的一致
    sys.path.insert(0, HERE)
    from engine import overlay_core as C
    from collections import defaultdict
    kc = os.path.join(OUT, "overlay_kcache")
    b4 = {s: C.klines(s, "4h", kc, C.DATA_START_MS) for s in C.SYMS}
    b8 = {s: C.klines(s, "8h", kc, C.DATA_START_MS) for s in C.SYMS}
    ov_ms = calendar.timegm(time.strptime(OVERLAY_LIVE, "%Y-%m-%d")) * 1000
    OV = defaultdict(float)
    for (strat, sym), trs in C.live_trades(b4, b8, ov_ms).items():
        if sym == "BTCUSDT" and not use_btc:
            continue
        w = C.LIVE_W["brk_tp" if strat.endswith("tp_4h") else "brk_trail" if strat.endswith("trail_4h") else "mio"]
        for t in trs:
            C.add_daily(OV, t, w)
    ov_end = time.strftime("%Y-%m-%d", time.gmtime(min(b8[s][-1]["t"] for s in C.SYMS) / 1000))
    fd = _rd(os.path.join(OUT, "funding.json"), {}) or {}
    fm = fd.get("by_month") or {}
    inc = _rd(INCOME_E, {}) or {}
    byday = inc.get("by_day") or {}
    tr_events = inc.get("transfer_events") or []
    ov_live_day = calendar.timegm(time.strptime(OVERLAY_LIVE, "%Y-%m-%d")) // 86400

    months = []
    y, m = time.gmtime(LIVE_START)[:2]
    while True:
        m0 = calendar.timegm((y, m, 1, 0, 0, 0)); m1 = calendar.timegm((y + (m == 12), m % 12 + 1, 1, 0, 0, 0))
        if m0 > now:
            break
        a = max(m0, LIVE_START); z = min(m1, now)
        e0 = LIVE_START_EQ if a == LIVE_START else eq_at(a); e1 = eq_at(z)
        tag = "%04d-%02d" % (y, m)
        tr = sum(float(e.get("amount") or 0) for e in tr_events if a * 1000 <= float(e.get("time") or 0) < z * 1000)
        live = (e1 - e0 - tr) / e0 if e0 and e1 else None
        days = [k for k in byday if a - 86400 < calendar.timegm(time.strptime(k, "%Y-%m-%d")) < z]
        flow = {x: round(sum(float(byday[k].get(x) or 0) for k in days), 4) for x in ("REALIZED_PNL", "COMMISSION", "FUNDING_FEE")}
        # 回测：a 之前最后一天 → z 之前最后一天
        da, dz = a * 1000 // D, (z * 1000 - 1) // D                 # dz = 区间内最后一天(不含下个月1日)
        ks = sorted(F)
        fa = next((F[k] for k in reversed(ks) if k <= da - 1), None) or next((F[k] for k in ks if k >= da), None)
        fz = next((F[k] for k in reversed(ks) if k <= dz), None)
        bt_main = fz / fa - 1 if fa and fz else None
        ov_ret = sum(v for d, v in OV.items() if max(da, ov_live_day) <= d <= dz)       # 简单相加(短区间足够)
        f_m = fm.get(tag) or {}
        bt_fund = -(f_m.get("crypto", 0) + f_m.get("stock", 0) + (f_m.get("overlay", 0) if dz >= ov_live_day else 0)) / 100
        bt_net = (bt_main or 0) + ov_ret + bt_fund
        months.append({"month": tag, "from": time.strftime("%m-%d", time.gmtime(a)), "to": time.strftime("%m-%d %H:%M", time.gmtime(z)),
                       "partial": z < m1, "eq0": e0, "eq1": e1, "transfer": round(tr, 2),
                       "live_pct": round(100 * live, 2) if live is not None else None,
                       "live_flow": flow, "live_funding_pct": round(100 * flow["FUNDING_FEE"] / e0, 3) if e0 else None,
                       "live_fee_pct": round(100 * flow["COMMISSION"] / e0, 3) if e0 else None,
                       "bt_main_pct": round(100 * bt_main, 2) if bt_main is not None else None, "bt_overlay_pct": round(100 * ov_ret, 2),
                       "bt_funding_pct": round(100 * bt_fund, 3), "bt_net_pct": round(100 * bt_net, 2),
                       "gap_pct": round(100 * (live - bt_net), 2) if live is not None else None})
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    res = {"asof": int(now), "basis": key, "use_btc": use_btc, "overlay_data_end": ov_end, "months": months,
           "note": f"回测口径：{'含BTC 方案F' if use_btc else '不含BTC(账户<5000U)'} + 突破叠加2%(实盘参数，只算 11-01 上实盘后开仓的单) - 回测资金费估算。"
                   "回测日线数据到最近一次收盘，月中的最后几天可能还没算进去；实盘净值取每小时快照。"}
    _wr(os.path.join(OUT, "monthly_report.json"), res); _wr(os.path.join(STATIC, "monthly_report.json"), res)
    # 每月1日把上个月发一次
    g = time.gmtime(now)
    sp = os.path.join(OUT, "monthly_report_state.json"); st = _rd(sp, {}) or {}
    done = [x for x in months if not x["partial"]]
    if g.tm_mday == 1 and done and st.get("sent") != done[-1]["month"]:
        x = done[-1]
        msg = (f"【月度对账】{x['month']}({x['from']}~月末)：实盘 {x['live_pct']:+.2f}% ｜ 回测同口径 {x['bt_net_pct']:+.2f}%"
               f"(主方案 {x['bt_main_pct']:+.2f}% 叠加 {x['bt_overlay_pct']:+.2f}% 资金费 {x['bt_funding_pct']:+.2f}%) ｜ 差 {x['gap_pct']:+.2f}%；"
               f"实盘手续费 {x['live_fee_pct']:+.3f}% 资金费 {x['live_funding_pct']:+.3f}%。详情 http://187.77.130.144:8890/static/overlay_recon.html")
        pp = os.path.join(OUT, "alerts_pending.json"); pend = _rd(pp, []) or []
        pend.append(msg); _wr(pp, pend); st["sent"] = x["month"]; _wr(sp, st)
    last = months[-1] if months else {}
    print(time.strftime("%F %T"), "月度对账", " | ".join(f"{x['month']} 实盘{x['live_pct']}% 回测{x['bt_net_pct']}% 差{x['gap_pct']}%" for x in months), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
