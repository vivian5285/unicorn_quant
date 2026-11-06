# -*- coding: utf-8 -*-
"""路径分辨率收敛测试：同一段行情，用 3H假设路径 / 15m / 5m / 1m 的真实K线还原价格路径，看 Pine 字面(tick单位)版收益是否随分辨率变细而塌掉。"""
import os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
if hasattr(os, "nice"):
    try: os.nice(15)
    except OSError: pass
from engine import pinefusion as pf
sym = sys.argv[1] if len(sys.argv) > 1 else "ETHUSDT"
days = int(sys.argv[2]) if len(sys.argv) > 2 else 150
b15 = pf.fetch_15m(sym, days * 96, "15m")
b5 = pf.fetch_15m(sym, days * 288, "5m")
b1 = pf.fetch_15m(sym, days * 1440, "1m")
mt = pf.tick_size(sym)
print(f"[{sym}] 最近约{days}天  15m={len(b15)}根 5m={len(b5)}根 1m={len(b1)}根")
def line(tag, r):
    print(f"   {tag:28s} {r['n']:4d}笔 总{r['ret']:8.1f}% 年化{r['cagr']:8.1f}% 回撤{r['dd']:5.1f}% 胜{r['win']:3.0f}% PF{r['pf']:5.2f} 单笔均{r['avg_pnl']:6.2f}U")
for tm, lab in (("literal", "Pine字面(tick单位,追踪距离几毛钱)"), ("atr", "作者本意(ATR价格单位)")):
    print(f"\n== {lab} ==")
    base, sig = pf.build(b15)
    line("3H假设路径(TradingView默认)", pf.simulate(base, sig, mt, tm, "tv"))
    line("15分钟真实路径", pf.simulate(base, sig, mt, tm, "real"))
    base5, sig5 = pf.build(b15, b5)
    line("5分钟真实路径", pf.simulate(base5, sig5, mt, tm, "real"))
    base1, sig1 = pf.build(b15, b1)
    line("1分钟真实路径", pf.simulate(base1, sig1, mt, tm, "real"))
