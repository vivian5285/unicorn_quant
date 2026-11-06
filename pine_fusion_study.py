# -*- coding: utf-8 -*-
"""复刻并回测 Pine「ETH融合版 最终最优版(3H)」。用法: python pine_fusion_study.py [SYMBOLS逗号分隔]"""
import os, sys, time, statistics as st
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
if hasattr(os, "nice"):
    try: os.nice(15)
    except OSError: pass
import requests
from engine import pinefusion as pf

CRYPTO = ["ETHUSDT", "BTCUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "LINKUSDT", "DOGEUSDT", "ZECUSDT", "XMRUSDT"]
METAL = ["XAUUSDT", "PAXGUSDT"]
STOCK = ["SNDKUSDT", "MUUSDT", "NVDAUSDT", "TSLAUSDT", "AMDUSDT", "ASMLUSDT", "METAUSDT", "AVGOUSDT"]
syms = sys.argv[1].split(",") if len(sys.argv) > 1 else CRYPTO + METAL + STOCK
N15 = 90000
info = requests.get(pf.FAPI + "/fapi/v1/exchangeInfo", timeout=30).json()
ticks = {}
for s in info["symbols"]:
    for f in s["filters"]:
        if f["filterType"] == "PRICE_FILTER":
            ticks[s["symbol"]] = float(f["tickSize"])

def fmt(r):
    return (f"{r['n']:4d}笔 总{r['ret']:8.1f}% 年化{r['cagr']:6.1f}% 回撤{r['dd']:5.1f}% 胜{r['win']:3.0f}% PF{r['pf']:4.2f} 单笔均{r['avg_pnl']:6.2f}U | 前半{r['h1']:7.1f}% 后半{r['h2']:7.1f}%")

res = {}
for sym in syms:
    t0 = time.time()
    try:
        bars15 = pf.fetch_15m(sym, N15)
        base, sig = pf.build(bars15)
    except Exception as e:
        print(f"{sym}: 数据失败 {str(e)[:60]}"); continue
    if len(base) < 400:
        print(f"{sym}: 3H仅{len(base)}根，跳过"); continue
    mt = ticks.get(sym, 0.01)
    d0 = time.strftime("%Y-%m-%d", time.gmtime(base[60]["t"] / 1000))
    r = {}
    for tm in ("literal", "atr"):
        r[tm + "_real"] = pf.simulate(base, sig, mt, tm, "real")
        r[tm + "_tv"] = pf.simulate(base, sig, mt, tm, "tv")
    res[sym] = r
    bh = r["atr_real"]["bh"]
    print(f"\n[{sym}] 最小跳动{mt} 3H共{len(base)}根 自{d0} 约{r['atr_real']['years']:.1f}年 买入持有{bh:+.1f}%  ({time.time()-t0:.0f}s)")
    for k, lab in (("literal_real", "按Pine字面(tick单位)+15m真实路径"), ("atr_real", "按作者本意(ATR价格单位)+15m真实路径"),
                   ("literal_tv", "按Pine字面+TV默认3H假设路径"), ("atr_tv", "按作者本意+TV默认3H假设路径")):
        if k in r:
            print(f"   {lab:30s} {fmt(r[k])}")
    if sym == "ETHUSDT":
        for k in ("literal_real", "atr_real"):
            ex = {}
            for t in r[k]["trades"]:
                for e in t["exits"]:
                    ex[e] = ex.get(e, 0) + 1
            print(f"   出场方式分布[{k}]: {ex}  多/空笔数: {sum(1 for t in r[k]['trades'] if t['side']>0)}/{sum(1 for t in r[k]['trades'] if t['side']<0)}")
        print("   前6笔(字面,真实路径):", [(time.strftime('%m-%d %H', time.gmtime(t['t']/1000)), '多' if t['side']>0 else '空', round(t['entry'],1), round(t['pnl'],2), t['exits']) for t in r['literal_real']['trades'][:6]])

print("\n\n===== 汇总(年化/回撤/笔数)：诚实口径=本意+15m路径；TV口径=3H假设路径 =====")
print(f"{'品种':10s}| {'本意+15m路径(诚实)':22s}| {'本意+TV路径':22s}| {'字面tick+15m路径':22s}| {'字面tick+TV路径':24s}| 买入持有")
for s_, r in res.items():
    f = lambda x: f"{x['cagr']:7.1f}%/{x['dd']:5.1f}%/{x['n']:4d}"
    print(f"{s_:10s}| {f(r['atr_real']):22s}| {f(r['atr_tv']):22s}| {f(r['literal_real']):22s}| {f(r['literal_tv']):24s}| {r['atr_real']['bh']:7.1f}%")
for lab, grp in (("加密", CRYPTO), ("黄金", METAL), ("美股永续", STOCK)):
    v = [res[x] for x in grp if x in res]
    if v:
        for key, nm in (("atr_real", "本意+15m"), ("atr_tv", "本意+TV路径"), ("literal_tv", "字面+TV路径")):
            print(f"[{lab}] {nm:10s}: 平均年化{st.mean(x[key]['cagr'] for x in v):8.1f}% 平均回撤{st.mean(x[key]['dd'] for x in v):5.1f}% 盈利品种{sum(x[key]['ret']>0 for x in v)}/{len(v)} 平均PF{st.mean(x[key]['pf'] for x in v):.2f}")
