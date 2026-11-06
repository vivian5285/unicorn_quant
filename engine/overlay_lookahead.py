# -*- coding: utf-8 -*-
"""突破叠加回测的每周前视检验(截断法，2026-11-06 Claude)。同 engine/lookahead_check.py 的思路：
把 4h/8h K线截到时点 T 重跑 engine/overlay_core，T 之前已平仓的每一笔(信号、开平时间、价格)和研究口径日收益必须和全量一致；
任何不一致 = 叠加回测用到了未来数据(改代码时引入的)。结果写 optimal_data/overlay_lookahead.json，不通过就告警。
cron：每周日 03:40 UTC。只读 K线缓存，不联网。"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.nice(15)
from engine import overlay_core as C  # noqa: E402

OUT = os.path.join(HERE, "optimal_data")
CACHE = os.path.join(OUT, "overlay_kcache")
START = 1646092800000          # 2022-03-01


def main():
    t0 = time.time()
    b4 = {s: json.load(open(os.path.join(CACHE, f"k_{s}_4h.json"))) for s in C.SYMS}
    b8 = {s: json.load(open(os.path.join(CACHE, f"k_{s}_8h.json"))) for s in C.SYMS}
    last = min(b8[s][-1]["t"] for s in C.SYMS)
    # 截断点：最近 3 年每年一个 + 半年前 + 两个月前
    cuts = sorted({last - k * 365 * C.D for k in (1, 2, 3)} | {last - 182 * C.D, last - 60 * C.D})
    full = C.live_trades(b4, b8, START)
    fb, fm = C.grid_series(b4, b8, START)
    bad = []
    key = lambda t: (t["sig_t"], t["entry_t"], t["d"], round(t["entry"], 8), t["exit_t"], round(t["exit"], 8))
    for T in cuts:
        c4 = {s: [x for x in b4[s] if x["t"] + C.STEP["4h"] <= T] for s in C.SYMS}
        c8 = {s: [x for x in b8[s] if x["t"] + C.STEP["8h"] <= T] for s in C.SYMS}
        cut = C.live_trades(c4, c8, START)
        for k in full:
            a = [key(t) for t in full[k] if not t.get("open") and t["exit_t"] < T - 8 * 3600000]
            b = [key(t) for t in cut[k] if not t.get("open") and t["exit_t"] < T - 8 * 3600000]
            if a != b:
                bad.append(f"{time.strftime('%Y-%m-%d', time.gmtime(T / 1000))} {k[0]} {k[1]} 全量{len(a)}笔/截断{len(b)}笔")
        cb, cm = C.grid_series(c4, c8, START)
        dd = [d for d in fb if d * C.D < T - 2 * C.D and abs(fb.get(d, 0) - cb.get(d, 0)) + abs(fm.get(d, 0) - cm.get(d, 0)) > 1e-12]
        if dd:
            bad.append(f"{time.strftime('%Y-%m-%d', time.gmtime(T / 1000))} 研究口径日收益有 {len(dd)} 天不一致")
    res = {"asof": int(time.time()), "ok": not bad, "cuts": [time.strftime("%Y-%m-%d", time.gmtime(T / 1000)) for T in cuts],
           "problems": bad, "sec": round(time.time() - t0)}
    tmp = os.path.join(OUT, "overlay_lookahead.json.tmp")
    json.dump(res, open(tmp, "w", encoding="utf-8"), ensure_ascii=False); os.replace(tmp, os.path.join(OUT, "overlay_lookahead.json"))
    if bad:
        pp = os.path.join(OUT, "alerts_pending.json")
        try:
            pend = json.load(open(pp, encoding="utf-8"))
        except Exception:
            pend = []
        pend.append("【叠加前视检验·⚠️】截断重跑和全量不一致(可能用到了未来数据)：" + "；".join(bad[:4]))
        tmp = pp + ".tmp"; json.dump(pend, open(tmp, "w", encoding="utf-8"), ensure_ascii=False); os.replace(tmp, pp)
    print(time.strftime("%F %T"), "叠加前视检验", "通过" if not bad else "不通过", bad[:3], f"{res['sec']}s", flush=True)
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
