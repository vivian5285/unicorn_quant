# -*- coding: utf-8 -*-
"""对照：让赢家多跑（追踪止盈 / 永不卖）vs 固定止盈。用法: python portfolio_study2.py"""
import itertools, os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
if hasattr(os, "nice"):
    try: os.nice(15)
    except OSError: pass
from engine import portfolio as pf
import portfolio_study as ps

data, miss = ps.load()
cal, arr = pf.prepare(data, "SPY")
uni = [s for s in ps.UNI if s in arr]
start = 170
bench = {k: pf.buy_hold(cal, arr, [k], start) for k in ("SPY", "QQQ")}
bench["全池等权"] = pf.buy_hold(cal, arr, uni, start)
for k, v in bench.items():
    h = len(v["eq"]) // 2
    print(f"基准 {k:6s} 总{v['ret']:7.1f}% 年化{v['cagr']:5.1f}% 回撤{v['maxdd']:5.1f}%  后半{(v['eq'][-1]/v['eq'][h]-1)*100:6.1f}%")

def line(tag, r):
    return (f"{tag:34s} 总{r['ret']:7.1f}% 年化{r['cagr']:5.1f}% 回撤{r['maxdd']:5.1f}% 卡玛{(r['calmar'] or 0):4.2f} 仓位{r['exposure']:3d}% 笔{r['trades']:4d} 均持{r['avg_days']:3d}天"
            f" | 前半{r['h1']:6.1f}% 后半{r['h2']:6.1f}%(撤{r['dd2']:4.1f}) | 期末套{r['open_under']}/{r['open_n']} 深套峰值{r['deep_under_max']:4.1f}%")

print("\n=== A) 买入后永不卖(只在拐头买入、满槽就不再买) ===")
for sig, slots in itertools.product(["turn", "macd", "hl"], [10, 20, 40]):
    r = pf.simulate(cal, arr, sig, None, None, slots, False, universe=uni, start=start)
    print(line(f"{sig} 槽{slots} 永不卖", r))
print("\n=== B) 追踪止盈(涨超15%后回落X%卖)+ -25%止损，不设固定止盈 ===")
res = []
for sig, trail, slots in itertools.product(["turn", "rsi", "macd", "hl"], [0.15, 0.25], [10, 20]):
    r = pf.simulate(cal, arr, sig, None, 0.25, slots, False, trail=trail, universe=uni, start=start)
    res.append((sig, trail, slots, r))
    print(line(f"{sig} 追踪{int(trail*100)}% 止损25% 槽{slots}", r))
print("\n=== C) 只用追踪、不设止损 ===")
for sig, trail, slots in itertools.product(["turn", "macd", "hl"], [0.15, 0.25], [20]):
    r = pf.simulate(cal, arr, sig, None, None, slots, False, trail=trail, universe=uni, start=start)
    print(line(f"{sig} 追踪{int(trail*100)}% 无止损 槽{slots}", r))
