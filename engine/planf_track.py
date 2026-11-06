# -*- coding: utf-8 -*-
"""2026-10-30 方案F 实盘跟踪(回测 → 擂台 → 实盘E)，每小时随 recon_guard.sh 跑一次，只读。

宝贝定的目标："回测结果落实于实盘"。这里把三层偏差每小时量一遍、留历史、超阈值发钉钉：
  1 回测↔擂台：持仓状态一致率(对账 recon)、修复后新出现的"擂台多开/漏开"笔数。
  2 擂台↔实盘：每个品种 实际持仓 vs 目标仓位、交易所止损单覆盖率。
  3 实盘收益拆解(相邻两次快照之间)：
       目标模型 = Σ 目标仓位 × 涨跌       ← 完全按目标持有该赚多少
       持仓模型 = Σ 实际仓位 × 涨跌       ← 按实际持仓该赚多少
       实际     = 账户净值变化
     持仓偏差成本 = 持仓模型 - 目标模型(漏单、晚成交、取整)；
     执行成本     = 实际 - 持仓模型(手续费、滑点、资金费、成交价和标记价的差)。
  另附：账户已实现盈亏/手续费/资金费(live/planf_income.py 每小时拉)，以及同期回测方案F的涨跌做背景
  (E账户不做BTC、资金小，两者不会完全一样，看的是长期差距是否稳定)。
结果写 optimal_data/planf_track.json，快照历史 planf_track_hist.jsonl；告警写 alerts_pending.json(12小时提醒一次，恢复时报恢复)。
"""
from __future__ import annotations

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, "optimal_data")
STATUS = "/var/lib/planf/live_E_status.json"
TARGETS = "/var/lib/planf/targets.json"
INCOME = "/var/lib/planf/income_E.json"
# 2026-11-01 叠加腿(第四批候选：真假突破4h两条 + 布林擠壓8h)直接上实盘。边界条件：叠加部分自己的回撤(按擂台已平单×实盘比例算)
# 回测"两者合并 1%"这一部分最大回撤 4.4%：超过 5% 告警，超过 9%(≈2倍)自动建 OVERLAY_OFF 停掉叠加(方案F不受影响，宝贝删文件恢复)
OVERLAY_SEEN = "/var/lib/planf/overlay_seen.json"
OVERLAY_OFF = "/var/lib/planf/OVERLAY_OFF"
OVERLAY_W = {"jiasheng_brk_tp_4h": 0.5, "jiasheng_brk_trail_4h": 0.5, "mio_bb_squeeze_8h": 1.0}   # 11-01 晚 2%档
OVERLAY_START = 1793520000          # 2026-11-01 叠加上实盘(之后擂台开的叠加单才算；擂台这几条今天才上线)
OVERLAY_WARN, OVERLAY_KILL = 0.10, 0.18   # 2%档：回测叠加自身最大回撤8.6%，10%告警、18%(≈2倍)自动停
ARENA_DB = "/home/stratroster/eth-webhook-server/strategy_engine/data/shadow_v2.db"
HIST = os.path.join(OUT, "planf_track_hist.jsonl")
LIVE_START = 1792407600            # 2026-10-19 10:59 UTC 宝贝建 ARMED，E 账户进入实盘
LIVE_START_EQ = 659.83
FIX_TS = 1793346700                # 2026-10-30 07:52 UTC 一致性修复上线(擂台风控豁免/开仓pending/FLAT空仓)
REMIND_SEC = 12 * 3600
DEV_TOL, COVER_MIN, GAP7_ALERT = 0.25, 0.80, -0.01
CRYPTO = {"BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"}


def _rd(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _wr(path, obj):
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(path + ".tmp", path)


def _hist():
    out = []
    try:
        for line in open(HIST, encoding="utf-8"):
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    except FileNotFoundError:
        pass
    return out


def snapshot(st):
    rows = {r["symbol"]: {"tu": float(r.get("target_usdt") or 0), "pu": float(r.get("position_usdt") or 0), "px": float(r.get("price") or 0),
                          "tq": float(r.get("target_qty") or 0), "pq": float(r.get("position") or 0)} for r in st.get("rows") or []}
    return {"ts": float(st["ts"]), "eq": float(st["equity"]), "rth": bool(st.get("rth")), "rows": rows}


def attribute(h, transfer_events=()):
    """相邻快照逐段拆解收益；按截至快照的USDT净划转校正权益，避免充值/提现进入执行偏差。"""
    seg = []
    def flow_at(ts):
        return sum(float(x.get("income") or 0.0) for x in transfer_events if float(x.get("time") or 0) / 1000 <= ts)
    for a, b in zip(h, h[1:]):
        if b["ts"] - a["ts"] > 2.5 * 3600:
            continue
        aeq = float(a["eq"]) - flow_at(a["ts"])
        beq = float(b["eq"]) - flow_at(b["ts"])
        if aeq <= 0:
            continue
        tm = pm = 0.0
        for s, r in a["rows"].items():
            p1 = (b["rows"].get(s) or {}).get("px")
            if not p1 or not r["px"]:
                continue
            ch = p1 / r["px"] - 1
            tm += r["tu"] * ch; pm += r["pu"] * ch
        seg.append({"ts": b["ts"], "target": tm / aeq, "held": pm / aeq, "actual": beq / aeq - 1})
    return seg


def window(seg, since):
    x = [s for s in seg if s["ts"] >= since]
    tot = {k: sum(s[k] for s in x) for k in ("target", "held", "actual")}
    return {"hours": len(x), "target_pct": round(100 * tot["target"], 3), "held_pct": round(100 * tot["held"], 3), "actual_pct": round(100 * tot["actual"], 3),
            "pos_gap_pct": round(100 * (tot["held"] - tot["target"]), 3), "exec_gap_pct": round(100 * (tot["actual"] - tot["held"]), 3),
            "total_gap_pct": round(100 * (tot["actual"] - tot["target"]), 3)}


def backtest_change(since, use_btc=None):
    o = _rd(os.path.join(OUT, "optimal.json"), {}) or {}
    out = {}
    for k in ("F", "F_no_btc_2026", "crypto", "stock"):
        s = ((o.get("series") or {}).get(k) or {}).get("series") or []
        a = [v for t, v in s if t / 1000 >= since - 86400]
        if len(a) >= 2:
            out[k] = round(100 * (a[-1] / a[0] - 1), 2)
    out["E_use_btc"] = use_btc
    out["E_comparable"] = out.get("F") if use_btc is True else (out.get("F_no_btc_2026") if use_btc is False else None)
    out["asof"] = (o.get("data_last_bar") or o.get("asof"))
    return out


def recon_layer(now):
    r = _rd(os.path.join(OUT, "recon.json"), {}) or {}
    rh = _rd(os.path.join(OUT, "recon_health.json"), {}) or {}
    h = rh.get("history") or []
    def rate(since):
        x = [y for y in h if y["ts"] >= since]; n = sum(y["n"] for y in x)
        return round(100 * sum(y["ok"] for y in x) / n, 1) if n else None
    pairs, extra, missed, bad, pending = [], 0, 0, [], []
    for p in r.get("pairs") or []:
        e = [x for x in p.get("extra") or [] if x["t"] / 1000 >= FIX_TS]; m = [x for x in p.get("missed") or [] if x["t"] / 1000 >= FIX_TS]
        extra += len(e); missed += len(m)
        # 2026-10-31 回测已发信号、下一个开盘才执行(对账器本来就跳过)——不算不一致，单列待执行(10-31 QCOM 周末误报)
        for s in p.get("state") or []:
            if s["ok"]:
                continue
            t_ = f"{p['name']} {s['symbol'].replace('USDT', '')} 擂台{s['arena']}/回测{s['backtest']}"
            (pending if "下一个开盘执行" in (s.get("note") or "") else bad).append(t_)
        pairs.append({"name": p["name"], "ok": p.get("state_ok"), "n": p.get("state_n"), "extra_after_fix": len(e), "missed_after_fix": len(m),
                      "avg_diff_bp": p.get("avg_diff_bp")})
    return {"natural_24h": rate(now - 86400), "natural_7d": rate(now - 7 * 86400), "natural_since_fix": rate(FIX_TS),
            "extra_after_fix": extra, "missed_after_fix": missed, "mismatch_now": bad, "pending_next_open": pending, "pairs": pairs}


def exec_layer(st):
    pos = {r["symbol"]: r for r in st.get("rows") or []}
    cover = {}
    for s in st.get("stops") or []:
        cover[s["symbol"]] = cover.get(s["symbol"], 0.0) + float(s.get("qty") or 0)
    rows, dev_bad, cov_bad = [], [], []
    for s, r in pos.items():
        tq, pq = float(r.get("target_qty") or 0), float(r.get("position") or 0)
        dev = abs(pq - tq) / abs(tq) if tq else (0.0 if not pq else 1.0)
        cov = cover.get(s, 0.0) / abs(pq) if pq else None
        stock_closed = s not in CRYPTO and not st.get("rth")
        rows.append({"symbol": s, "target": tq, "position": pq, "dev_pct": round(100 * dev, 1), "stop_cover_pct": None if cov is None else round(100 * cov, 1),
                     "note": "美股休市，开盘后再调" if stock_closed and dev > DEV_TOL else ""})
        if dev > DEV_TOL and abs(pq - tq) * float(r.get("price") or 0) >= 5 and not stock_closed:
            dev_bad.append(f"{s.replace('USDT', '')} 目标{tq:g}/实际{pq:g}")
        if cov is not None and cov < COVER_MIN:
            cov_bad.append(f"{s.replace('USDT', '')} 止损覆盖{100 * cov:.0f}%")
    au = st.get("audit") or {}
    return {"rows": rows, "dev_bad": dev_bad, "cover_bad": cov_bad, "audit_today": au}


def leg_layer():
    """2026-10-31 每条腿的止损/入场是否和回测同口径(targets.json 里发布器给的 bt_entry / bt_stop)。
    10-30 查出两类：日线腿对账补开时默认挂了3×ATR(回测趋势单不设止损)、美股腿3×ATR按擂台自己的入场价算(回测入场价不同)。
    规则：币圈腿 回测有止损(突破/底部单) → 交易所止损应≈回测止损(差>1%报)；回测没有止损 → 只应有10×ATR兜底(离入场<9×ATR报)。"""
    tg = _rd(TARGETS, {}) or {}
    rows, bad = [], []
    for L in tg.get("legs") or []:
        if L.get("overlay"):
            continue                                   # 叠加腿没有回测口径，止损就是擂台的(真假突破箱中线/追踪、布林中轨)
        e, a0, xs, bs, be = (float(L.get(k) or 0) for k in ("entry", "atr0", "exchange_stop", "bt_stop", "bt_entry"))
        gap = round(100 * (e / be - 1), 2) if be else None
        note = ""
        if L["strategy"] != "stock_sector_rot6m5_long" and xs > 0:
            if bs > 0 and abs(xs / bs - 1) > 0.01:
                note = f"止损{xs:.6g}≠回测{bs:.6g}"
            elif not bs and a0 > 0 and abs(xs - e) / a0 < 9:
                note = f"止损离入场只有{abs(xs - e) / a0:.1f}×ATR，回测这笔没有止损(应只留10×ATR兜底)"
        if note:
            bad.append(f"{L['strategy'].split('_')[0]} {L['symbol'].replace('USDT', '')} {note}")
        rows.append({"strategy": L["strategy"], "symbol": L["symbol"], "side": L["side"], "entry": e, "bt_entry": be or None,
                     "entry_gap_pct": gap, "exchange_stop": xs, "bt_stop": bs or None, "note": note})
    return {"rows": rows, "bad": bad}


def overlay_layer():
    """叠加腿已平单按实盘比例(权重×min(1%÷开仓止损距离,3))折成占净值的盈亏，累加成曲线看回撤。"""
    import sqlite3
    seen = _rd(OVERLAY_SEEN, {}) or {}
    try:
        con = sqlite3.connect(f"file:{ARENA_DB}?mode=ro", uri=True, timeout=10)
        rows = con.execute("select id, strategy, symbol, side, entry, exit_price, closed_at from shadow_positions_v2 where status!='open' and created_at>=? "
                           "and strategy in (%s) order by closed_at" % ",".join("?" * len(OVERLAY_W)), (OVERLAY_START, *OVERLAY_W)).fetchall()
        n_open = con.execute("select count(*) from shadow_positions_v2 where status='open' and strategy in (%s)" % ",".join("?" * len(OVERLAY_W)),
                             tuple(OVERLAY_W)).fetchone()[0]
        con.close()
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}", "dd": 0.0, "cum": 0.0, "n": 0}
    cum = peak = dd = 0.0; trades = []
    for pid, strat, sym, side, entry, exitp, closed in rows:
        rd_ = seen.get(str(pid))
        if not rd_ or not entry or not exitp:
            continue
        d = 1 if str(side).upper() == "LONG" else -1
        pnl = OVERLAY_W[strat] * min(0.01 / rd_, 3.0) * d * (float(exitp) / float(entry) - 1) - OVERLAY_W[strat] * min(0.01 / rd_, 3.0) * 0.0013
        cum += pnl; peak = max(peak, cum); dd = max(dd, peak - cum)
        trades.append({"strategy": strat, "symbol": sym, "pnl_pct": round(100 * pnl, 3), "closed_at": closed})
    off = os.path.exists(OVERLAY_OFF)
    if dd > OVERLAY_KILL and not off:
        open(OVERLAY_OFF, "w").write(f"auto {time.strftime('%F %T')} 叠加回撤 {dd:.1%} > {OVERLAY_KILL:.0%}" + chr(10)); off = True
    return {"n": len(trades), "open": n_open, "cum_pct": round(100 * cum, 2), "dd_pct": round(100 * dd, 2), "off": off, "recent": trades[-10:],
            "dd": dd, "cum": cum}


def main():
    now = time.time()
    st = _rd(STATUS)
    if not st or now - float(st.get("ts") or 0) > 900:
        res = {"ts": now, "ok": False, "error": "实盘执行器状态超过15分钟没更新"}
        _wr(os.path.join(OUT, "planf_track.json"), res); print(time.strftime("%F %T"), res["error"], flush=True); return
    snap = snapshot(st)
    h = _hist()
    if not h or snap["ts"] - h[-1]["ts"] > 600:
        with open(HIST, "a", encoding="utf-8") as f:
            f.write(json.dumps(snap, ensure_ascii=False) + "\n")
        h.append(snap)
    inc = _rd(INCOME, {}) or {}
    transfer_ok = bool(inc.get("transfer_ok"))
    transfer_events = (inc.get("transfer_events") or []) if transfer_ok else []
    transfer_net = float(inc.get("transfer_net") or 0.0) if transfer_ok else None
    adjusted_eq = snap["eq"] - transfer_net if transfer_ok else None
    raw_return = round(100 * (snap["eq"] / LIVE_START_EQ - 1), 2) if LIVE_START_EQ else None
    flow_return = round(100 * (adjusted_eq / LIVE_START_EQ - 1), 2) if adjusted_eq is not None and LIVE_START_EQ else None
    seg = attribute(h, transfer_events)
    rec = recon_layer(now); ex = exec_layer(st); lg = leg_layer(); ov = overlay_layer()
    res = {"ts": now, "ok": True, "equity": snap["eq"],
           "since_live": {"start": LIVE_START, "start_eq": LIVE_START_EQ, "eq_change_pct": flow_return,
                          "raw_eq_change_pct": raw_return, "equity_adjusted": round(adjusted_eq, 4) if adjusted_eq is not None else None,
                          "transfer_net": round(transfer_net, 6) if transfer_net is not None else None,
                          "transfer_count": inc.get("transfer_count"), "transfer_ok": transfer_ok,
                          "income": inc.get("total"), "income_by_day": inc.get("by_day"), "backtest_pct": backtest_change(LIVE_START, st.get("use_btc"))},
           "attr": {"24h": window(seg, now - 86400), "7d": window(seg, now - 7 * 86400), "all": window(seg, 0),
                    "first_snapshot": h[0]["ts"] if h else None},
           "recon": rec, "exec": ex, "legs": lg, "overlay": {k: v for k, v in ov.items() if k not in ("dd", "cum")}}
    _wr(os.path.join(OUT, "planf_track.json"), res)
    # 告警
    checks = [("track_dev", not ex["dev_bad"], "实盘持仓偏离目标超过25%：" + "；".join(ex["dev_bad"])),
              ("track_cover", not ex["cover_bad"], "交易所止损单没盖住持仓：" + "；".join(ex["cover_bad"])),
              ("track_recon", not rec["mismatch_now"], "回测↔擂台持仓不一致：" + "；".join(rec["mismatch_now"][:4])),
              ("track_legs", not lg["bad"], "止损和回测口径不一致：" + "；".join(lg["bad"][:4])),
              ("track_overlay", ov.get("dd", 0) <= OVERLAY_WARN, f"叠加腿(真假突破+布林擠壓)回撤 {ov.get('dd', 0):.1%}，超过回测最大8.6%的告警线10%"
               + ("；已超18%，自动停用叠加(建了 OVERLAY_OFF，方案F照常；删掉该文件恢复)" if ov.get("off") else "")),
              ("track_gap7", res["attr"]["7d"]["hours"] < 24 or res["attr"]["7d"]["total_gap_pct"] / 100 > GAP7_ALERT,
               f"近7天实盘比目标模型少赚 {-res['attr']['7d']['total_gap_pct']:.2f}%(持仓偏差 {res['attr']['7d']['pos_gap_pct']:.2f}%、执行成本 {res['attr']['7d']['exec_gap_pct']:.2f}%)")]
    sp = os.path.join(OUT, "planf_track_alert_state.json"); sst = _rd(sp, {}) or {}
    pp = os.path.join(OUT, "alerts_pending.json"); pend = _rd(pp, []) or []
    for key, ok, text in checks:
        prev = sst.get(key) or {}
        if not ok:
            # 持仓偏差/对账不一致要连续两次(≥1小时)才报，避免刚出信号、正在挂单的那一会儿误报
            streak = int(prev.get("streak") or 0) + 1
            if streak >= 2 and (not prev.get("bad") or now - float(prev.get("last") or 0) > REMIND_SEC):
                pend.append(f"【方案F实盘跟踪·⚠️】{text}"); sst[key] = {"bad": True, "last": now, "streak": streak}
            else:
                sst[key] = {**prev, "streak": streak}
        else:
            if prev.get("bad"):
                pend.append(f"【方案F实盘跟踪·✅恢复】{text.split('：')[0]}已恢复正常")
            sst[key] = {"bad": False, "last": now, "streak": 0}
    _wr(sp, sst); _wr(pp, pend)
    a = res["attr"]["24h"]
    print(time.strftime("%F %T"), f"实盘跟踪 净值{snap['eq']:.2f} 24h 目标{a['target_pct']}% 实际{a['actual_pct']}% 差{a['total_gap_pct']}% | 对账不一致{len(rec['mismatch_now'])} 偏离{len(ex['dev_bad'])} 止损缺{len(ex['cover_bad'])}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
