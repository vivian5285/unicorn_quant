# -*- coding: utf-8 -*-
"""均线(10/30,7/25,8/30,可叠加三均线) + 大级别支撑压力 + 盈利换手 的多组合研究。
用法: python trend_rotation_study.py PHASE [SR]
  PHASE=1  触发=日线, 大级别=周线, 约9年
  PHASE=2  触发=4小时(由小时线合成), 大级别=日线, 约2年
  SR=fractal(默认,分形高低点聚类) | donch(近50根最高/近20根最低)"""
import itertools
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
if hasattr(os, "nice"):
    try:
        os.nice(15)
    except OSError:
        pass
from engine import stocks, trendrot as tr  # noqa: E402
import portfolio_study as ps  # noqa: E402

phase = int(sys.argv[1]) if len(sys.argv) > 1 else 1
SR = sys.argv[2] if len(sys.argv) > 2 else "fractal"
CFG = {
    1: dict(htf="1w", htf_fast=10, htf_slow=30, pivot_k=2, pivot_L=104, rs_n=63, room=0.08, per_year=252, start=140, name="日线触发/周线大级别"),
    2: dict(htf="1d", htf_fast=20, htf_slow=50, pivot_k=3, pivot_L=120, rs_n=126, room=0.05, per_year=504, start=140, name="4小时触发/日线大级别"),
}[phase]


def load():
    base, htf, miss = {}, {}, []
    for s in ps.UNI + ["SPY", "QQQ"]:
        try:
            if phase == 1:
                b = stocks.get_klines(s, "1d", 3000, closed_only=True)
                h = stocks.get_klines(s, "1w", 1500, closed_only=True)
            else:
                b = tr.resample_4h(stocks.get_klines(s, "1h", 6000, closed_only=True))
                h = stocks.get_klines(s, "1d", 3000, closed_only=True)
            if len(b) >= 300 and len(h) > 60:
                base[s], htf[s] = b, h
            else:
                miss.append(s)
        except Exception:
            miss.append(s)
    return base, htf, miss


def main():
    t0 = time.time()
    base, htf, miss = load()
    cal, pre = tr.prepare(base, htf, CFG)
    uni = [s for s in ps.UNI if s in pre]
    start = CFG["start"]
    f = lambda t: time.strftime("%Y-%m-%d", time.gmtime(t / 1000))
    print(f"[阶段{phase} {CFG['name']} | 支撑压力={SR}] 标的 {len(uni)} 只(缺 {miss}) 区间 {f(cal[start])} ~ {f(cal[-1])} 约{(len(cal)-start)/CFG['per_year']:.1f}年  准备耗时 {time.time()-t0:.0f}s")
    bench = {k: tr.buy_hold(cal, pre, [k], start, per_year=CFG["per_year"]) for k in ("SPY", "QQQ")}
    bench["全池等权"] = tr.buy_hold(cal, pre, uni, start, per_year=CFG["per_year"])
    for k, v in bench.items():
        print(f"  基准 {k:6s} 总{v['ret']:8.1f}% 年化{v['cagr']:6.1f}% 回撤{v['maxdd']:5.1f}%  前半{v['h1']:6.1f}% 后半{v['h2']:6.1f}%")
    spy_cagr = bench["SPY"]["cagr"]
    pairs = [(10, 30), (7, 25), (8, 30)]
    # 预计算信号
    ent = {}
    for pair, tri, mode, hon in itertools.product(pairs, [False, True], ["cross", "cross_sr", "breakout", "pullback"], [True, False]):
        ent[(pair, tri, mode, hon)] = {s: tr.entries(pre, s, pair, mode, tri, hon, CFG["room"], SR) for s in uni}
    print(f"  信号预计算完成 {time.time()-t0:.0f}s，开始跑组合…")
    res = []
    for pair, tri, mode, hon, ex, st, rot in itertools.product(pairs, [False, True], ["cross", "cross_sr", "breakout", "pullback"], [True, False],
                                                              ["trend", "slow", "trend_rej"], ["none", "fix10", "sr"], ["none", "weak", "active"]):
        r = tr.simulate(cal, pre, ent[(pair, tri, mode, hon)], pair, ex, st, rot, 10, start=start, sr=SR, per_year=CFG["per_year"])
        r.pop("eq")
        r.update(pair=f"{pair[0]}/{pair[1]}", tri=tri, entry=mode, htf=hon, exit=ex, stop=st, rot=rot)
        res.append(r)
    print(f"  共 {len(res)} 个组合，耗时 {time.time()-t0:.0f}s")
    out = os.path.join(HERE, "sweeps", f"trend_rot_p{phase}_{SR}.json")
    json.dump({"phase": phase, "sr": SR, "cfg": CFG, "bench": {k: {a: b for a, b in v.items() if a != "eq"} for k, v in bench.items()}, "res": res}, open(out, "w"), ensure_ascii=False)

    def avg(rs, k):
        v = [x[k] for x in rs if x[k] is not None]
        return sum(v) / len(v) if v else 0

    print("\n=== 因子消融：每个因子取值下，所有组合的平均表现（让数据说话）===")
    print("   因子=取值        组合数 | 平均年化 平均回撤 平均卡玛 平均仓位 平均笔数 | 前半 后半 | 跑赢SPY年化占比 | 年化>0占比")
    for key, levels in [("pair", ["10/30", "7/25", "8/30"]), ("tri", [False, True]), ("entry", ["cross", "cross_sr", "breakout", "pullback"]),
                        ("htf", [True, False]), ("exit", ["trend", "slow", "trend_rej"]), ("stop", ["none", "fix10", "sr"]), ("rot", ["none", "weak", "active"])]:
        for lv in levels:
            rs = [x for x in res if x[key] == lv]
            print(f"   {key:5s}={str(lv):10s} {len(rs):5d} | {avg(rs,'cagr'):7.1f}% {avg(rs,'maxdd'):7.1f}% {avg(rs,'calmar'):7.2f} {avg(rs,'exposure'):6.0f}% {avg(rs,'trades'):7.0f} | {avg(rs,'h1'):6.1f}% {avg(rs,'h2'):6.1f}% | {100*sum(x['cagr']>spy_cagr for x in rs)/len(rs):5.0f}% | {100*sum(x['cagr']>0 for x in rs)/len(rs):5.0f}%")

    def line(r):
        return (f"{r['pair']:5s} {'三线' if r['tri'] else '双线'} {r['entry']:8s} 大级别{'开' if r['htf'] else '关'} 出场{r['exit']:9s} 止损{r['stop']:5s} 换手{r['rot']:6s}"
                f" | 总{r['ret']:7.1f}% 年化{r['cagr']:5.1f}% 回撤{r['maxdd']:5.1f}% 卡玛{(r['calmar'] or 0):5.2f} 仓位{r['exposure']:3d}% 笔{r['trades']:4d} 胜{r['win']:3d}% PF{(r['pf'] or 0):4.2f} 均持{r['avg_days']:5.1f}"
                f" | 前半{r['h1']:6.1f}% 后半{r['h2']:6.1f}% 深套{r['deep']:4.1f}%")

    ok = [x for x in res if x["trades"] >= 30 and x["h1"] > 0 and x["h2"] > 0]
    print(f"\n=== 前后半段都盈利且笔数>=30 的组合：{len(ok)}/{len(res)}；其中年化跑赢SPY的 {sum(x['cagr']>spy_cagr for x in ok)} 个 ===")
    print("--- Top12 按卡玛(年化/最大回撤) ---")
    for r in sorted(ok, key=lambda x: -(x['calmar'] or -9))[:12]:
        print(line(r))
    print("--- Top8 按年化收益 ---")
    for r in sorted(ok, key=lambda x: -x['cagr'])[:8]:
        print(line(r))
    print("\n=== 样本外检验：只用【前半段】卡玛选Top8 → 看它们【后半段】 ===")
    for r in sorted([x for x in res if x["trades"] >= 30], key=lambda x: -(x["h1"] / max(x["dd1"], 1)))[:8]:
        print(line(r) + f" | 前半回撤{r['dd1']}% 后半回撤{r['dd2']}%")
    print(f"   (后半段 SPY {bench['SPY']['h2']}% QQQ {bench['QQQ']['h2']}% 全池等权 {bench['全池等权']['h2']}%)")


if __name__ == "__main__":
    main()
