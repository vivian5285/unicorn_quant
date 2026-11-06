# -*- coding: utf-8 -*-
"""突破叠加 实盘 ↔ 回测 逐笔对账(每小时)。2026-11-06 Claude 写。只读：不改擂台/发布器/执行器的任何东西。

三层：
 ① 回测信号(engine/overlay_core.py，实盘同一套参数) ↔ 擂台开仓(shadow_v2.db)：漏单 / 多单 / 开仓价差 / 止损距离差 / 平仓方式和R
 ② 擂台开着的叠加单 ↔ 发布器 targets.json：该跟的有没有进目标(OVERLAY_OFF、小账户不做BTC 除外)
 ③ 累计表现：实盘叠加已平单的R、累计收益 vs 回测同期，和回测历史每笔平均R对照
结果：optimal_data/overlay_live_recon.json + static/overlay_recon.json(页面 /static/overlay_recon.html)
告警：写 alerts_pending.json(recon_guard.sh 每小时 :35 发钉钉)；同一问题连续 2 次才报，12 小时提醒一次，恢复时报恢复。
"""
import json
import os
import sqlite3
import sys
import time
from collections import defaultdict

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from engine import overlay_core as C  # noqa: E402

OUT = os.path.join(HERE, "optimal_data")
STATIC = os.path.join(HERE, "static")
CACHE = os.path.join(OUT, "overlay_kcache")
ARENA_DB = "/home/stratroster/eth-webhook-server/strategy_engine/data/shadow_v2.db"
TARGETS = "/var/lib/planf/targets.json"
STATUS_E = "/var/lib/planf/live_E_status.json"
OVERLAY_SEEN = "/var/lib/planf/overlay_seen.json"
OVERLAY_OFF = "/var/lib/planf/OVERLAY_OFF"
ROTATION = "/home/stratroster/eth-webhook-server/strategy_engine/data/sector_rotation.json"
LIVE_START = 1793520000                     # 2026-11-01 08:00 UTC，同 planf_track.OVERLAY_START
LEGS = {"jiasheng_brk_tp_4h": ("4h", "brk_tp"), "jiasheng_brk_trail_4h": ("4h", "brk_trail"), "mio_bb_squeeze_8h": ("8h", "mio")}
NAMES = {"jiasheng_brk_tp_4h": "真假突破4h·1.5R止盈", "jiasheng_brk_trail_4h": "真假突破4h·2ATR追踪", "mio_bb_squeeze_8h": "布林擠壓8h"}
PRICE_BP_WARN = 30                           # 开仓价差超过 30bp 记"价差大"
GRACE_BARS = 2                               # 信号后 2 根K线内擂台没开 → 算漏
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


def ts(ms):
    return time.strftime("%m-%d %H:%M", time.gmtime(ms / 1000)) if ms else None


def _ms(x):
    if not x:
        return None
    x = float(x)
    return int(x if x > 1e11 else x * 1000)


def main():
    now = time.time()
    # 回测历史每笔平均R(2022起，实盘参数)，做对照基准
    hist_start = 1640995200000
    hb4 = b4 = {s: C.klines(s, "4h", CACHE, C.DATA_START_MS) for s in C.SYMS}
    hb8 = b8 = {s: C.klines(s, "8h", CACHE, C.DATA_START_MS) for s in C.SYMS}
    hist = C.live_trades(hb4, hb8, hist_start)
    exp_R = {}
    for strat in LEGS:
        rs = [C.trade_R(t) for (st, _), tr in hist.items() if st == strat for t in tr if not t.get("open")]
        exp_R[strat] = {"n": len(rs), "avg_R": round(sum(rs) / len(rs), 3) if rs else None,
                        "win": round(sum(1 for r in rs if r > 0) / len(rs), 3) if rs else None}

    bt = C.live_trades(b4, b8, LIVE_START * 1000)
    con = sqlite3.connect(f"file:{ARENA_DB}?mode=ro", uri=True, timeout=10)
    rows = con.execute("select id, strategy, symbol, side, entry, stop, status, entry_bar_time, exit_price, exit_bar_time, exit_reason, created_at, closed_at, tp1_price "
                       "from shadow_positions_v2 where strategy in (?,?,?) and created_at >= ? order by created_at", (*LEGS, LIVE_START)).fetchall()
    con.close()
    seen = _rd(OVERLAY_SEEN, {}) or {}
    arena = [{"id": r[0], "strategy": r[1], "symbol": r[2], "side": str(r[3]).upper(), "entry": r[4], "stop": r[5], "status": r[6],
              "sig_t": _ms(r[7]), "exit": r[8], "exit_t": _ms(r[9]), "exit_reason": r[10], "created": r[11], "closed": r[12],
              "rd": seen.get(str(r[0])) or (abs(r[4] - r[5]) / r[4] if r[4] and r[5] else None)} for r in rows]
    used = set(); trades = []; missed = []; extra = []
    for (strat, sym), tr in sorted(bt.items()):
        step = C.STEP[LEGS[strat][0]]
        for t in tr:
            side = "LONG" if t["d"] > 0 else "SHORT"
            m = next((a for a in arena if a["id"] not in used and a["strategy"] == strat and a["symbol"] == sym and a["side"] == side
                      and a["sig_t"] is not None and abs(a["sig_t"] - t["sig_t"]) <= step), None)
            rowd = {"strategy": strat, "name": NAMES[strat], "symbol": sym, "side": side, "bt_sig": ts(t["sig_t"]), "bt_entry": round(t["entry"], 6),
                    "bt_rd": round(t["rd"], 5), "bt_exit": round(t["exit"], 6), "bt_exit_t": ts(t["exit_t"]), "bt_why": t["why"],
                    "bt_R": round(C.trade_R(t), 3) if not t.get("open") else None, "bt_open": bool(t.get("open"))}
            if m:
                used.add(m["id"])
                pbp = 1e4 * (m["entry"] - t["entry"]) / t["entry"] * (1 if side == "LONG" else -1)   # 正=实盘开得更贵(吃亏)
                live_R = None
                if m["status"] != "open" and m["exit"] and m["rd"]:
                    d = 1 if side == "LONG" else -1
                    live_R = round((d * (m["exit"] / m["entry"] - 1) - 0.0013) / m["rd"], 3)
                rowd.update(status="配上", arena_id=m["id"], live_entry=m["entry"], live_sig=ts(m["sig_t"]), price_bp=round(pbp, 1),
                            live_rd=round(m["rd"], 5) if m["rd"] else None, live_open=m["status"] == "open",
                            live_exit=m["exit"], live_exit_t=ts(m["exit_t"]), live_exit_reason=m["exit_reason"], live_R=live_R,
                            flags=[f for f, ok in (("开仓价差大", abs(pbp) <= PRICE_BP_WARN),
                                                   ("止损距离不同", not m["rd"] or abs(m["rd"] / t["rd"] - 1) <= 0.15),
                                                   ("平仓状态不同", (m["status"] == "open") == bool(t.get("open")))) if not ok])
            elif now * 1000 - t["sig_t"] > (GRACE_BARS + 1) * step:
                rowd.update(status="漏单", flags=["回测有、擂台没开"]); missed.append(rowd)
            else:
                rowd.update(status="等待", flags=[])
            trades.append(rowd)
    for a in arena:
        if a["id"] not in used:
            extra.append({"strategy": a["strategy"], "name": NAMES[a["strategy"]], "symbol": a["symbol"], "side": a["side"], "status": "多单",
                          "live_sig": ts(a["sig_t"]), "live_entry": a["entry"], "live_open": a["status"] == "open", "flags": ["擂台开了、回测没有"]})
    # ② 擂台 → 发布器目标
    tg = _rd(TARGETS, {}) or {}; stt = _rd(STATUS_E, {}) or {}
    use_btc = bool(stt.get("use_btc")); off = os.path.exists(OVERLAY_OFF)
    tg_ids = {L.get("leg_id"): L for L in (tg.get("legs") or []) if L.get("overlay")}
    pub_bad = []
    for a in arena:
        if a["status"] != "open":
            continue
        lid = f"{a['strategy']}|{a['symbol']}|{a['created']}"
        L = tg_ids.get(lid) or next((x for x in tg_ids.values() if x.get("strategy") == a["strategy"] and x.get("symbol") == a["symbol"]), None)
        skip = off or (a["symbol"] == "BTCUSDT" and not use_btc)
        if not skip and (not L or not (L.get("frac") if use_btc else L.get("frac_no_btc"))):
            pub_bad.append(f"{NAMES[a['strategy']]} {a['symbol']} {a['side']}")
    # ③ 累计表现
    closed_live = [t for t in trades if t.get("live_R") is not None]
    by_leg = defaultdict(list)
    for t in closed_live:
        by_leg[t["strategy"]].append(t["live_R"])
    perf = {s: {"live_n": len(by_leg[s]), "live_avg_R": round(sum(by_leg[s]) / len(by_leg[s]), 3) if by_leg[s] else None,
                "bt_hist_avg_R": exp_R[s]["avg_R"], "bt_hist_n": exp_R[s]["n"], "bt_hist_win": exp_R[s]["win"]} for s in LEGS}
    bt_pnl = defaultdict(float)
    for (strat, sym), tr in bt.items():
        for t in tr:
            if sym == "BTCUSDT" and not use_btc:
                continue
            C.add_daily(bt_pnl, t, C.LIVE_W[LEGS[strat][1]])
    live_pnl = 0.0
    for a in arena:
        if a["status"] != "open" and a["exit"] and a["rd"] and not (a["symbol"] == "BTCUSDT" and not use_btc):
            d = 1 if a["side"] == "LONG" else -1; w = C.LIVE_W[LEGS[a["strategy"]][1]]
            live_pnl += w * min(0.01 / a["rd"], 3.0) * (d * (a["exit"] / a["entry"] - 1) - 0.0013)
    res = {"asof": int(now), "live_start": LIVE_START, "use_btc": use_btc, "overlay_off": off,
           "summary": {"bt_signals": len(trades), "matched": sum(1 for t in trades if t["status"] == "配上"), "waiting": sum(1 for t in trades if t["status"] == "等待"),
                       "missed": len(missed), "extra": len(extra), "publisher_missing": pub_bad,
                       "flagged": sum(1 for t in trades if t["status"] == "配上" and t["flags"]),
                       "bt_cum_pct": round(100 * sum(bt_pnl.values()), 3), "live_closed_cum_pct": round(100 * live_pnl, 3)},
           "perf": perf, "trades": trades + extra}
    _wr(os.path.join(OUT, "overlay_live_recon.json"), res)
    _wr(os.path.join(STATIC, "overlay_recon.json"), res)
    # 告警
    checks = [("ovr_missed", not missed, "回测有、擂台没开的叠加单：" + "；".join(f"{t['name']} {t['symbol']} {t['side']} 信号{t['bt_sig']}" for t in missed[:4])),
              ("ovr_extra", not extra, "擂台开了、回测没有的叠加单：" + "；".join(f"{t['name']} {t['symbol']} {t['side']} {t['live_sig']}" for t in extra[:4])),
              ("ovr_pub", not pub_bad, "擂台开着但没进实盘目标的叠加单：" + "；".join(pub_bad[:4])),
              ("ovr_flag", not any(t["status"] == "配上" and t["flags"] for t in trades),
               "叠加单和回测细节不一致：" + "；".join(f"{t['name']} {t['symbol']} {'/'.join(t['flags'])}" for t in trades if t["status"] == "配上" and t["flags"])[:300])]
    # 美股换仓名单是否过期(擂台换月闸门在名单过期时会拦普通开仓，只放行对账器的强制开仓)。
    # sector_rotation 每天 22:40 UTC 跑，每月 1 日 22:40 起应切到当月；computed_at 超过 26 小时 = 任务没在跑。
    rot = _rd(ROTATION, {}) or {}
    g = time.gmtime(now); cur_m = "%04d-%02d" % (g.tm_year, g.tm_mon)
    rot_bad = []
    if str(rot.get("month") or "") < cur_m and (g.tm_mday > 1 or g.tm_hour >= 23):
        rot_bad.append(f"名单月份 {rot.get('month')} 落后于当前 {cur_m}")
    if now - float(rot.get("computed_at") or 0) > 26 * 3600:
        rot_bad.append(f"名单 {(now - float(rot.get('computed_at') or 0)) / 3600:.0f} 小时没更新")
    checks.append(("rot_stale", not rot_bad, "美股行业轮动名单过期，擂台暂停按名单开新美股仓(对账器强制开仓照常)：" + "；".join(rot_bad)
                   + "。检查 eth-webhook-server crontab 的 strategy_engine.sector_rotation(22:40 UTC)"))
    sp = os.path.join(OUT, "overlay_recon_alert_state.json"); sst = _rd(sp, {}) or {}
    pp = os.path.join(OUT, "alerts_pending.json"); pend = _rd(pp, []) or []
    for key, ok, text in checks:
        prev = sst.get(key) or {}
        if not ok:
            streak = int(prev.get("streak") or 0) + 1
            if streak >= 2 and (not prev.get("bad") or now - float(prev.get("last") or 0) > REMIND_SEC):
                pend.append(f"【实盘对账·⚠️】{text}"); sst[key] = {"bad": True, "last": now, "streak": streak}
            else:
                sst[key] = {**prev, "streak": streak}
        else:
            if prev.get("bad"):
                pend.append(f"【实盘对账·✅恢复】{text.split('：')[0]}已恢复")
            sst[key] = {"bad": False, "last": now, "streak": 0}
    _wr(sp, sst); _wr(pp, pend)
    s_ = res["summary"]
    print(time.strftime("%F %T"), f"叠加对账 回测信号{s_['bt_signals']} 配上{s_['matched']} 等待{s_['waiting']} 漏{s_['missed']} 多{s_['extra']} "
          f"未进目标{len(pub_bad)} 细节不符{s_['flagged']} | 回测同期{s_['bt_cum_pct']:+.2f}% 实盘已平{s_['live_closed_cum_pct']:+.2f}%", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
