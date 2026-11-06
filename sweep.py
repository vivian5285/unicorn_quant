# -*- coding: utf-8 -*-
"""全库扫描：对一个品种+周期，把所有可回测策略依次跑一遍，结果增量写入 sweeps/*.json。
用法: python sweep.py SYMBOL INTERVAL LIMIT   （由 server.py 以低优先级子进程启动）"""
import json
import os
import signal
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from engine import backtest, data  # noqa: E402

PER_STRATEGY_TIMEOUT = 60


class _Timeout(Exception):
    pass


def _alarm(*_):
    raise _Timeout()


def main():
    sym, itv, limit = sys.argv[1].upper(), sys.argv[2], int(sys.argv[3])
    path = os.path.join(HERE, "sweeps", f"{sym}_{itv}_{limit}.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if hasattr(os, "nice"):
        try:
            os.nice(10)
        except OSError:
            pass
    strategies = [s for s in backtest.list_strategies() if s["source"] in ("builtin", "repo")]
    skipped = [s["id"] for s in strategies if s.get("flag")]
    todo = [s for s in strategies if not s.get("flag")]
    st = {"status": "running", "symbol": sym, "interval": itv, "limit": limit, "total": len(todo), "done": 0,
          "skipped": skipped, "rows": [], "started": time.time(), "updated": time.time(), "pid": os.getpid()}

    def save():
        st["updated"] = time.time()
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False)
        os.replace(tmp, path)

    save()
    bars = data.get_klines(sym, itv, limit, closed_only=True)
    mtf = {n: data.get_klines(sym, backtest_iv, 1500, closed_only=True) for n, backtest_iv in (("240", "4h"), ("D", "1d"))}
    hist = backtest.Hist(lambda sy, iv, n: data.get_klines(sy, iv, n, closed_only=True), bars[-1]["t"] - bars[0]["t"])
    for s in todo:
        t0 = time.time()
        row = {"id": s["id"]}
        try:
            if hasattr(signal, "SIGALRM"):
                signal.signal(signal.SIGALRM, _alarm)
                signal.alarm(PER_STRATEGY_TIMEOUT)
            p = s["params"] or None
            r = backtest.run(bars, s["id"], p, None if s["source"] == "builtin" else mtf, 10000, 1.0,
                             window=260 if s["source"] == "builtin" else 600, symbol=sym, hist=hist)
            x = r["stats"]
            row.update(trades=x["trades"], ret=x["total_return"], ann=x["annualized"], dd=x["max_drawdown"],
                       sharpe=x["sharpe"], win=x["win_rate"], pf=x["profit_factor"], bh=x["buy_hold"])
        except _Timeout:
            row["err"] = "超时(>60s)"
        except Exception as e:
            row["err"] = (type(e).__name__ + ": " + str(e))[:80]
        finally:
            if hasattr(signal, "SIGALRM"):
                signal.alarm(0)
        row["secs"] = round(time.time() - t0, 1)
        st["rows"].append(row)
        st["done"] += 1
        save()
    st["status"] = "done"
    save()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # 记录失败原因，前端会显示
        sys.stderr.write(repr(e) + "\n")
        raise
