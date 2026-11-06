# -*- coding: utf-8 -*-
"""美股趋势突破扫描：扫描全池 -> 写 latest.json -> 更新纸面跟踪账户。由cron在美股收盘后调用，也可由页面"立即刷新"触发。"""
import json, os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
if hasattr(os, "nice"):
    try: os.nice(15)
    except OSError: pass
from engine import scanner

os.makedirs(scanner.DIR, exist_ok=True)
lock = os.path.join(scanner.DIR, "running.lock")
open(lock, "w").write(str(time.time()))
try:
    t0 = time.time()
    scan = scanner.run_scan()
    tmp = os.path.join(scanner.DIR, "latest.json.tmp")
    json.dump(scan, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
    os.replace(tmp, os.path.join(scanner.DIR, "latest.json"))
    st = scanner.update_paper(scan)
    sig = [r["sym"] for r in scan["rows"] if r["signal"]]
    print(f"{time.strftime('%F %T')} 扫描完成 {len(scan['rows'])}只 数据日期{scan['bar_date']} 新突破{sig} 持仓{list(st['pos'])} 挂买{st['pend_buy']} 挂卖{list(st['pend_sell'])} 错误{len(scan['errors'])} 耗时{time.time()-t0:.0f}s", flush=True)
finally:
    try: os.remove(lock)
    except OSError: pass
