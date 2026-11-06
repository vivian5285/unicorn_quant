# -*- coding: utf-8 -*-
"""方案F 实盘信号发布器(2026-10-19)。以 stratroster 身份运行，碰不到任何交易所密钥。

每 60 秒：读擂台方案F四条腿(纸面，已和回测对齐)的当前持仓方向 + 开仓时的波动率调仓系数、币种年度权重、组合波动率缩放，
算出"每个品种应持净仓位占账户净值的比例"(带正负号：正=多，负=空)，写到 /var/lib/planf/targets.json。
实盘执行器(binanceE 等账户用户身份运行)只读这个文件，乘以自己账户的净值去下单。

口径(与 engine/optimal.py 回测一致)：
  币圈：比例 = 币种权重 × 1/3 × Σ(三条子腿 方向 × min(1, 波动目标 ÷ 开仓时ATR%))；日线腿波动目标 3%，8h 两腿 3%×√(8/24)
  美股：比例 = min(1, 3% ÷ 开仓时ATR%) ÷ max(本月可交易只数, 5)，美股腿总和 ≤ 1
  再乘组合波动率缩放 scale_now(optimal.json，60 天波动超 40% 才降)，总名义 ≤ 2
同时给出"不含 BTC"版本(BTC 权重按比例分给其余币)：账户净值 < 5000U 时 BTC 0.001 个的最小下单太粗，回测比较这样最接近(比值 5.01 vs 就近取整 4.63)。
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import subprocess
import time

OPT = "/home/stratroster/unicorn_quant/optimal_data"
ARENA_DB = "/home/stratroster/eth-webhook-server/strategy_engine/data/shadow_v2.db"
ROT = "/home/stratroster/eth-webhook-server/strategy_engine/data/sector_rotation.json"
OUT_DIR = "/var/lib/planf"
CRYPTO_LEGS = {"daily_ema_brk_cycle_main5": 0.03, "st8h_cycle_main5": 0.03 * math.sqrt(8 / 24), "boll8h_cycle_main5": 0.03 * math.sqrt(8 / 24)}
STOCK_LEG = "stock_sector_rot6m5_long"
M5 = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]
# 2026-11-01 宝贝拍板：第四批两个候选直接叠加上实盘(回测"两者合并 1%"：方案F 5.26→6.55，回撤18.8%→19.2%)。
# 口径同 youtube_b4/b4_overlay.py(combo_mtm.mtm_daily)：每笔名义仓位 = 权重 × min(1% ÷ 止损距离%, 3)，开仓后数量不变；
# 真假突破拆两条(1.5R止盈 / 2ATR追踪)各占 0.25，布林擠壓 0.5。不乘币种权重、不乘波动率缩放；不受方案F 2倍上限约束(11-01 晚改)。
# 11-01 晚宝贝定 1%→2%(回测 比值7.75 回撤20.0%，前后段 5.97/10.01 都比1%好)：权重翻倍
OVERLAY_LEGS = {"jiasheng_brk_tp_4h": 0.5, "jiasheng_brk_trail_4h": 0.5, "mio_bb_squeeze_8h": 1.0}
OVERLAY_SEEN = os.path.join(OUT_DIR, "overlay_seen.json")   # 每笔第一次看到时的止损距离(追踪止损会把擂台的 stop 改掉，仓位要按开仓那一刻定)
# 边界条件：这个文件存在 = 叠加腿全部不跟(已有叠加仓位由执行器按目标平掉，方案F不受影响)。宝贝手动建/删，或 planf_track 自动建(叠加部分回撤超限)
OVERLAY_OFF = os.path.join(OUT_DIR, "OVERLAY_OFF")


def _rd(p, default=None):
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _bt_entry(opt: dict, rot: dict, strat: str, sym: str, d: int, field: str = "entry"):
    """回测里这条腿同方向持仓的入场价(engine/optimal.py 的 crypto.state / stock.positions)。执行器按回测口径算仓位漂移时用它，
    擂台的入场价会因为对账补开/晚开而不同(2026-10-30 QCOM 擂台165.3、回测180.49)。回测没有同方向持仓就返回 None(执行器退回用擂台入场价)。"""
    side = "LONG" if d > 0 else "SHORT"
    if strat == STOCK_LEG:
        tk = (rot.get("ticker_of") or {}).get(sym) or (sym[:-4] if sym.endswith("USDT") else sym)
        p = next((x for x in ((opt.get("stock") or {}).get("positions") or []) if x.get("symbol") == tk), None)
        return float(p[field]) if p and p.get(field) else None
    st = ((opt.get("crypto") or {}).get("state") or {}).get(sym) or {}
    v = st.get("position") if strat == "daily_ema_brk_cycle_main5" else \
        (st.get("subs") or {}).get("8h超级趋势" if strat == "st8h_cycle_main5" else "8h布林突破")
    if field == "stop" and strat == "daily_ema_brk_cycle_main5":
        # 日线腿的止损只在回测交易记录里(突破单/底部反转单才有，均线跟上后撤掉)，state.position 里没有：从持仓中的那笔交易取
        t = next((x for x in (opt.get("trades") or []) if x.get("symbol") == sym and x.get("open") and x.get("sub") == "日线EMA8/30"), None)
        return float(t["stop"]) if t and t.get("stop") and not (t.get("brk") is False) else None
    return float(v[field]) if v and v.get("side") == side and v.get(field) else None


def build() -> dict:
    now = time.time(); problems = []
    cw = (_rd(os.path.join(OPT, "crypto_weights.json"), {}) or {}).get("current") or {}
    if not cw:
        problems.append("读不到币种权重")
    opt = _rd(os.path.join(OPT, "optimal.json"), {}) or {}
    vs = float(((opt.get("vol_control") or {}).get("scale_now")) or 1.0)
    if now - float(opt.get("asof") or 0) > 12 * 3600:
        problems.append("回测结果超过12小时没更新(波动率缩放可能过期)")
    rot = _rd(ROT, {}) or {}
    recon_src = _rd(os.path.join(OPT, "recon_override.json"), {}) or {}
    recon_flat = []
    def pending_flat(strat, sym):
        o = (recon_src.get(strat) or {}).get(sym) or {}
        try:
            return str(o.get("want") or "").upper() == "FLAT" and now - float(o.get("ts") or 0) <= 48 * 3600
        except (TypeError, ValueError):
            return False
    k = max(len(rot.get("active_symbols") or []), 5)
    arena_ok = subprocess.run(["systemctl", "is-active", "--quiet", "strategy-roster"]).returncode == 0
    if not arena_ok:
        problems.append("擂台(strategy-roster)没在运行，信号可能过期")
    con = sqlite3.connect(f"file:{ARENA_DB}?mode=ro", uri=True, timeout=10)
    rows = con.execute("select strategy, symbol, side, entry, atr0, stop, created_at from shadow_positions_v2 where status='open' and strategy in (?,?,?,?)",
                       (*CRYPTO_LEGS, STOCK_LEG)).fetchall()
    con.close()
    legs, crypto_raw, stock = [], {}, {}
    for strat, sym, side, entry, atr0, stop, created in rows:
        d = 1 if str(side).upper() == "LONG" else -1
        atrp = (float(atr0) / float(entry)) if entry and atr0 else None
        if strat in CRYPTO_LEGS:
            alloc = min(1.0, CRYPTO_LEGS[strat] / atrp) if atrp else 1.0
            crypto_raw[sym] = crypto_raw.get(sym, 0.0) + d * alloc / 3.0
        else:
            alloc = min(1.0, 0.03 / atrp) if atrp else 1.0
            stock[sym] = stock.get(sym, 0.0) + d * alloc / k
        # 交易所止损单用的价位：币圈腿用擂台的止损(突破/底部单3×ATR，其余10×ATR兜底)；美股腿只挂10×ATR兜底
        # (3×ATR止损只在纽约常规时段由擂台判断，24小时挂在交易所会被夜盘插针误触发)
        xstop = stop
        if strat == STOCK_LEG and entry and atr0:
            xstop = float(entry) - 10.0 * float(atr0)
        is_flat = pending_flat(strat, sym)
        if is_flat:
            recon_flat.append(f"{strat}|{sym}")
        legs.append({"strategy": strat, "symbol": sym, "side": "LONG" if d > 0 else "SHORT", "entry": entry, "alloc": round(alloc, 4), "recon_flat": is_flat,
                     "stop": stop, "exchange_stop": xstop, "since": created, "atr0": atr0, "leg_id": f"{strat}|{sym}|{created}",
                     "bt_entry": _bt_entry(opt, rot, strat, sym, d), "bt_stop": _bt_entry(opt, rot, strat, sym, d, "stop")})
    st_gross = sum(abs(v) for v in stock.values())
    if st_gross > 1.0:
        stock = {s: v / st_gross for s, v in stock.items()}
    def crypto_with(weights):
        return {s: weights.get(s, 0.0) * v for s, v in crypto_raw.items() if weights.get(s, 0.0)}
    w_nobtc = {s: v for s, v in cw.items() if s != "BTCUSDT"}
    tot = sum(w_nobtc.values()) or 1.0
    w_nobtc = {s: v / tot for s, v in w_nobtc.items()}
    def finish(cr):
        t = {**{s: v * vs for s, v in cr.items()}, **{s: v * vs for s, v in stock.items()}}
        g = sum(abs(v) for v in t.values())
        if g > 2.0:
            t = {s: v * 2.0 / g for s, v in t.items()}
        return {s: round(v, 6) for s, v in t.items() if abs(v) > 1e-9}
    # 每条腿自己的比例(给交易所止损单定数量：止损只减这条腿的那一份，不会把整个净仓平掉)
    g_all = sum(abs(v) for v in {**{s: v * vs for s, v in crypto_with(cw).items()}, **{s: v * vs for s, v in stock.items()}}.values())
    g_nb = sum(abs(v) for v in {**{s: v * vs for s, v in crypto_with(w_nobtc).items()}, **{s: v * vs for s, v in stock.items()}}.values())
    for L in legs:
        s, d = L["symbol"], (1 if L["side"] == "LONG" else -1)
        if L["strategy"] in CRYPTO_LEGS:
            base = d * L["alloc"] / 3.0 * vs
            L["frac"] = round(base * cw.get(s, 0.0) * (2.0 / g_all if g_all > 2 else 1), 6)
            L["frac_no_btc"] = round(base * w_nobtc.get(s, 0.0) * (2.0 / g_nb if g_nb > 2 else 1), 6)
        else:
            f_ = d * L["alloc"] / k * (1 / st_gross if st_gross > 1 else 1) * vs
            L["frac"] = round(f_ * (2.0 / g_all if g_all > 2 else 1), 6); L["frac_no_btc"] = round(f_ * (2.0 / g_nb if g_nb > 2 else 1), 6)
    # 2026-10-19 今天的盘中突破单(回测 optimal.json 算好的触发价)：实盘提前挂交易所条件单，触价即成交，不再等擂台每小时检查(实测平均晚36分钟、多付22bp)
    brk = []
    daily_open = {L["symbol"] for L in legs if L["strategy"] == "daily_ema_brk_cycle_main5"}
    for s, stt in ((opt.get("crypto") or {}).get("state") or {}).items():
        o = stt.get("brk_order")
        if not o or s in daily_open:
            continue
        d = 1 if o["side"] == "LONG" else -1; atrp = float(stt.get("atr_pct") or 0) / 100
        base = d * float(o["alloc"]) / 3.0 * vs
        brk.append({"symbol": s, "side": o["side"], "level": float(o["level"]), "for_day": o.get("for_day"),
                    "stop": round(float(o["level"]) * (1 - d * 3.0 * atrp), 8) if atrp else None,
                    "frac": round(base * cw.get(s, 0.0), 6), "frac_no_btc": round(base * w_nobtc.get(s, 0.0), 6)})
    t_all, t_nb = finish(crypto_with(cw)), finish(crypto_with(w_nobtc))
    # A fresh FLAT repair suppresses only that strategy leg from targets. Keep its leg record so catastrophe stops remain active until the live close.
    for L in legs:
        if not L.get("recon_flat"):
            continue
        for key, tg in (("frac", t_all), ("frac_no_btc", t_nb)):
            v = float(L.get(key) or 0.0)
            if not v:
                continue
            new_v = round(float(tg.get(L["symbol"], 0.0)) - v, 6)
            if abs(new_v) <= 1e-9:
                tg.pop(L["symbol"], None)
            else:
                tg[L["symbol"]] = new_v
    # 2026-11-01 叠加腿(第四批候选)：擂台纸面开着的仓 → 按开仓时止损距离定比例，加到目标和腿里(执行器照常下单、挂交易所止损)
    con = sqlite3.connect(f"file:{ARENA_DB}?mode=ro", uri=True, timeout=10)
    orows = con.execute("select id, strategy, symbol, side, entry, atr0, stop, created_at, tp1_price from shadow_positions_v2 where status='open' and strategy in (%s)"
                        % ",".join("?" * len(OVERLAY_LEGS)), tuple(OVERLAY_LEGS)).fetchall()
    con.close()
    seen = _rd(OVERLAY_SEEN, {}) or {}; ov_legs = []
    for pid, strat, sym, side, entry, atr0, stop, created, tp1 in orows:
        d = 1 if str(side).upper() == "LONG" else -1
        rd_ = seen.get(str(pid)) or (abs(float(entry) - float(stop)) / float(entry) if entry and stop else None)
        if not rd_:
            problems.append(f"叠加腿 {strat} {sym} 没有止损，不跟"); continue
        seen[str(pid)] = rd_
        f_ = d * OVERLAY_LEGS[strat] * min(0.01 / rd_, 3.0)
        ov_legs.append({"strategy": strat, "symbol": sym, "side": "LONG" if d > 0 else "SHORT", "entry": entry, "alloc": 0.0,
                        "stop": stop, "exchange_stop": stop, "exchange_tp": tp1 or None, "since": created, "atr0": atr0, "bt_entry": None, "bt_stop": None,
                        "overlay": True, "leg_id": f"{strat}|{sym}|{created}",
                        "risk_dist": round(rd_, 5), "frac": f_, "frac_no_btc": 0.0 if sym == "BTCUSDT" else f_})
    tmp = OVERLAY_SEEN + ".tmp"                      # 不删旧记录：planf_track 用它算已平叠加单的 R(开仓止损距离)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(seen, f)
    os.replace(tmp, OVERLAY_SEEN)
    if os.path.exists(OVERLAY_OFF):
        ov_legs = []
    for key, tg in (("frac", t_all), ("frac_no_btc", t_nb)):
        # 11-01 晚宝贝：叠加在各种行情下都为正，不要被方案F的2倍上限压住(回测本来就没压)。叠加名义回测中位1%、90%分位0.24、最大1.33倍；
        # 总量的最后防线在执行器 MAX_GROSS=3.0(方案F≤2 + 叠加历史最大1.33)
        for L in ov_legs:
            L[key] = round(L[key], 6)
            if L[key]:
                tg[L["symbol"]] = round(tg.get(L["symbol"], 0.0) + L[key], 6)
        for s in [s for s, v in tg.items() if abs(v) <= 1e-9]:
            tg.pop(s)
    legs += ov_legs
    return {"ts": now, "asof": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now)) + " UTC", "ok": not problems, "problems": problems, "recon_flat": recon_flat,
            "brk_orders": brk,
            "vol_scale": vs, "coin_weights": cw, "coin_weights_no_btc": {s: round(v, 4) for s, v in w_nobtc.items()}, "stock_slots": k,
            "btc_min_equity": 5000, "targets": t_all, "targets_no_btc": t_nb, "overlay_legs": sorted(OVERLAY_LEGS), "overlay_off": os.path.exists(OVERLAY_OFF),
            "stock_symbols": sorted(stock), "legs": legs,
            # 方案F 管辖的全部品种(5个币 + 美股池里币安可交易的)。执行器只碰这些，其它持仓一律不动只告警
            "universe": sorted(set(M5) | {s for lst in (rot.get("tradable") or {}).values() for s in lst}),
            "note": "比例=应持净仓位÷账户净值(正多负空)。账户净值<btc_min_equity 用 targets_no_btc。"}


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    while True:
        try:
            res = build()
        except Exception as e:
            res = {"ts": time.time(), "ok": False, "problems": [f"发布器出错 {type(e).__name__}: {e}"], "targets": {}, "targets_no_btc": {}}
        tmp = os.path.join(OUT_DIR, "targets.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
        os.chmod(tmp, 0o644)
        os.replace(tmp, os.path.join(OUT_DIR, "targets.json"))
        # 2026-11-04 整点后3分钟内每5秒一刷(擂台整点+5秒先跑方案F各腿)，其余时间60秒且对齐到整点+3秒
        _m = time.time() % 3600
        time.sleep(5 if _m < 180 else min(60, 3600 - _m + 3))


if __name__ == "__main__":
    main()
