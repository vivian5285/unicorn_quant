# -*- coding: utf-8 -*-
"""生成「最优方案」复盘数据(optimal_data/optimal.json)。cron：每天 UTC 22:50(美股收盘后)跑一次。"""
import os
import sys
import time

os.nice(10)
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from engine import optimal  # noqa: E402

t0 = time.time()
res = optimal.build()
optimal.write(res)
F = res["series"]["F"]
print(time.strftime("%Y-%m-%d %H:%M:%S"), f"方案F 年化{F['cagr']}% 回撤{F['max_dd']}% 比{F['ratio']} 今年{F['ytd']}% | 交易{len(res['trades'])}笔 | {time.time()-t0:.0f}s", flush=True)

try:                                   # 2026-10-18 全品种复盘(universe.json + u/<SYM>.json)
    from engine import universe
    u = universe.build()
    print(time.strftime("%Y-%m-%d %H:%M:%S"), f"全品种复盘: {len(u['symbols'])} 个品种 {u['n_trades']} 笔", flush=True)
except Exception as e:
    print("全品种复盘失败:", e, flush=True)

try:                                   # 2026-10-17 擂台纸面 vs 回测 对账
    from engine import recon
    rc = recon.run()
    print(time.strftime("%Y-%m-%d %H:%M:%S"), "对账:", " | ".join(f"{p['name']} 状态{p.get('state_ok','-')}/{p.get('state_n','-')} 配上{len(p.get('matched',[]))} 漏{len(p.get('missed',[]))} 多{len(p.get('extra',[]))}" for p in rc["pairs"]), flush=True)
except Exception as e:
    print("对账失败:", e, flush=True)
