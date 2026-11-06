# -*- coding: utf-8 -*-
"""2026-10-19 方案F 复刻健康检查(每小时随 recon_guard.sh 跑一次，只读)。

宝贝要求"把系统方面能预防、修正的完全做好"。这里检查所有"会让纸面/实盘悄悄偏离回测"的情况：
  数据新鲜度：回测结果、币种权重(每年1月1日)、行业轮动名单(每月)、对账结果、每周前视检验
  一致性：擂台四条腿的参数 = 回测参数；擂台币圈腿用现货K线；币安美股永续名单有没有上新/下架
  验证进度：自然一致率
结果写 optimal_data/data_health.json(网页 /best 自动对账面板显示)；新出现/持续未解决(12小时提醒一次)的问题写进
alerts_pending.json，由 recon_guard.sh 用 root 身份走看门狗现成的钉钉机器人发出去(密钥不复制到别处)。
"""
from __future__ import annotations

import ast
import calendar
import inspect
import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, "optimal_data")
ARENA = "/home/stratroster/eth-webhook-server/strategy_engine"
sys.path.insert(0, HERE)
from engine import optimal as O   # noqa: E402

REMIND_SEC = 12 * 3600


def _rd(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _wr(path, obj):
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(path + ".tmp", path)


def _dict_literal(path, name):
    """从擂台策略源码里读出 NAME = {...} 字面量(不 import 擂台代码，避免副作用)"""
    tree = ast.parse(open(path, encoding="utf-8").read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            return ast.literal_eval(node.value)
    return None


def _const(path, name):
    tree = ast.parse(open(path, encoding="utf-8").read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            try:
                return ast.literal_eval(node.value)
            except Exception:
                return None
    return None


def checks():
    now = time.time(); g = time.gmtime(now); out = []
    add = lambda key, ok, text, level="bad": out.append({"key": key, "ok": bool(ok), "level": "ok" if ok else level, "text": text})
    # 1 回测结果新鲜度(每天 4 次：22:50、00:20、08:20、16:20 UTC)
    opt = _rd(os.path.join(OUT, "optimal.json"), {}) or {}
    age = (now - float(opt.get("asof") or 0)) / 3600
    add("optimal_fresh", age < 10, f"回测结果 {age:.1f} 小时前更新" + ("" if age < 10 else "，超过10小时没刷新(optimal_run 定时任务可能失败)"))
    # 2 币种权重(每年1月1日)
    cw = _rd(os.path.join(OUT, "crypto_weights.json"), {}) or {}
    add("weights_year", int(cw.get("year") or 0) == g.tm_year, f"币种权重年份 {cw.get('year')}（今年 {g.tm_year}）"
        + ("" if int(cw.get("year") or 0) == g.tm_year else "，没按年更新，擂台会用旧权重"))
    # 3 行业轮动名单(擂台用，每天 22:40 刷新)
    sr = _rd(os.path.join(ARENA, "data", "sector_rotation.json"), {}) or {}
    cur_m = "%04d-%02d" % (g.tm_year, g.tm_mon); sr_age = (now - float(sr.get("computed_at") or 0)) / 86400
    ok_m = sr.get("month") == cur_m or g.tm_mday <= 3
    add("rotation_fresh", ok_m and sr_age < 3, f"美股行业名单 {sr.get('month')}，{sr_age:.1f} 天前计算"
        + ("" if ok_m and sr_age < 3 else "，名单过期(sector_rotation 定时任务可能失败)"))
    # 4 回测和擂台的行业排名要一样
    bt_sel = ((opt.get("stock") or {}).get("rotation") or {}).get("selected")
    if bt_sel and sr.get("selected") and sr.get("month") == ((opt.get("stock") or {}).get("rotation") or {}).get("month"):
        add("rotation_same", list(bt_sel) == list(sr["selected"]), f"本月前5行业 回测={','.join(bt_sel)} 擂台={','.join(sr['selected'])}")
    # 5 对账结果新鲜度
    rc = _rd(os.path.join(OUT, "recon.json"), {}) or {}
    rage = (now - float(rc.get("asof") or 0)) / 3600
    add("recon_fresh", rage < 3, f"对账 {rage:.1f} 小时前", "warn")
    # 6 自然一致率(纸面验证标准 ≥80%)
    rh = _rd(os.path.join(OUT, "recon_health.json"), {}) or {}
    nr = rh.get("natural_rate")
    if nr is not None:
        add("natural_rate", float(nr) >= 80, f"自然一致率 {nr}%（{rh.get('checks')} 次检查）", "warn")
    # 7 每周前视检验
    la = _rd(os.path.join(OUT, "lookahead_check.json"), None)
    if not la:
        add("lookahead", False, "每周前视检验还没有结果", "warn")
    else:
        lage = (now - float(la.get("ts") or 0)) / 86400
        add("lookahead", la.get("ok") and lage < 9, f"前视检验 {'通过' if la.get('ok') else '未通过：' + '；'.join(la.get('problems') or [])}，{lage:.1f} 天前")
    # 8 擂台参数 = 回测参数
    try:
        sd = inspect.signature(O._st_dirs).parameters; bd = inspect.signature(O._bb_states).parameters
        it = _dict_literal(os.path.join(ARENA, "strategies", "intraday_trend_cycle.py"), "DEFAULT_PARAMS") or {}
        de = _dict_literal(os.path.join(ARENA, "strategies", "daily_ema_brk.py"), "DEFAULT_PARAMS") or {}
        ss = _dict_literal(os.path.join(ARENA, "strategies", "stock_sector_rotation.py"), "DEFAULT_PARAMS") or {}
        pairs = [("8h超趋周期", it.get("st_len"), sd["n"].default), ("8h超趋倍数", it.get("st_mult"), sd["m"].default),
                 ("8h布林周期", it.get("bb_len"), bd["n"].default), ("8h布林倍数", it.get("bb_k"), bd["k"].default),
                 ("日线快线", de.get("fast_period"), 8), ("日线慢线", de.get("slow_period"), 30), ("突破N日", de.get("brk_n"), O.BRK_N),
                 ("日线波动目标", de.get("vol_target"), O.VOL_TGT), ("美股快线", ss.get("fast_period"), 8), ("美股慢线", ss.get("slow_period"), 30),
                 ("美股止损ATR", ss.get("atr_stop_mult"), O.STOP_ATR), ("美股最少格数", ss.get("min_slots"), O.MIN_SLOTS),
                 ("美股波动目标", ss.get("vol_target"), O.VOL_TGT),
                 ("行业回看月数", _const(os.path.join(ARENA, "sector_rotation.py"), "LOOKBACK_MONTHS"), O.LOOKBACK_M),
                 ("行业前K", _const(os.path.join(ARENA, "sector_rotation.py"), "TOP_K"), O.TOP_K)]
        bad = [f"{n} 擂台{a}≠回测{b}" for n, a, b in pairs if a is None or abs(float(a) - float(b)) > 1e-9]
        add("params_same", not bad, "擂台参数与回测一致" if not bad else "参数不一致：" + "；".join(bad))
    except Exception as e:
        add("params_same", False, f"参数一致性检查出错：{type(e).__name__}: {e}", "warn")
    # 9 擂台币圈腿用现货K线
    try:
        src = open(os.path.join(ARENA, "comparison_roster.py"), encoding="utf-8").read()
        okspot = src.count('"data_source": "spot"') >= 2          # BRK_ROSTER(日线腿) + PLAN_B_8H_ROSTER(8h两腿)
        add("spot_source", okspot, "擂台币圈腿用现货K线(同回测)" if okspot else "擂台币圈腿没有用现货K线，信号会和回测有偏差")
    except Exception as e:
        add("spot_source", False, f"读取擂台配置出错：{e}", "warn")
    # 11 减半周期规则失效迹象(规则只基于过去3轮周期，事后风险最大)：熊市阶段 BTC 收盘创本轮新高、或牛市阶段从本轮高点回撤超过50%
    try:
        b = O.spot_daily("BTCUSDT"); now_ms = now * 1000
        ph, mo = O.phase_of(now_ms)
        last_h = max(h for h in O.HALVINGS if h <= now) * 1000
        bull_end = last_h + int(18 * 30.44 * 86400000)
        bull_hi = max((x["c"] for x in b if last_h <= x["t"] < bull_end), default=0)
        cyc_hi = max((x["c"] for x in b if x["t"] >= last_h), default=0); c = b[-1]["c"]
        if ph == "熊":
            ok = c <= bull_hi
            add("cycle", ok, f"减半周期：熊市阶段第{mo:.0f}月，BTC {c:,.0f}，牛市段最高收盘 {bull_hi:,.0f}"
                + ("" if ok else "——熊市阶段创了新高，周期规律可能失效，需要人工评估是否改用'逆阶段半仓'软化版"), "warn")
        elif ph == "牛" and mo >= 6:
            ok = c >= cyc_hi * 0.5
            add("cycle", ok, f"减半周期：牛市阶段第{mo:.0f}月，BTC {c:,.0f}，本轮高点 {cyc_hi:,.0f}"
                + ("" if ok else "——牛市阶段从高点腰斩，周期规律可能失效，需要人工评估"), "warn")
        else:
            add("cycle", True, f"减半周期：{ph}阶段第{mo:.0f}月({O.PHASE_RULE[ph]})，BTC {c:,.0f}")
    except Exception as e:
        add("cycle", False, f"周期检查出错：{type(e).__name__}", "warn")
    # 12 2026-10-19 事先定好的选币规则(消除"站在今天挑币"的偏差)：每年1月1日币池 = 候选池里过去365天币安现货成交额前5(排除稳定币)。
    #    回测(research_scripts/rule_universe.py)：按这条规则 2018 起选币，主方案 4.97(固定主流5是 5.56、按当时市值前5是 4.93)。
    #    每年 11、12 月预演明年的规则币池，跟现在的主流5不一样就提醒，1月1日人工换(回测 M5、擂台 roster 一起换)。
    if g.tm_mon in (11, 12):
        try:
            cache_p = os.path.join(OUT, "rule_universe_preview.json"); cache = _rd(cache_p, {}) or {}
            if now - float(cache.get("ts") or 0) > 86400:
                cand = ["BTC", "ETH", "BNB", "SOL", "XRP", "BCH", "LTC", "ADA", "DOGE", "LINK", "DOT", "TRX", "AVAX", "ETC", "XLM", "SHIB",
                        "TON", "SUI", "HYPE", "POL", "NEAR", "APT", "UNI"]
                vol = {}
                for c_ in cand:
                    try:
                        rows = json.load(urllib.request.urlopen(f"https://api.binance.com/api/v3/klines?symbol={c_}USDT&interval=1d&limit=365", timeout=20))
                        if len(rows) >= 300:
                            vol[c_] = sum(float(r[7]) for r in rows)          # 报价资产成交额(USDT)
                    except Exception:
                        pass
                    time.sleep(0.2)
                top = [k for k, _ in sorted(vol.items(), key=lambda z: -z[1])[:5]]
                cache = {"ts": now, "top5": top}; _wr(cache_p, cache)
            top = cache.get("top5") or []; cur = sorted(s[:-4] for s in O.M5)
            same = sorted(top) == cur
            add("rule_universe", same, f"明年规则币池预演(过去365天成交额前5)：{','.join(top)}；现在：{','.join(cur)}"
                + ("" if same else "——明年1月1日需要换币(回测 M5 和擂台 roster 一起换)"), "warn")
        except Exception as e:
            add("rule_universe", False, f"规则币池预演出错：{type(e).__name__}", "warn")
    # 13 2026-10-19 方案F实盘执行器(客户E账户)：服务在跑就必须每分钟更新状态；冻结/熔断要报
    try:
        import subprocess
        if subprocess.run(["systemctl", "is-enabled", "--quiet", "planf-live-E"]).returncode == 0:
            ls = _rd("/var/lib/planf/live_E_status.json", {}) or {}
            age = (now - float(ls.get("ts") or 0)) / 60
            ok = age < 5 and not ls.get("frozen") and not ls.get("circuit_breaker")
            add("live_E", ok, f"实盘执行器E账户：{ls.get('mode')}，净值 {ls.get('equity')}U，{age:.0f} 分钟前更新"
                + ("" if age < 5 else "——执行器可能停了") + ("；冻结：" + "；".join(ls.get("why") or []) if ls.get("frozen") else "")
                + ("；熔断中" if ls.get("circuit_breaker") else ""))
            tg = _rd("/var/lib/planf/targets.json", {}) or {}
            tage = (now - float(tg.get("ts") or 0)) / 60
            add("publisher", tage < 5 and tg.get("ok"), f"实盘信号发布器 {tage:.0f} 分钟前更新" + ("" if tg.get("ok") else "；问题：" + "；".join(tg.get("problems") or [])))
    except Exception as e:
        add("live_E", False, f"执行器检查出错：{type(e).__name__}", "warn")
    # 10 币安美股永续上新/下架(回测股票池 = 今天可交易的)
    try:
        info = json.load(urllib.request.urlopen("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=30))
        listed = {s["symbol"][:-4] for s in info["symbols"] if s.get("contractType") == "TRADIFI_PERPETUAL" and s.get("status") == "TRADING"}
        pool = set(O.SEC_OF); now_tr = sorted(pool & listed)
        snap_p = os.path.join(OUT, "tradable_snapshot.json"); snap = _rd(snap_p, None)
        if snap is None:
            _wr(snap_p, {"ts": now, "tradable": now_tr}); add("universe", True, f"可交易美股 {len(now_tr)} 只(首次记录)")
        else:
            added = sorted(set(now_tr) - set(snap["tradable"])); removed = sorted(set(snap["tradable"]) - set(now_tr))
            if added or removed:
                add("universe", False, f"币安美股永续名单变化：新增 {','.join(added) or '无'}；下架 {','.join(removed) or '无'}。回测股票池会自动跟着变，"
                                       f"有持仓的下架品种需要人工处理", "warn")
                _wr(snap_p, {"ts": now, "tradable": now_tr})
            else:
                add("universe", True, f"可交易美股 {len(now_tr)} 只，名单没变")
    except Exception as e:
        add("universe", False, f"读取币安美股永续名单失败：{type(e).__name__}", "warn")
    return out


def main():
    now = time.time(); res = checks()
    _wr(os.path.join(OUT, "data_health.json"), {"ts": now, "checks": res})
    st_p = os.path.join(OUT, "health_alert_state.json"); st = _rd(st_p, {}) or {}
    pend_p = os.path.join(OUT, "alerts_pending.json"); pend = _rd(pend_p, []) or []
    for c in res:
        prev = st.get(c["key"]) or {}
        if not c["ok"]:
            if not prev.get("bad") or now - float(prev.get("last") or 0) > REMIND_SEC:
                pend.append(f"【方案F纸面·{'❌' if c['level'] == 'bad' else '⚠️'}】{c['text']}")
                st[c["key"]] = {"bad": True, "last": now}
        elif prev.get("bad"):
            pend.append(f"【方案F纸面·✅恢复】{c['text']}")
            st[c["key"]] = {"bad": False, "last": now}
    _wr(st_p, st); _wr(pend_p, pend)
    print(time.strftime("%Y-%m-%d %H:%M:%S"), "健康检查", sum(c["ok"] for c in res), "/", len(res), "待发告警", len(pend), flush=True)


if __name__ == "__main__":
    main()
