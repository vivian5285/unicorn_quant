# -*- coding: utf-8 -*-
"""研究：美股现货「底部拐头买入 / 盈利才卖 / 满仓换手」组合回测。用法: python portfolio_study.py [out.json]"""
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
from engine import portfolio as pf, stocks  # noqa: E402

BASE = [s for s in stocks.SYMBOLS if s not in ("SPY", "QQQ", "IWM")]
# 加入这几年跌得惨/横盘的蓝筹，降低"只挑赢家"的幸存者偏差
EXTRA = ["PYPL", "NKE", "SBUX", "VZ", "PFE", "CVS", "KO", "PEP", "MCD", "HD", "MRK", "CSCO", "IBM", "GE", "CAT",
         "GS", "BAC", "MA", "ADBE", "QCOM", "TXN", "UBER", "T", "WBA", "MMM", "DOW"]
UNI = BASE + EXTRA


def load():
    data, miss = {}, []
    for s in UNI + ["SPY", "QQQ"]:
        try:
            b = stocks.get_klines(s, "1d", 3000, closed_only=True)
            if len(b) >= 400:
                data[s] = b
            else:
                miss.append(s)
        except Exception as e:
            miss.append(s)
    return data, miss


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "sweeps", "portfolio_study.json")
    data, miss = load()
    cal, arr = pf.prepare(data, "SPY")
    uni = [s for s in UNI if s in arr]
    n = len(cal)
    start = 170
    d0 = time.strftime("%Y-%m-%d", time.gmtime(cal[start] * 86400))
    d1 = time.strftime("%Y-%m-%d", time.gmtime(cal[-1] * 86400))
    print(f"标的 {len(uni)} 只（缺失 {miss}），区间 {d0} ~ {d1}，约 {(n - start) / 252:.1f} 年")
    bench = {k: pf.buy_hold(cal, arr, [k], start) for k in ("SPY", "QQQ")}
    bench["全池等权"] = pf.buy_hold(cal, arr, uni, start)
    for k, v in bench.items():
        h = len(v["eq"]) // 2
        print(f"  基准 {k:6s} 总收益 {v['ret']:8.1f}%  年化 {v['cagr']:5.1f}%  最大回撤 {v['maxdd']:5.1f}%  前半 {(v['eq'][h-1]/v['eq'][0]-1)*100:6.1f}%  后半 {(v['eq'][-1]/v['eq'][h]-1)*100:6.1f}%")
    res = []
    for sig, tp, stop, slots, rot in itertools.product(["turn", "rsi", "macd", "hl"], [0.10, 0.20, 0.30], [None, 0.25], [10, 20], [False, True]):
        r = pf.simulate(cal, arr, sig, tp, stop, slots, rot, universe=uni, start=start)
        r.update(sig=sig, tp=tp, stop=stop, slots=slots, rot=rot)
        res.append(r)
    slim = [{k: v for k, v in r.items() if k not in ("eq", "open")} for r in res]
    json.dump({"period": [d0, d1], "n_uni": len(uni), "miss": miss, "bench": {k: {kk: vv for kk, vv in v.items() if kk != "eq"} for k, v in bench.items()},
               "res": slim}, open(out, "w"), ensure_ascii=False)

    def line(r):
        return (f"{r['sig']:5s} 止盈{int(r['tp']*100):2d}% 止损{('%d%%' % int(r['stop']*100)) if r['stop'] else '无':>3s} 槽{r['slots']:2d} 换手{'是' if r['rot'] else '否'}"
                f" | 总{r['ret']:7.1f}% 年化{r['cagr']:5.1f}% 回撤{r['maxdd']:5.1f}% 卡玛{(r['calmar'] or 0):5.2f} 仓位{r['exposure']:3d}%"
                f" | 笔{r['trades']:4d} 胜{r['win']:3d}% 均持{r['avg_days']:3d}天 | 期末套{r['open_under']}/{r['open_n']} 最差{r['open_worst']:6.1f}% 深套占比峰值{r['deep_under_max']:4.1f}%"
                f" | 前半{r['h1']:6.1f}%(撤{r['dd1']:4.1f}) 后半{r['h2']:6.1f}%(撤{r['dd2']:4.1f})")

    print("\n=== 用户规则本身：盈利才卖(无止损)、不换手 ===")
    for r in [x for x in res if x["stop"] is None and not x["rot"]]:
        print(line(r))
    print("\n=== 无止损 + 换手 ===")
    for r in [x for x in res if x["stop"] is None and x["rot"]]:
        print(line(r))
    print("\n=== 加 -25% 止损 对比 (不换手) ===")
    for r in [x for x in res if x["stop"] and not x["rot"]]:
        print(line(r))
    # 前半段挑 → 后半段看
    print("\n=== 样本外检验：按【前半段 收益/回撤】选Top5，看它们【后半段】 ===")
    ranked = sorted(res, key=lambda r: -(r["h1"] / max(r["dd1"], 1)))[:5]
    for r in ranked:
        print(line(r))
    spy_h2 = (bench["SPY"]["eq"][-1] / bench["SPY"]["eq"][len(bench["SPY"]["eq"]) // 2] - 1) * 100
    print(f"   (后半段 SPY {spy_h2:.1f}%)")
    # 当前信号
    print("\n=== 今天(最后一根日线)各信号命中的股票 ===")
    for sig in ("turn", "rsi", "macd", "hl"):
        hit = sorted([(arr[s]["DM"][-1] or 0, s) for s in uni if arr[s]["S"][sig][-1]])
        print(f"  {sig:5s}: " + (", ".join(f"{s}(近期最深回撤{dm*100:.0f}%)" for dm, s in hit) or "无"))
    # 被套明细(最佳样本外配置)
    best = ranked[0]
    r = pf.simulate(cal, arr, best["sig"], best["tp"], best["stop"], best["slots"], best["rot"], universe=uni, start=start)
    print("\n=== 该配置期末仍持有、浮亏最大的仓位 ===")
    for s, g, d in r["open"]:
        print(f"  {s:6s} 浮盈 {g*100:6.1f}%  已持有 {d} 个交易日")


if __name__ == "__main__":
    main()
