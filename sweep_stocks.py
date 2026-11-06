# -*- coding: utf-8 -*-
"""美股策略适配矩阵：知名个股 × 趋势/通道/缠论/斐波那契/Supertrend/布林带 等策略，多空与仅做多两种模式。
用法: python sweep_stocks.py INTERVAL LIMIT [SYMBOLS逗号分隔]   （由 server.py 低优先级后台启动，结果增量写入 sweeps/US_*.json）"""
import json
import os
import signal
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from engine import backtest, data, stocks  # noqa: E402

PER_RUN_TIMEOUT = 45
# 重点：趋势 / 通道 / 缠论 / 斐波那契 / Supertrend / 布林带 / MACD / 突破 / 均值回归
WANT = [
    # 内置（经典、透明）
    "dual_ema_band", "ema_cross", "sma_cross", "macd_cross", "supertrend_flip", "boll_breakout", "boll_revert",
    "donchian_trend", "rsi_revert",
    # 趋势 / 超级趋势
    "supertrend_adx", "hma_trend", "heikin_ashi_trend", "ichimoku_cloud", "vegas_tunnel", "parabolic_sar_flip",
    "kaufman_ama", "schaff_trend_cycle", "vortex_indicator", "williams_alligator", "adx_regime_switch",
    "raschke_adx_pullback", "trend_ensemble_stocks", "weinstein_stage", "time_series_momentum",
    # 通道 / 布林 / 突破
    "keltner_channel", "ttm_squeeze", "bollinger_squeeze", "bollinger_rsi_contrarian", "turtle_breakout",
    "turtle_system2", "donchian_reversal", "darvas_box", "volatility_breakout", "livermore_pivotal_point",
    "fiftytwo_week_high",
    # 缠论 / 斐波那契
    "chanlun_pivot", "chanlun_pivot_duan", "chanlun_pivot_sentinel", "fibonacci_retracement",
    # MACD / CCI / 震荡
    "macd_histogram", "macd_rsi_trend_3565", "macd_rsi_reversal", "cci_mean_reversion", "rsi_mean_reversion",
    "connors_rsi2", "td_sequential", "wavetrend", "ehlers_fisher_transform", "kdj_cross",
]


class _Timeout(Exception):
    pass


def _alarm(*_):
    raise _Timeout()


def main():
    itv, limit = sys.argv[1], int(sys.argv[2])
    syms = [x.upper() for x in sys.argv[3].split(",")] if len(sys.argv) > 3 and sys.argv[3] else list(stocks.SYMBOLS)
    path = os.path.join(HERE, "sweeps", f"US_{itv}_{limit}.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if hasattr(os, "nice"):
        try:
            os.nice(15)
        except OSError:
            pass
    meta = {s["id"]: s for s in backtest.list_strategies()}
    todo, skipped = [], []
    for sid in WANT:
        m = meta.get(sid)
        if not m or m["source"] == "error":
            skipped.append({"id": sid, "why": "策略库中不存在"})
        elif m.get("flag"):
            skipped.append({"id": sid, "why": "依赖外部数据/横截面"})
        elif m.get("mtf"):
            skipped.append({"id": sid, "why": "需要4H+日线多周期数据"})
        else:
            todo.append(m)
    st = {"status": "running", "interval": itv, "limit": limit, "symbols": syms, "strategies": [m["id"] for m in todo],
          "skipped": skipped, "total": len(syms) * len(todo) * 2, "done": 0, "rows": [], "bh": {}, "errors": 0,
          "started": time.time(), "updated": time.time(), "pid": os.getpid()}

    def save():
        st["updated"] = time.time()
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False)
        os.replace(tmp, path)

    save()
    for sym in syms:
        try:
            bars = data.get_klines(sym, itv, limit, closed_only=True)
        except Exception as e:
            st["errors"] += len(todo) * 2
            st["done"] += len(todo) * 2
            st.setdefault("sym_err", {})[sym] = str(e)[:80]
            save()
            continue
        if len(bars) < 150:
            st["done"] += len(todo) * 2
            st.setdefault("sym_err", {})[sym] = f"K线仅{len(bars)}根"
            save()
            continue
        hist = backtest.Hist(lambda sy, iv, n: data.get_klines(sy, iv, n, closed_only=True), bars[-1]["t"] - bars[0]["t"])
        for m in todo:
            for mode in ("ls", "lo"):
                row = {"s": sym, "i": m["id"], "m": mode}
                try:
                    if hasattr(signal, "SIGALRM"):
                        signal.signal(signal.SIGALRM, _alarm)
                        signal.alarm(PER_RUN_TIMEOUT)
                    builtin = m["source"] == "builtin"
                    r = backtest.run(bars, m["id"], m["params"] or None, None, 10000, 1.0,
                                     window=260 if builtin else 500, symbol=sym, hist=hist, long_only=(mode == "lo"))
                    x = r["stats"]
                    row.update(n=x["trades"], r=x["total_return"], a=x["annualized"], d=x["max_drawdown"], sh=x["sharpe"],
                               w=x["win_rate"], pf=x["profit_factor"], h1=x.get("ret_h1"), h2=x.get("ret_h2"),
                               ex=x["exposure"])
                    st["bh"].setdefault(sym, {"r": x["buy_hold"], "d": x.get("bh_dd"), "bars": x["bars"], "days": x["days"]})
                except _Timeout:
                    row["err"] = "超时"
                    st["errors"] += 1
                except Exception as e:
                    row["err"] = (type(e).__name__ + ": " + str(e))[:60]
                    st["errors"] += 1
                finally:
                    if hasattr(signal, "SIGALRM"):
                        signal.alarm(0)
                st["rows"].append(row)
                st["done"] += 1
            if st["done"] % 8 == 0:
                save()
        save()
    st["status"] = "done"
    save()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        sys.stderr.write(repr(e) + "\n")
        raise
