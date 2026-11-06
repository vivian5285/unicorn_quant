# -*- coding: utf-8 -*-
"""入围参数对比：每个资产类别挑候选参数，比较组内中位卡玛、平均回撤、两半段是否都盈利，以及主调品种明细。"""
import os, statistics as st, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import donchian_study as ds
from engine import data

D = ds.DEFAULT
def C(pair, don, ex, adx, stop, trail, **kw):
    return dict(D, pair=pair, don=don, exit=ex, adx=adx, stop=stop, trail=trail, **kw)
CAND = {
 "crypto4h": [("宝贝原始", D), ("c1 30/120 don30 x5 s3 t5", C((30,120),30,5,0,3.0,5.0)), ("c2 30/120 don30 x10 s3 t4", C((30,120),30,10,0,3.0,4.0)),
              ("c3 30/120 don30 x20 s3 t5", C((30,120),30,20,0,3.0,5.0)), ("c4 20/100 don30 x10 s3 t4", C((20,100),30,10,0,3.0,4.0)),
              ("c5 50/200 don30 x10 s3 t4", C((50,200),30,10,0,3.0,4.0)), ("c6 30/120 don55 x10 s3 t4", C((30,120),55,10,0,3.0,4.0)),
              ("c7 30/120 don30 x10 s3 t4 +BB", C((30,120),30,10,0,3.0,4.0,bb=True))],
 "crypto1d": [("宝贝原始", D), ("d1 50/200 don10 x20 s1.5 t5", C((50,200),10,20,0,1.5,5.0)), ("d2 50/200 don20 x20 s2 t4", C((50,200),20,20,0,2.0,4.0)),
              ("d3 50/200 don30 x20 s3 t4", C((50,200),30,20,0,3.0,4.0)), ("d4 30/120 don30 x10 s2 t4", C((30,120),30,10,0,2.0,4.0)),
              ("d5 50/200 don55 x20 s2 t5", C((50,200),55,20,0,2.0,5.0)), ("d6 20/100 don30 x10 s2 t4", C((20,100),30,10,0,2.0,4.0))],
 "gold4h": [("宝贝原始", D), ("g1 20/100 don10 x5 s3 t3", C((20,100),10,5,0,3.0,3.0)), ("g2 20/100 don30 x10 s3 t3", C((20,100),30,10,0,3.0,3.0)),
            ("g3 30/120 don30 x10 s3 t3", C((30,120),30,10,0,3.0,3.0)), ("g4 50/200 don20 x10 s3 t3", C((50,200),20,10,0,3.0,3.0)),
            ("g5 20/100 don20 x10 s2 t3 ADX20", C((20,100),20,10,20,2.0,3.0))],
 "gold1d": [("宝贝原始", D), ("l1 30/120 don55 x20 s2 t3", C((30,120),55,20,0,2.0,3.0)), ("l2 50/200 don55 x20 s3 t4", C((50,200),55,20,0,3.0,4.0)),
            ("l3 50/200 don20 x10 s2 t3", C((50,200),20,10,0,2.0,3.0)), ("l4 30/120 don30 x10 s2 t3", C((30,120),30,10,0,2.0,3.0))],
 "stock1d": [("宝贝原始", D), ("s1 20/100 don55 x20 s2 t4", C((20,100),55,20,0,2.0,4.0)), ("s2 20/100 don30 x20 s2 t4", C((20,100),30,20,0,2.0,4.0)),
             ("s3 30/120 don30 x20 s1.5 t3", C((30,120),30,20,0,1.5,3.0)), ("s4 20/100 don30 x20 s3 t4", C((20,100),30,20,0,3.0,4.0)),
             ("s5 50/200 don55 x20 s2 t4", C((50,200),55,20,0,2.0,4.0)), ("s6 20/100 don30 x20 s2 t4 ADX20", C((20,100),30,20,20,2.0,4.0))],
 "tradfi4h": [("宝贝原始", D), ("t1 20/100 don55 x20 s3 t3", C((20,100),55,20,0,3.0,3.0)), ("t2 20/100 don30 x20 s3 t4", C((20,100),30,20,0,3.0,4.0)),
              ("t3 30/120 don55 x20 s3 t4", C((30,120),55,20,0,3.0,4.0)), ("t4 20/100 don30 x10 s2 t3", C((20,100),30,10,0,2.0,3.0))],
}
for name in sys.argv[1:]:
    itv, py, assets = ds.SETS[name]
    A = {}
    for sym, prim in assets:
        try:
            b = data.get_klines(sym, itv, 20000 if itv == "4h" else 3000, closed_only=True)
            if len(b) >= 300:
                A[sym] = ds.prep(b); A[sym]["prim"] = prim
        except Exception:
            pass
    print(f"\n===== {name}  资产 {len(A)} =====")
    print("   候选 | 组中位卡玛 平均年化 平均回撤 中位前半/后半 前后半都盈利占比 | 主调品种(年化/回撤/笔数/前半/后半)")
    for label, cfg in CAND[name]:
        res = {s: ds.sim(A[s], cfg, py) for s in A}
        rs = [r for r in res.values() if r]
        med = st.median([(r["calmar"] if r["n"] >= 8 else 0.0) for r in rs])
        both = sum(1 for r in rs if r["h1"] > 0 and r["h2"] > 0) / len(rs) * 100
        prim = " ; ".join(f"{s} {r['cagr']:.0f}%/{r['dd']:.0f}%/{r['n']}笔/{r['h1']:.0f}%/{r['h2']:.0f}%" for s, r in res.items() if r and A[s]["prim"])
        print(f"   {label:34s}| {med:5.2f} {st.mean(r['cagr'] for r in rs):6.1f}% {st.mean(r['dd'] for r in rs):5.1f}% {st.median([r['h1'] for r in rs]):6.1f}%/{st.median([r['h2'] for r in rs]):6.1f}% {both:4.0f}% | {prim}")
