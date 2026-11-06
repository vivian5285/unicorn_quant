# -*- coding: utf-8 -*-
"""擂台 ↔ 回测 自动对账修复器(2026-10-19 宝贝："不光提醒，要成立能自己修复的系统")。cron 每小时跑一次。

流程：recon.run() 比持仓状态 → 对每个不一致的(策略, 品种)判断能不能安全修 → 写/删修复指令(recon_override.json)
      → 擂台策略下一轮扫描按指令开/平(走擂台自己的下单、手续费、净值结算) → 一致后指令自动删除。
安全规则：
  · 持续两次检查(≥50分钟)才动；"回测已发信号、下一根开盘才执行"的不算偏差。
  · 擂台有仓、回测没仓：只有回测是在擂台开仓之后算出来的(回测见过同一根K线)才平，否则等回测刷新。
  · 回测有仓、擂台没仓：价格还没越过回测止损才补开；今年权重为0的币不补。
  · 每天最多 6 次新修复；optimal_data/recon_autofix_off 存在就只记录不修。
  · 只改纸面仓位，绝不改策略代码；同一个(策略,品种)一周内被修 ≥3 次 → 记"疑似代码问题"，停止对它自动修复。
统计：自然一致率(修复前看到的一致比例) 和 修复次数分开记，纸面验证用自然一致率。
"""
from __future__ import annotations

import json
import os
import sqlite3
import time

from . import recon
from .optimal import OUT

OVR = "/home/stratroster/unicorn_quant/optimal_data/recon_override.json"
WATCH = os.path.join(OUT, "recon_watch.json")
LOG = os.path.join(OUT, "recon_actions.json")
HEALTH = os.path.join(OUT, "recon_health.json")
OFF = os.path.join(OUT, "recon_autofix_off")
DB = "/home/stratroster/eth-webhook-server/strategy_engine/data/shadow_v2.db"
MAX_PER_DAY, PERSIST_S, SUSPECT_N = 6, 50 * 60, 3
LEG = {"daily_ema_brk_cycle_main5": ("币圈", "日线EMA8/30"), "st8h_cycle_main5": ("币圈", "8h超级趋势"),
       "boll8h_cycle_main5": ("币圈", "8h布林突破"), "stock_sector_rot6m5_long": ("美股", None)}


def _j(p, default):
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _w(p, obj):
    with open(p + ".tmp", "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(p + ".tmp", p)


LAST_TF = {"daily_ema_brk_cycle_main5": ("1d", 86400), "st8h_cycle_main5": ("8h", 28800), "boll8h_cycle_main5": ("8h", 28800)}


def _bt_stale(opt, strat, sym, now):
    """回测用的K线还没包含最近一根已收盘K线 → 回测结论过期，不能拿来修擂台(2026-10-29 BNB 误发开空指令)"""
    if strat not in LAST_TF:
        return False
    tf, period = LAST_TF[strat]
    t = ((opt.get("data_last_bar") or {}).get(sym) or {}).get(tf)
    return not t or t / 1000 < now // period * period - period


def _bt_cover_end(opt, strat, sym):
    """回测数据覆盖到哪一刻(毫秒)：币圈腿 = 最后一根已收盘K线的收盘时间；美股腿 = 正股最后一根日线收盘(开盘时间+7小时)。取不到返回 None。"""
    try:
        if strat in LAST_TF:
            tf, period = LAST_TF[strat]
            t = ((opt.get("data_last_bar") or {}).get(sym) or {}).get(tf)
            return int(t) + period * 1000 if t else None
        tk = sym[:-4] if sym.endswith("USDT") else sym
        b = _j(os.path.join(OUT, f"bars_{tk}.json"), [])
        return int(b[-1][0]) + 7 * 3600 * 1000 if b else None
    except Exception:
        return None


def _bt_sym(s):
    return s if s.endswith("USDT") else s + "USDT"


def run() -> dict:
    now = time.time()
    opt = _j(os.path.join(OUT, "optimal.json"), {})
    rc = recon.run()
    asof = float(opt.get("asof") or 0)
    weights = opt.get("crypto_weights_current") or {}
    watch = _j(WATCH, {}); log = _j(LOG, []); ovr_old = _j(OVR, {})
    day0 = now - now % 86400
    acted_today = sum(1 for a in log if a.get("ts", 0) >= day0 and a.get("kind") == "修复")
    week = [a for a in log if a.get("ts", 0) >= now - 7 * 86400 and a.get("kind") == "修复"]
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    ovr, seen_keys, n_ok, n_all, events = {}, set(), 0, 0, []
    off = os.path.exists(OFF)
    for p in rc.get("pairs", []):
        strat = p["strategy"]
        if strat not in LEG or not p.get("state"):
            continue
        leg, sub = LEG[strat]
        bt_open = {_bt_sym(t["symbol"]): t for t in opt.get("trades", []) if t.get("open") and t["leg"] == leg and (sub is None or t.get("sub") == sub)}
        for row in p["state"]:
            sym = row["symbol"]; key = f"{strat}|{sym}"; n_all += 1; seen_keys.add(key)
            if row["ok"]:
                n_ok += 1; watch.pop(key, None); continue
            if "下一个开盘执行" in (row.get("note") or ""):
                continue
            first = watch.setdefault(key, now)
            want = {"LONG": "LONG", "SHORT": "SHORT"}.get(row["backtest"], "FLAT")
            old = (ovr_old.get(strat) or {}).get(sym)
            if old and old.get("want") == want:                       # 已经下过同样的指令，等擂台执行
                ovr.setdefault(strat, {})[sym] = old; continue
            why_skip = None
            if now - first < PERSIST_S:
                why_skip = "刚发现，等下一次检查确认"
            elif off:
                why_skip = "自动修复已关闭(recon_autofix_off)"
            elif _bt_stale(opt, strat, sym, now):
                why_skip = "回测还没算到最新收盘K线，等回测刷新"
            elif sum(1 for a in week if a.get("key") == key) >= SUSPECT_N:
                why_skip = f"一周内已修 {SUSPECT_N} 次，疑似代码问题，停止自动修复，等人工检查"
            elif acted_today >= MAX_PER_DAY:
                why_skip = f"今天已修 {MAX_PER_DAY} 次，到上限"
            elif leg == "币圈" and want != "FLAT" and float(weights.get(sym, 0.2)) <= 0:
                why_skip = "这个币今年权重为0，回测仓位不计入组合，不补开"
            elif row["arena"] in ("LONG", "SHORT") and want == "FLAT":
                r = con.execute("select max(created_at) from shadow_positions_v2 where strategy=? and symbol=? and status='open'", (strat, sym)).fetchone()
                if r and r[0] and float(r[0]) > asof - 1800:
                    why_skip = "擂台这笔开在回测刷新之后，等下一次回测刷新再判断"
            elif row["arena"] == "空仓" and want != "FLAT":
                # 2026-11-06 Claude 对称保护(用户同意)：擂台在回测数据覆盖之后才平掉的仓(典型：美股常规时段盘中3×ATR止损，
                # 回测要等当天正股日线收盘后才知道)，回测此刻仍显示持有是因为没看到那根K线，不能据此补开——否则当天买回、
                # 回测刷新后又平掉，白做一买一卖(11-06 PFE)。等回测覆盖到擂台平仓那根K线后再按回测判断。
                r = con.execute("select exit_bar_time, closed_at from shadow_positions_v2 where strategy=? and symbol=? and status='closed' "
                                "order by closed_at desc limit 1", (strat, sym)).fetchone()
                ex = float(r[0]) if r and r[0] else (float(r[1]) * 1000 if r and r[1] else None)
                cover = _bt_cover_end(opt, strat, sym)
                if ex and cover and ex >= cover:
                    why_skip = "擂台这笔平在回测数据覆盖之后(如盘中止损)，回测还没看到那根K线，等回测刷新再判断"
            stop = None
            if not why_skip and want != "FLAT":
                t = bt_open.get(sym); stop = t.get("stop") if t else None
                px = None
                try:
                    from urllib.request import urlopen
                    px = float(json.load(urlopen(f"https://fapi.binance.com/fapi/v1/ticker/price?symbol={sym}", timeout=10))["price"])
                except Exception:
                    pass
                if stop and px and ((want == "LONG" and px <= stop) or (want == "SHORT" and px >= stop)):
                    why_skip = f"现价已越过回测止损 {stop:.6g}，不补开"
            if why_skip:
                if "刚发现" not in why_skip:
                    events.append({"ts": now, "kind": "跳过", "key": key, "strategy": strat, "symbol": sym, "arena": row["arena"], "backtest": row["backtest"], "reason": why_skip})
                continue
            reason = f"擂台{row['arena']}/回测{row['backtest']}，持续{int((now - first) / 60)}分钟"
            ovr.setdefault(strat, {})[sym] = {"want": want, "stop": stop, "reason": reason, "ts": now}
            acted_today += 1
            events.append({"ts": now, "kind": "修复", "key": key, "strategy": strat, "symbol": sym, "arena": row["arena"], "backtest": row["backtest"],
                           "action": {"FLAT": "平仓", "LONG": "开多", "SHORT": "开空"}[want] if row["arena"] == "空仓" or want == "FLAT" else f"先平再开{'多' if want == 'LONG' else '空'}",
                           "reason": reason})
    con.close()
    for k in list(watch):
        if k not in seen_keys:
            watch.pop(k, None)
    _w(OVR, ovr); _w(WATCH, watch)
    # 去重：同一 key 的"跳过"只在原因变化时记
    last_skip = {}
    for a in log:
        if a.get("kind") == "跳过": last_skip[a["key"]] = a.get("reason")
    events = [e for e in events if e["kind"] != "跳过" or last_skip.get(e["key"]) != e["reason"]]
    gp = os.path.join(OUT, "guard_events.jsonl")                  # recon_guard.sh 记的服务自动重启
    if os.path.exists(gp):
        try:
            with open(gp, "r", encoding="utf-8") as f:
                for ln in f:
                    ln = ln.strip()
                    if ln:
                        g = json.loads(ln); events.append({"ts": g["ts"], "kind": "重启", "key": "service|" + g["service"], "strategy": "服务", "symbol": g["service"],
                                                           "arena": "", "backtest": "", "action": "自动重启", "reason": f"{g['service']} 不在运行"})
            os.remove(gp)
        except Exception:
            pass
    log = (log + events)[-300:]; _w(LOG, log)
    hist = _j(HEALTH, {}).get("history", [])
    hist = (hist + [{"ts": now, "ok": n_ok, "n": n_all}])[-24 * 30:]
    tot_ok = sum(h["ok"] for h in hist); tot_n = sum(h["n"] for h in hist)
    health = {"ts": now, "natural_ok": n_ok, "natural_n": n_all, "overrides": sum(len(v) for v in ovr.values()),
              "fixed_today": acted_today, "autofix_on": not off, "natural_rate": round(100 * tot_ok / tot_n, 1) if tot_n else None,
              "checks": len(hist), "fixes_total": sum(1 for a in log if a.get("kind") == "修复"), "history": hist}
    _w(HEALTH, health)
    return {k: v for k, v in health.items() if k != "history"} | {"events": events}


if __name__ == "__main__":
    r = run()
    print(time.strftime("%Y-%m-%d %H:%M:%S"), json.dumps({k: v for k, v in r.items() if k != "events"}, ensure_ascii=False),
          " | ".join(f"{e['kind']} {e['symbol']} {e.get('action', '')} {e['reason']}" for e in r["events"]), flush=True)
