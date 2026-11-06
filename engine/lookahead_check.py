# -*- coding: utf-8 -*-
"""2026-10-19 每周前视检验(截断法)，cron 每周日跑，结果写 optimal_data/lookahead_check.json，health.py 读取并告警。

做法：把全部行情截到 T(最近 3 年里 3 个时点 + 半年前)，重跑正式引擎；T 之前的每笔开平仓/仓位、组合净值、币种权重必须和全量运行完全一致。
任何一处不一致 = 引擎用到了未来数据(改代码时引入的)，必须先修再继续。研究版见 research_scripts/lookahead_trunc.py。"""
from __future__ import annotations

import calendar
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.nice(19)
from engine import optimal as O   # noqa: E402

DAY = 86400000
_sd, _s8, _st = O.spot_daily, O.spot_8h, O.stock_daily
CUT = {"t": None}


def _cut(b, hours):
    return b if CUT["t"] is None else [x for x in b if x["t"] + hours * 3600000 <= CUT["t"]]


O.spot_daily = lambda s: _cut(_sd(s), 24)
O.spot_8h = lambda s: _cut(_s8(s), 8)
O.stock_daily = lambda s: _cut(_st(s), 7)
ALL5 = list(O.M5)


def run():
    O.M5[:] = [s for s in ALL5 if len(O.spot_daily(s)) > 60]
    _, ctr, _, _, CD = O.crypto_leg()
    _, tst, _ = O.crypto_8h_leg("st", CD["BTCUSDT"]); _, tbb, _ = O.crypto_8h_leg("bb", CD["BTCUSDT"])
    coin = {s: O.combine([O.CURVES["日线EMA8/30"][s], O.CURVES["8h超级趋势"][s], O.CURVES["8h布林突破"][s]],
                         [O.SUB_W["日线EMA8/30"], O.SUB_W["8h超级趋势"], O.SUB_W["8h布林突破"]]) for s in O.M5}
    last_y = time.gmtime((CUT["t"] or time.time() * 1000) / 1000).tm_year
    WY = O.coin_weights(coin, range(2017, last_y + 1)); cB = O.weighted_basket(coin, WY)
    sc, s_tr, _, _ = O.stock_leg()
    F = O.vol_control([cB, sc], [O.W_CRYPTO, O.W_STOCK], 2.0)[0]
    return {"日线": ctr, "8h超趋": tst, "8h布林": tbb, "美股": s_tr}, F, WY


def main():
    t0 = time.time()
    FULL, FF, WYF = run()
    g = time.gmtime()
    cuts = [calendar.timegm((g.tm_year - k, g.tm_mon, 1, 0, 0, 0)) * 1000 for k in (3, 2, 1)]
    cuts.append(int(time.time() * 1000) - 180 * DAY)
    problems, detail = [], []
    ke = lambda t: (t["symbol"], t["side"], t["entry_t"], round(t["entry"], 6), round(t.get("alloc") or 0, 4))
    kx = lambda t: (t["symbol"], t["entry_t"], t["exit_t"], round(t["exit"], 6))
    for T in cuts:
        CUT["t"] = T; TR, FT, WYT = run(); CUT["t"] = None
        lab = time.strftime("%Y-%m-%d", time.gmtime(T / 1000)); bad = 0
        for leg in FULL:
            a = {ke(t) for t in FULL[leg] if t["entry_t"] < T - 2 * DAY}; b = {ke(t) for t in TR[leg] if t["entry_t"] < T - 2 * DAY}
            ax = {kx(t) for t in FULL[leg] if t.get("exit_t") and t["exit_t"] < T - 2 * DAY}
            bx = {kx(t) for t in TR[leg] if t.get("exit_t") and t["exit_t"] < T - 2 * DAY}
            n = len(a ^ b) + len(ax ^ bx); bad += n
            if n:
                problems.append(f"{lab} {leg} {n}处成交不一致")
        ks = [k for k in FT if k < T // DAY - 2 and k in FF and k >= O.START_DAY]
        if ks:
            mx = max(abs(FT[k] / FT[ks[0]] - FF[k] / FF[ks[0]]) for k in ks)
            if mx > 1e-9:
                problems.append(f"{lab} 净值曲线差 {mx:.2e}"); bad += 1
        wd = max((abs(WYT[y].get(s, 0) - WYF[y].get(s, 0)) for y in WYT if y <= time.gmtime(T / 1000).tm_year for s in ALL5), default=0)
        if wd > 1e-9:
            problems.append(f"{lab} 币种权重差 {wd:.2e}"); bad += 1
        detail.append({"cut": lab, "ok": bad == 0})
    res = {"ts": time.time(), "ok": not problems, "problems": problems, "detail": detail, "sec": round(time.time() - t0)}
    p = os.path.join(HERE, "optimal_data", "lookahead_check.json")
    with open(p + ".tmp", "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    os.replace(p + ".tmp", p)
    print(time.strftime("%Y-%m-%d %H:%M:%S"), "前视检验", "通过" if res["ok"] else "未通过", problems, f"{res['sec']}s", flush=True)


if __name__ == "__main__":
    main()
