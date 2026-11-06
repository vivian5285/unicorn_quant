# -*- coding: utf-8 -*-
"""擂台纸面 vs 回测 对账(2026-10-17，宝贝："擂台纸面验证认真做，把规则执行上的偏差都找出来")。

对每条子腿：擂台策略 ↔ 回测(optimal.json)里对应的交易
  1) 当前持仓状态：同一品种，擂台和回测此刻是否持有同方向仓位(上线当天擂台按"当前状态"直接开仓，所以先看状态)。
  2) 上线后的新交易：按 品种+方向+开仓时间(容差：日线1.5天 / 8h 16小时 / 美股2天) 配对，
     报 回测有擂台没有(漏单)、擂台有回测没有(多单)、配上的开仓价差(bp，正=擂台更差)。
结果写 optimal_data/recon.json，最优方案页"纸面对账"区显示；optimal_run.py 每天跑完回测后顺带跑。
"""
from __future__ import annotations

import json
import os
import sqlite3
import time

from .optimal import OUT

DB = "/home/stratroster/eth-webhook-server/strategy_engine/data/shadow_v2.db"
PAIRS = [  # (擂台策略, 回测里的 leg/sub, 配对容差毫秒, 名称)
    ("daily_ema_brk_cycle_main5", ("币圈", "日线EMA8/30"), int(1.5 * 86400000), "币圈·日线EMA+突破单"),
    ("st8h_cycle_main5", ("币圈", "8h超级趋势"), 16 * 3600000, "币圈·8h超级趋势"),
    ("boll8h_cycle_main5", ("币圈", "8h布林突破"), 16 * 3600000, "币圈·8h布林突破"),
    ("stock_sector_rot6m5_long", ("美股", None), 2 * 86400000, "美股·行业轮动"),
]


def _bt_sym(t):
    s = t["symbol"]
    return s if s.endswith("USDT") else s + "USDT"


def run() -> dict:
    with open(os.path.join(OUT, "optimal.json"), "r", encoding="utf-8") as f:
        opt = json.load(f)
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    out = {"asof": int(time.time()), "pairs": []}
    for strat, (leg, sub), tol, name in PAIRS:
        rows = con.execute("select symbol, side, entry, entry_bar_time, exit_price, exit_bar_time, exit_reason, status, created_at "
                           "from shadow_positions_v2 where strategy=? order by created_at", (strat,)).fetchall()
        bt = [t for t in opt["trades"] if t["leg"] == leg and (sub is None or t.get("sub") == sub)]
        if not rows:
            # 2026-10-18 擂台还没成交时也要比持仓状态：回测里上线前就开着的仓，擂台不会有(只跟上线后的新信号)
            bo = [t for t in bt if t.get("open")]
            if not bo:
                out["pairs"].append({"strategy": strat, "name": name, "status": "擂台上线后还没有新成交，回测这条腿目前也空仓(一致)", "arena_n": 0}); continue
            st_ = [{"symbol": _bt_sym(t), "arena": "空仓", "backtest": t["side"], "ok": False,
                    "note": f"回测这笔开于 {time.strftime('%m-%d', time.gmtime(t['entry_t'] / 1000))}，早于擂台这条腿上线；擂台只跟上线后的新信号，待同步"} for t in bo]
            out["pairs"].append({"strategy": strat, "name": name, "status": "擂台上线后还没有新成交", "arena_n": 0, "since": None,
                                 "state": st_, "state_ok": 0, "state_n": len(st_), "matched": [], "missed": [], "extra": [], "avg_diff_bp": None})
            continue
        t0 = min(r[8] for r in rows) * 1000                        # 擂台这条腿上线(第一笔)时间
        # 1) 当前持仓状态
        a_open = {r[0]: r[1] for r in rows if r[7] == "open"}
        b_open = {_bt_sym(t): t["side"] for t in bt if t.get("open")}
        # 2026-10-18 回测已发出、下一个开盘才执行的平仓/反手，按执行后的状态来比(否则擂台已平、回测还记着持仓，会被误判)
        note = {}
        if leg == "美股":
            for p_ in opt["stock"].get("pending", []):
                s_ = _bt_sym(p_)
                if p_.get("action") == "平仓" and s_ in b_open:
                    b_open.pop(s_); note[s_] = "回测已发平仓信号，下一个开盘执行"
                elif p_.get("action") == "开多" and s_ not in b_open:      # 2026-10-30 开仓信号同理(10-29 QCOM 被误判后反复开平)
                    b_open[s_] = "LONG"; note[s_] = "回测已发开仓信号，下一个开盘执行"
        else:
            for s_, st_ in (opt["crypto"].get("state") or {}).items():
                sd_ = (st_ or {}) if sub == "日线EMA8/30" else (((st_ or {}).get("subs") or {}).get(sub) or {})
                pd_ = sd_.get("pending")
                if s_ in b_open and pd_ in ("exit", "flip"):
                    side_ = b_open.pop(s_); note[s_] = "回测已发" + ("反手" if pd_ == "flip" else "平仓") + "信号，下一个开盘执行"
                    if pd_ == "flip": b_open[s_] = "SHORT" if side_ == "LONG" else "LONG"
                elif s_ not in b_open and pd_ == "enter" and sd_.get("pending_side") in ("LONG", "SHORT"):   # 2026-10-30
                    b_open[s_] = sd_["pending_side"]; note[s_] = "回测已发开仓信号，下一个开盘执行"
        # 擂台持仓、回测空仓：如果回测这只最近是被3ATR止损打掉的，说明擂台这笔是"止损后锁定"修复(2026-10-17 06:55)之前开的遗留单
        last_bt = {}
        for t in bt:
            if not t.get("open") and t.get("exit_t"):
                k_ = _bt_sym(t)
                if k_ not in last_bt or t["exit_t"] > last_bt[k_]["exit_t"]: last_bt[k_] = t
        for r in rows:
            if r[7] == "open" and r[0] not in b_open and r[0] in last_bt and "硬止损" in (last_bt[r[0]].get("exit_reason") or "") and r[8] < 1792220145:
                note[r[0]] = f"回测 {time.strftime('%m-%d', time.gmtime(last_bt[r[0]]['exit_t'] / 1000))} 已被3ATR止损、按规则锁定；擂台这笔是10-17修复前开的遗留单"
        syms = sorted(set(a_open) | set(b_open))
        state = [{"symbol": s, "arena": a_open.get(s, "空仓"), "backtest": b_open.get(s, "空仓"),
                  "ok": a_open.get(s) == b_open.get(s), "note": note.get(s, "")} for s in syms]
        # 2) 上线后的新交易
        a_new = [r for r in rows if r[8] * 1000 > t0 + 3600000]       # 上线第一个小时内的开仓算"状态同步"
        b_new = [t for t in bt if t["entry_t"] > t0 + 3600000]
        used = set(); matched = []; extra = []
        for r in a_new:
            best = None
            for k, t in enumerate(b_new):
                if k in used or _bt_sym(t) != r[0] or t["side"] != r[1]:
                    continue
                dt = abs(int(r[3]) - int(t["entry_t"]))
                if dt <= tol and (best is None or dt < best[1]):
                    best = (k, dt)
            if best:
                used.add(best[0]); t = b_new[best[0]]; d = 1 if r[1] == "LONG" else -1
                matched.append({"symbol": r[0], "side": r[1], "arena_t": int(r[3]), "bt_t": t["entry_t"], "arena_px": r[2], "bt_px": t["entry"],
                                "diff_bp": round(10000 * d * (r[2] / t["entry"] - 1), 1)})
            elif any(_bt_sym(t) == r[0] and t["side"] == r[1] and t.get("open") and t["entry_t"] < t0 for t in bt):
                pass                                               # 2026-10-19 上线状态同步单：对应回测里上线前就开着的仓，不算多单
            else:
                extra.append({"symbol": r[0], "side": r[1], "t": int(r[3]), "px": r[2]})
        missed = [{"symbol": _bt_sym(t), "side": t["side"], "t": t["entry_t"], "px": t["entry"], "reason": t["entry_reason"][:80]}
                  for k, t in enumerate(b_new) if k not in used]
        avg = round(sum(m["diff_bp"] for m in matched) / len(matched), 1) if matched else None
        out["pairs"].append({"strategy": strat, "name": name, "since": int(t0), "arena_n": len(rows), "state": state,
                             "state_ok": sum(1 for x in state if x["ok"]), "state_n": len(state),
                             "matched": matched, "missed": missed, "extra": extra, "avg_diff_bp": avg})
    con.close()
    with open(os.path.join(OUT, "recon.json") + ".tmp", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    os.replace(os.path.join(OUT, "recon.json") + ".tmp", os.path.join(OUT, "recon.json"))
    return out
