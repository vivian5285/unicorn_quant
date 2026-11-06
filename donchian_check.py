# -*- coding: utf-8 -*-
"""一致性核对：策略模块(经 engine.backtest 回放) vs 参数研究用的快速回测器，默认参数应给出几乎相同的成交。"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
from engine import backtest, data
import donchian_study as ds
for sym, itv, per_year in (("ETHUSDT", "4h", 2190), ("ZECUSDT", "4h", 2190), ("NVDA", "1d", 252)):
    bars = data.get_klines(sym, itv, 3000, closed_only=True)
    a = ds.prep(bars)
    cfg = dict(ds.DEFAULT)
    f = ds.sim(a, cfg, per_year)
    r = backtest.run(bars, "donchian_trend_atr_4h", None, None, 10000, 1.0, window=600, symbol=sym)
    s = r["stats"]
    print(f"{sym} {itv} {len(bars)}根 | 模块回放: 笔{s['trades']} 收益{s['total_return']}% 回撤{s['max_drawdown']}% | 快速回测器: 笔{f['n']} 收益{f['ret']:.1f}% 回撤{f['dd']:.1f}%")
