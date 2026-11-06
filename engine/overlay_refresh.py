# -*- coding: utf-8 -*-
"""每天把「最优方案」页用的突破叠加回测(optimal_data/overlay_series.json)续算到最新收盘。2026-11-06 Claude 写。

原文件是 11-01 研究脚本(youtube_b4，不在本机)生成的，只到 2026-09-30，之后页面按"叠加=0"算，头条数字漏掉了10月以来的表现。
这里用 engine/overlay_core.py 独立重算同一口径(真假突破18组 + 布林擠壓24组参数邻域平均，各1%风险)，从 2022-03-01 到最新。

安全阀：新序列和 11-01 原始序列在原窗口(2022-03-01~2026-09-30)上必须对得上(日收益相关≥0.85，且按页面公式算的
同窗口"叠加2%"比值相差≤20%)，对不上就不覆盖、发告警。第一次覆盖前把原文件存成 overlay_series.orig_20261101.json(以后都拿它做基准)。
用法：python -m engine.overlay_refresh [--dry]
"""
import calendar
import json
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from engine import overlay_core as C  # noqa: E402

OUT = os.path.join(HERE, "optimal_data")
P = os.path.join(OUT, "overlay_series.json")
ORIG = os.path.join(OUT, "overlay_series.orig_20261101.json")
CACHE = os.path.join(OUT, "overlay_kcache")
START = "2022-03-01"
MIN_CORR, MAX_RATIO_DIFF = 0.85, 0.20


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


def alert(text):
    pp = os.path.join(OUT, "alerts_pending.json"); pend = _rd(pp, []) or []
    pend.append(text); _wr(pp, pend)


def ratio_fo2(F, add, d0, d1):
    ks = [k for k in sorted(F) if d0 - 1 <= k <= d1]
    v = 1.0; pk = 1.0; dd = 0.0
    for a, b in zip(ks, ks[1:]):
        v *= F[b] / F[a] + add(b); pk = max(pk, v); dd = max(dd, 1 - v / pk)
    cg = v ** (365.25 / (ks[-1] - ks[0])) - 1
    return cg / dd if dd else None, cg, dd


def corr(x, y):
    n = len(x); mx, my = sum(x) / n, sum(y) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(x, y))
    vx = sum((a - mx) ** 2 for a in x); vy = sum((b - my) ** 2 for b in y)
    return cov / math.sqrt(vx * vy) if vx and vy else 0.0


def main():
    dry = "--dry" in sys.argv
    t0 = time.time()
    start_ms = calendar.timegm(time.strptime(START, "%Y-%m-%d")) * 1000
    warm = start_ms - 200 * C.D                                    # 布林擠壓要126天带宽历史
    b4 = {s: C.klines(s, "4h", CACHE, warm) for s in C.SYMS}
    b8 = {s: C.klines(s, "8h", CACHE, warm) for s in C.SYMS}
    opt = _rd(os.path.join(OUT, "optimal.json"), {})
    F = {int(t) // C.D: v for t, v in opt["series"]["F"]["series"]}
    # 固定数量的盈亏要除以当天总净值(方案F+叠加)才是收益率：先用方案F净值算，再用合并净值迭代两次(已收敛)
    eq = F
    for _ in range(3):
        brk, mio = C.grid_series(b4, b8, start_ms, eq=eq)
        ks = sorted(F); eq = {ks[0]: 1.0}
        for a, b in zip(ks, ks[1:]):
            eq[b] = eq[a] * (F[b] / F[a] + 1.0 * (brk.get(b, 0.0) + mio.get(b, 0.0)))   # 实盘2%档 = brk + mio 各1%
    last_day = min(b8[s][-1]["t"] for s in C.SYMS) // C.D - 1    # 最后一个完整的日子
    first_day = start_ms // C.D
    days = range(first_day, last_day + 1)

    base = _rd(ORIG) or _rd(P)
    if not base:
        print("没有原始 overlay_series.json 可对照，停止"); return 2
    OB = {int(t) // C.D: v for t, v in base["brk"]}; OM = {int(t) // C.D: v for t, v in base["mio"]}
    w0, w1 = min(OB), max(OB)
    common = [d for d in range(w0, w1 + 1)]
    c = corr([brk.get(d, 0) + mio.get(d, 0) for d in common], [OB.get(d, 0) + OM.get(d, 0) for d in common])
    r_new = ratio_fo2(F, lambda d: brk.get(d, 0) + mio.get(d, 0), w0, w1)
    r_old = ratio_fo2(F, lambda d: OB.get(d, 0) + OM.get(d, 0), w0, w1)
    diff = abs(r_new[0] / r_old[0] - 1)
    msg = (f"续算叠加 {START}~{time.strftime('%Y-%m-%d', time.gmtime(last_day * 86400))} | 原窗口对照：日收益相关{c:.3f}，"
           f"同窗口叠加2%比值 新{r_new[0]:.2f} 原{r_old[0]:.2f}(差{diff:.0%}) | {time.time() - t0:.0f}s")
    print(time.strftime("%F %T"), msg, flush=True)
    if c < MIN_CORR or diff > MAX_RATIO_DIFF:
        alert(f"【叠加回测续算·⚠️】新算的叠加序列和11-01原始数据对不上(相关{c:.2f}/比值差{diff:.0%})，没有覆盖页面数据，请人工看 engine/overlay_refresh.py")
        return 1
    if dry:
        return 0
    if not os.path.exists(ORIG) and os.path.exists(P):
        _wr(ORIG, _rd(P))
    res = {"note": "突破叠加回测(逐日盯市占净值比例)：真假突破4h 18组邻域平均 + 布林擠壓8h 24组邻域平均，各按每笔1%风险；"
                   "2026-11-06 起由 engine/overlay_refresh.py 每天续算(独立实现，原窗口对照见 check)。原 11-01 研究版本存于 overlay_series.orig_20261101.json",
           "start": START, "end": time.strftime("%Y-%m-%d", time.gmtime(last_day * 86400)),
           "brk": [[d * C.D, round(brk.get(d, 0.0), 8)] for d in days],
           "mio": [[d * C.D, round(mio.get(d, 0.0), 8)] for d in days],
           "check": {"asof": int(time.time()), "corr_vs_orig": round(c, 4), "ratio_fo2_new": round(r_new[0], 3),
                     "ratio_fo2_orig": round(r_old[0], 3), "orig_window": [base["start"], base["end"]]}}
    _wr(P, res)
    return 0


if __name__ == "__main__":
    sys.exit(main())
