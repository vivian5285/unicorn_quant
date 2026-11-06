# -*- coding: utf-8 -*-
"""回测资金费单独估算 + 扣资金费后的收益。2026-11-06 Claude 写。不改回测引擎：只读 optimal.json / overlay_series.json。

做法(逐笔、逐次结算)：
  币圈：optimal.json 每笔币圈交易，名义占总资金 = 当年币种权重 × 子腿权重(1/3) × 仓位 alloc × 币圈腿1倍 × 当天波动率缩放；
        每次币安永续资金费结算(8小时一次)：成本 = 方向 × 名义 × 费率(做多付正费率，做空收正费率)
  美股：每笔名义 = alloc(已除以当月只数) × 美股腿1倍 × 当天波动率缩放，用币安美股永续的真实费率(2026-04 起才有，之前记0)
  叠加：实盘参数逐笔信号(engine/overlay_core)，名义 = 权重 × min(1%÷止损距离, 3)，不受波动率缩放
  波动率缩放：用 optimal.json 里币圈腿/美股腿曲线按 vol_control 同口径重算
  币安永续 2019-09(BTC)~2020-09(SOL) 才上线，之前没有费率，记 0(这几年的资金费被低估)
结果：optimal_data/funding.json + static/funding.json，最优方案页显示。用法：python -m engine.funding_cost
"""
import calendar
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from engine import optimal as O        # noqa: E402  只用 stats / vol_control 两个纯函数
from engine import overlay_core as C   # noqa: E402

OUT = os.path.join(HERE, "optimal_data")
STATIC = os.path.join(HERE, "static")
CACHE = os.path.join(OUT, "funding_cache")
D = 86400000
LIVE_START = 1792407600                 # 2026-10-19 10:59 UTC E 账户进入实盘(同 planf_track.LIVE_START)
INCOME_E = "/var/lib/planf/income_E.json"
STATUS_E = "/var/lib/planf/live_E_status.json"


def _rd(p, d=None):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return d


def _wr(p, obj):
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, p)


def funding_hist(sym):
    """币安永续历史资金费率，增量缓存。返回 [(结算时间ms, 费率)]；没有这个合约返回 []。"""
    os.makedirs(CACHE, exist_ok=True)
    p = os.path.join(CACHE, f"fr_{sym}.json")
    out = _rd(p, []) or []
    s = out[-1][0] + 1 if out else 1567296000000
    try:
        while True:
            r = json.load(urllib.request.urlopen(
                f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={sym}&startTime={s}&limit=1000", timeout=30))
            if not r:
                break
            out += [(int(x["fundingTime"]), float(x["fundingRate"])) for x in r]
            if len(r) < 1000:
                break
            s = out[-1][0] + 1
            time.sleep(0.25)
    except urllib.error.HTTPError:
        pass                                  # 没有这个永续
    _wr(p, out)
    return out


def curve_of(stats):
    return {int(t) // D: v for t, v in stats["series"]}


def main():
    t0 = time.time()
    opt = _rd(os.path.join(OUT, "optimal.json"))
    ov = _rd(os.path.join(OUT, "overlay_series.json"), {}) or {}
    W = {int(y): w for y, w in (opt.get("crypto_weights") or {}).items()}
    F = curve_of(opt["series"]["F"])
    cport, scurve = curve_of(opt["series"]["crypto"]), curve_of(opt["series"]["stock"])
    Fchk, scale = O.vol_control([cport, scurve], [O.W_CRYPTO, O.W_STOCK], O.W_CRYPTO + O.W_STOCK)
    chk = O.stats(Fchk)["cagr"]                                       # 重算的方案F年化，应≈页面
    now = time.time() * 1000
    drag = {"crypto": defaultdict(float), "stock": defaultdict(float), "overlay": defaultdict(float)}
    rates = {}

    def rate_list(sym):
        if sym not in rates:
            rates[sym] = funding_hist(sym)
        return rates[sym]

    first_fr = {}
    for t in opt["trades"]:
        sym = t["symbol"]; crypto = sym.endswith("USDT")
        perp = sym if crypto else sym + "USDT"
        fr = rate_list(perp)
        if not fr:
            continue
        first_fr.setdefault(perp, fr[0][0])
        a = int(t["entry_t"]); z = int(t["exit_t"]) if t.get("exit_t") else now
        side = 1 if t["side"] == "LONG" else -1
        for ft, r in fr:
            if ft <= a or ft > z:
                continue
            d = ft // D; s = scale.get(d, scale.get(d - 1, 1.0))
            if crypto:
                y = time.gmtime(ft / 1000).tm_year
                frac = W.get(y, {}).get(sym, 0.0) * float(t.get("w") or 1 / 3) * float(t.get("alloc") or 1) * O.W_CRYPTO * s
                drag["crypto"][d] += side * frac * r
            else:
                drag["stock"][d] += side * float(t.get("alloc") or 0) * O.W_STOCK * s * r
    # 叠加腿(实盘参数逐笔)
    ov_start = calendar.timegm(time.strptime(ov.get("start") or "2022-03-01", "%Y-%m-%d")) * 1000
    kc = os.path.join(OUT, "overlay_kcache")
    b4 = {s: C.klines(s, "4h", kc, C.DATA_START_MS) for s in C.SYMS}
    b8 = {s: C.klines(s, "8h", kc, C.DATA_START_MS) for s in C.SYMS}
    for (strat, sym), trs in C.live_trades(b4, b8, ov_start).items():
        w = C.LIVE_W["brk_tp" if strat.endswith("tp_4h") else "brk_trail" if strat.endswith("trail_4h") else "mio"]
        fr = rate_list(sym)
        for t in trs:
            f = w * min(0.01 / t["rd"], 3.0)
            for ft, r in fr:
                if t["entry_t"] < ft <= t["exit_t"]:
                    drag["overlay"][ft // D] += t["d"] * f * r
    # 扣费后曲线
    ks = sorted(F)
    OB = {int(t) // D: v for t, v in ov.get("brk", [])}; OM = {int(t) // D: v for t, v in ov.get("mio", [])}

    def build(with_ov, net):
        c = {ks[0]: 1.0}
        for a, b in zip(ks, ks[1:]):
            r = F[b] / F[a] - 1
            if with_ov:
                r += OB.get(b, 0.0) + OM.get(b, 0.0)
            if net:
                # 两天之间(美股周末等)的所有结算都算进来
                for d in range(a + 1, b + 1):
                    r -= drag["crypto"].get(d, 0.0) + drag["stock"].get(d, 0.0) + (drag["overlay"].get(d, 0.0) if with_ov else 0.0)
            c[b] = c[a] * (1 + r)
        return c
    S = {"F": O.stats(build(False, False)), "F_net": O.stats(build(False, True)),
         "FO2": O.stats(build(True, False)), "FO2_net": O.stats(build(True, True))}
    by_year = {}
    for k, dd in drag.items():
        for d, v in dd.items():
            y = str(time.gmtime(d * 86400).tm_year)
            by_year.setdefault(y, {"crypto": 0.0, "stock": 0.0, "overlay": 0.0})[k] += v
    by_month = {}                                                     # 月度对账用(engine/monthly_report.py)
    for k, dd in drag.items():
        for d, v in dd.items():
            m = time.strftime("%Y-%m", time.gmtime(d * 86400))
            if m >= "2026-01":
                by_month.setdefault(m, {"crypto": 0.0, "stock": 0.0, "overlay": 0.0})[k] += v
    by_month = {m: {k: round(100 * x, 4) for k, x in v.items()} for m, v in sorted(by_month.items())}
    for y, v in by_year.items():
        for k in list(v):
            v[k] = round(100 * v[k], 2)
        v["total_F"] = round(v["crypto"] + v["stock"], 2); v["total_FO2"] = round(v["crypto"] + v["stock"] + v["overlay"], 2)
    # 实盘期对照
    use_btc = bool((_rd(STATUS_E, {}) or {}).get("use_btc"))
    bt_live = sum(v for k in ("crypto", "stock", "overlay") for d, v in drag[k].items() if d * D >= LIVE_START * 1000 - D)
    inc = _rd(INCOME_E, {}) or {}
    st = _rd(os.path.join(OUT, "planf_track.json"), {}) or {}
    eq0 = ((st.get("since_live") or {}).get("start_eq")) or 659.83
    live_fee = ((inc.get("total") or {}).get("FUNDING_FEE"))
    span_y = (ks[-1] - ks[0]) / 365.25
    lite = lambda x: {k: x[k] for k in ("cagr", "max_dd", "ratio", "ytd", "last12m", "last12m_dd", "yearly")}
    res = {"asof": int(time.time()), "build_sec": round(time.time() - t0, 1),
           "check_F_cagr_recomputed": chk, "check_F_cagr_page": opt["series"]["F"]["cagr"],
           "by_year": dict(sorted(by_year.items())), "by_month": by_month,
           "avg_per_year": {"F": round(sum(v["total_F"] for v in by_year.values()) / span_y, 2),
                            "FO2": round(sum(v["total_FO2"] for v in by_year.values()) / span_y, 2)},
           "stats": {k: lite(v) for k, v in S.items()},
           "series": {"F_net": S["F_net"]["series"], "FO2_net": S["FO2_net"]["series"]},
           "funding_from": {k: time.strftime("%Y-%m", time.gmtime(v / 1000)) for k, v in sorted(first_fr.items()) if k in C.SYMS},
           "live": {"since": LIVE_START, "backtest_funding_pct": round(-100 * bt_live, 3),
                    "live_funding_usdt": live_fee, "live_funding_pct": round(100 * live_fee / eq0, 3) if live_fee is not None else None,
                    "note": "正 = 收到资金费；回测口径含BTC" if use_btc else "正 = 收到资金费；回测口径含BTC(小账户实盘不做BTC，仅作量级对照)"},
           "note": "资金费按币安历史费率逐次结算估算：做多付正费率、做空收正费率；币安永续上线前(BTC 2019-09、SOL 2020-09，美股永续 2026-04 前)没有费率记0；"
                   "不含基差、滑点。叠加腿按实盘参数逐笔。"}
    _wr(os.path.join(OUT, "funding.json"), res)
    _wr(os.path.join(STATIC, "funding.json"), res)
    print(time.strftime("%F %T"), f"资金费 方案F 平均每年{res['avg_per_year']['F']:+.2f}% 叠加后{res['avg_per_year']['FO2']:+.2f}% | "
          f"扣费后年化 F {S['F_net']['cagr']}%(原{S['F']['cagr']}%) FO2 {S['FO2_net']['cagr']}%(原{S['FO2']['cagr']}%) | 校验F {chk}% | {res['build_sec']}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
