# -*- coding: utf-8 -*-
"""复盘引擎：逐根K线回放策略（只喂"截至当前"的历史，无未来函数），产出标记/成交/权益曲线/统计。

策略来源：
  1. 内置 builtin：独角兽自带的双均线(7/25)等，纯本平台实现
  2. strategy_engine.strategies.STRATEGIES：仓库里已有的全部策略（同一套 generate_signal 接口）
"""
from __future__ import annotations

import ast
import bisect
import inspect
import math
import re
import threading
from typing import Callable, Dict, List, Optional

from . import ta

FEE = 0.0005  # 单边0.05%（币安合约taker）

_PERIOD_MS = {"4h": 4 * 3600_000, "1d": 86400_000}


# ---------------- 内置策略 ----------------
def dual_ema_band(bars_by_tf, params, position):
    """图中策略（EMA7/EMA25 双均线带，4H）：
    开空：阴线收盘跌破双均线 且 快线<慢线
    平空：阳线站上双均线（若同时快线>慢线则反手开多，否则只平仓）
    开多：阳线站上双均线 且 快线>慢线
    平多：阴线跌破双均线（若同时快线<慢线则反手开空，否则只平仓）
    硬止损：开仓价 ∓ ATR(14)×stop_atr（stop_atr=0 关闭）"""
    p = {"fast": 7, "slow": 25, "stop_atr": 2.5, **(params or {})}
    bars = bars_by_tf["base"]
    if len(bars) < int(p["slow"]) + 16:
        return None
    c = [x["c"] for x in bars]
    f, s = ta.ema(c, int(p["fast"])), ta.ema(c, int(p["slow"]))
    if f[-1] is None or s[-1] is None:
        return None
    b = bars[-1]
    px = b["c"]
    top, bot = max(f[-1], s[-1]), min(f[-1], s[-1])
    bear_break = b["c"] < b["o"] and px < bot
    bull_above = b["c"] > b["o"] and px > top
    side = (position or {}).get("side")
    base = {"price": px, "bar_time": b["t"]}
    stop_n = float(p["stop_atr"])
    a = ta.atr(bars, 14)[-1] if stop_n > 0 else None

    def opened(act):
        out = {**base, "action": act, "reason": "开多" if act == "LONG" else "开空"}
        if a:
            out["stop_loss"] = px - a * stop_n if act == "LONG" else px + a * stop_n
        return out

    if side == "LONG" and bear_break:
        return opened("SHORT") if f[-1] < s[-1] else {**base, "action": "CLOSE_QUICK_EXIT", "reason": "平多"}
    if side == "SHORT" and bull_above:
        return opened("LONG") if f[-1] > s[-1] else {**base, "action": "CLOSE_QUICK_EXIT", "reason": "平空"}
    if side is None:
        if bear_break and f[-1] < s[-1]:
            return opened("SHORT")
        if bull_above and f[-1] > s[-1]:
            return opened("LONG")
    return None


def ema_cross(bars_by_tf, params, position):
    p = {"fast": 7, "slow": 30, **(params or {})}
    bars = bars_by_tf["base"]
    if len(bars) < int(p["slow"]) + 3:
        return None
    c = [b["c"] for b in bars]
    f, s = ta.ema(c, int(p["fast"])), ta.ema(c, int(p["slow"]))
    if None in (f[-1], f[-2], s[-1], s[-2]):
        return None
    gold = f[-2] <= s[-2] and f[-1] > s[-1]
    dead = f[-2] >= s[-2] and f[-1] < s[-1]
    base = {"price": bars[-1]["c"], "bar_time": bars[-1]["t"]}
    if gold:
        return {**base, "action": "LONG", "reason": "金叉"}
    if dead:
        return {**base, "action": "SHORT", "reason": "死叉"}
    return None


def rsi_revert(bars_by_tf, params, position):
    p = {"n": 14, "lo": 30, "hi": 70, **(params or {})}
    bars = bars_by_tf["base"]
    c = [b["c"] for b in bars]
    r = ta.rsi(c, int(p["n"]))
    if len(r) < 3 or r[-1] is None or r[-2] is None:
        return None
    base = {"price": bars[-1]["c"], "bar_time": bars[-1]["t"]}
    side = (position or {}).get("side")
    if side is None:
        if r[-2] < p["lo"] <= r[-1]:
            return {**base, "action": "LONG", "reason": f"RSI上穿{p['lo']}"}
        if r[-2] > p["hi"] >= r[-1]:
            return {**base, "action": "SHORT", "reason": f"RSI下穿{p['hi']}"}
    elif side == "LONG" and r[-1] >= 50:
        return {**base, "action": "CLOSE_QUICK_EXIT", "reason": "RSI回到50平仓"}
    elif side == "SHORT" and r[-1] <= 50:
        return {**base, "action": "CLOSE_QUICK_EXIT", "reason": "RSI回到50平仓"}
    return None



def _sig(bars, act, reason, stop=None):
    b = bars[-1]
    out = {"price": b["c"], "bar_time": b["t"], "action": act, "reason": reason}
    if stop is not None:
        out["stop_loss"] = stop
    return out


def supertrend_flip(bt, params, position):
    """Supertrend(10,3) 趋势翻转：多头趋势持多，空头趋势持空；止损=Supertrend线本身。"""
    p = {"n": 10, "mult": 3.0, **(params or {})}
    bars = bt["base"]
    if len(bars) < int(p["n"]) + 5:
        return None
    line, dirs = ta.supertrend(bars, int(p["n"]), float(p["mult"]))
    if not dirs[-1] or line[-1] is None:
        return None
    want = "LONG" if dirs[-1] == 1 else "SHORT"
    side = (position or {}).get("side")
    if side != want:
        return _sig(bars, want, "Supertrend转多" if want == "LONG" else "Supertrend转空", line[-1])
    return None


def boll_breakout(bt, params, position):
    """布林带突破：收盘突破上轨做多/下轨做空，回到中轨平仓。"""
    p = {"n": 20, "k": 2.0, **(params or {})}
    bars = bt["base"]
    if len(bars) < int(p["n"]) + 2:
        return None
    c = [x["c"] for x in bars]
    up, mid, lo = ta.boll(c, int(p["n"]), float(p["k"]))
    if mid[-1] is None:
        return None
    side = (position or {}).get("side")
    if side is None:
        if c[-1] > up[-1]:
            return _sig(bars, "LONG", "突破布林上轨", mid[-1])
        if c[-1] < lo[-1]:
            return _sig(bars, "SHORT", "跌破布林下轨", mid[-1])
    elif side == "LONG" and c[-1] < mid[-1]:
        return _sig(bars, "CLOSE_QUICK_EXIT", "回落中轨")
    elif side == "SHORT" and c[-1] > mid[-1]:
        return _sig(bars, "CLOSE_QUICK_EXIT", "回升中轨")
    return None


def boll_revert(bt, params, position):
    """布林带回归：收盘跌破下轨做多/突破上轨做空，回到中轨平仓。"""
    p = {"n": 20, "k": 2.0, "stop_atr": 3.0, **(params or {})}
    bars = bt["base"]
    if len(bars) < int(p["n"]) + 16:
        return None
    c = [x["c"] for x in bars]
    up, mid, lo = ta.boll(c, int(p["n"]), float(p["k"]))
    a = ta.atr(bars, 14)[-1]
    if mid[-1] is None or not a:
        return None
    side = (position or {}).get("side")
    if side is None:
        if c[-1] < lo[-1]:
            return _sig(bars, "LONG", "下轨超卖", c[-1] - a * float(p["stop_atr"]))
        if c[-1] > up[-1]:
            return _sig(bars, "SHORT", "上轨超买", c[-1] + a * float(p["stop_atr"]))
    elif side == "LONG" and c[-1] >= mid[-1]:
        return _sig(bars, "CLOSE_QUICK_EXIT", "回到中轨")
    elif side == "SHORT" and c[-1] <= mid[-1]:
        return _sig(bars, "CLOSE_QUICK_EXIT", "回到中轨")
    return None


def donchian_trend(bt, params, position):
    """唐奇安通道趋势(海龟式)：突破N日高做多/跌破N日低做空，反向M日通道平仓。"""
    p = {"entry": 20, "exit": 10, **(params or {})}
    bars = bt["base"]
    ne, nx = int(p["entry"]), int(p["exit"])
    if len(bars) < ne + 2:
        return None
    prev = bars[-ne - 1:-1]
    hi, lo = max(x["h"] for x in prev), min(x["l"] for x in prev)
    prx = bars[-nx - 1:-1]
    xhi, xlo = max(x["h"] for x in prx), min(x["l"] for x in prx)
    c = bars[-1]["c"]
    side = (position or {}).get("side")
    if side is None:
        if c > hi:
            return _sig(bars, "LONG", f"突破{ne}周期高点", xlo)
        if c < lo:
            return _sig(bars, "SHORT", f"跌破{ne}周期低点", xhi)
    elif side == "LONG" and c < xlo:
        return _sig(bars, "CLOSE_QUICK_EXIT", f"跌破{nx}周期低点")
    elif side == "SHORT" and c > xhi:
        return _sig(bars, "CLOSE_QUICK_EXIT", f"突破{nx}周期高点")
    return None


def macd_cross(bt, params, position):
    """MACD(12,26,9) 线上穿/下穿信号线，多空互换。"""
    p = {"fast": 12, "slow": 26, "sig": 9, **(params or {})}
    bars = bt["base"]
    if len(bars) < int(p["slow"]) + int(p["sig"]) + 5:
        return None
    c = [x["c"] for x in bars]
    m, sg, _ = ta.macd(c, int(p["fast"]), int(p["slow"]), int(p["sig"]))
    if None in (m[-1], m[-2], sg[-1], sg[-2]):
        return None
    if m[-2] <= sg[-2] and m[-1] > sg[-1]:
        return _sig(bars, "LONG", "MACD金叉")
    if m[-2] >= sg[-2] and m[-1] < sg[-1]:
        return _sig(bars, "SHORT", "MACD死叉")
    return None


def sma_cross(bt, params, position):
    """SMA 50/200 金叉死叉（经典长线趋势）。"""
    p = {"fast": 50, "slow": 200, **(params or {})}
    bars = bt["base"]
    if len(bars) < int(p["slow"]) + 3:
        return None
    c = [x["c"] for x in bars]
    f, s_ = ta.sma(c, int(p["fast"])), ta.sma(c, int(p["slow"]))
    if None in (f[-1], f[-2], s_[-1], s_[-2]):
        return None
    if f[-2] <= s_[-2] and f[-1] > s_[-1]:
        return _sig(bars, "LONG", "金叉")
    if f[-2] >= s_[-2] and f[-1] < s_[-1]:
        return _sig(bars, "SHORT", "死叉")
    return None


BUILTIN: Dict[str, dict] = {
    "dual_ema_band": {"fn": dual_ema_band, "title": "双均线带 EMA7/25", "params": {"fast": 7, "slow": 25, "stop_atr": 2.5},
                      "desc": "阴线跌破双均线且快<慢开空，阳线站上且快>慢开多，反向破位平仓，ATR硬止损（图中策略）"},
    "ema_cross": {"fn": ema_cross, "title": "EMA 金叉死叉", "params": {"fast": 7, "slow": 30},
                  "desc": "快线上穿慢线做多，下穿做空"},
    "supertrend_flip": {"fn": supertrend_flip, "title": "Supertrend 翻转", "params": {"n": 10, "mult": 3.0},
                        "desc": "Supertrend(10,3)趋势翻转，多空互换，止损=Supertrend线"},
    "boll_breakout": {"fn": boll_breakout, "title": "布林带突破", "params": {"n": 20, "k": 2.0},
                      "desc": "收盘突破上/下轨顺势开仓，回到中轨平仓"},
    "boll_revert": {"fn": boll_revert, "title": "布林带回归", "params": {"n": 20, "k": 2.0, "stop_atr": 3.0},
                    "desc": "收盘跌破下轨做多、突破上轨做空，回到中轨平仓，ATR止损"},
    "donchian_trend": {"fn": donchian_trend, "title": "唐奇安通道(海龟)", "params": {"entry": 20, "exit": 10},
                       "desc": "突破20周期高/低点入场，反向10周期通道离场"},
    "macd_cross": {"fn": macd_cross, "title": "MACD 金叉死叉", "params": {"fast": 12, "slow": 26, "sig": 9},
                   "desc": "MACD线上穿/下穿信号线，多空互换"},
    "sma_cross": {"fn": sma_cross, "title": "SMA 50/200 均线交叉", "params": {"fast": 50, "slow": 200},
                  "desc": "经典长线金叉死叉"},
    "rsi_revert": {"fn": rsi_revert, "title": "RSI 超买超卖", "params": {"n": 14, "lo": 30, "hi": 70},
                   "desc": "RSI从超卖区回升做多，超买区回落做空，回到50平仓"},
}



# ---------------- 唐奇安趋势组合预设（参数来自 donchian_study.py / donchian_final.py） ----------------
_DTA_BASE = {"adx_min": 0, "vol_mult": 0.0, "bb_filter": False, "ema_exit": False, "allow_short": False,
             "chandelier": True, "don_exit": True}
DTA_PRESETS = {
    "dta_crypto4h": ("唐奇安趋势·币圈4h", dict(_DTA_BASE, ema_fast=30, ema_slow=120, don_n=30, exit_n=5, stop_atr=3.0, trail_atr=5.0),
                     "EMA30/120趋势过滤+30根突破+3ATR止损+5ATR吊灯；币圈4h研究的稳健参数(只做多)"),
    "dta_crypto1d": ("唐奇安趋势·币圈1d", dict(_DTA_BASE, ema_fast=50, ema_slow=200, don_n=10, exit_n=20, stop_atr=1.5, trail_atr=5.0),
                     "EMA50/200+10根突破+20根反向通道；币圈日线(2018起)参数，回撤较深"),
    "dta_gold4h": ("唐奇安趋势·黄金4h", dict(_DTA_BASE, ema_fast=20, ema_slow=100, don_n=10, exit_n=5, stop_atr=3.0, trail_atr=3.0),
                   "EMA20/100+10根突破；XAU仅约300天样本，参考用"),
    "dta_stock1d": ("唐奇安趋势·美股1d", dict(_DTA_BASE, ema_fast=20, ema_slow=100, don_n=55, exit_n=20, stop_atr=2.0, trail_atr=4.0),
                    "EMA20/100+55根突破+20根反向通道+2ATR止损+4ATR吊灯；15只美股10年研究参数(只做多)"),
}


def _dta_fn(preset):
    def fn(bars_by_tf, params, position):
        from strategy_engine.strategies import donchian_trend_atr
        return donchian_trend_atr.generate_signal(bars_by_tf, {**preset, **(params or {})}, position)
    return fn


for _k, (_t, _pr, _d) in DTA_PRESETS.items():
    BUILTIN[_k] = {"fn": _dta_fn(_pr), "title": _t, "params": _pr, "desc": _d}

CATS = [  # (分类名, 关键词) —— 顺序即优先级
    ("MACD", ("macd",)), ("CCI", ("cci",)), ("缠论", ("chanlun",)),
    ("残差/动量", ("residual", "momentum", "tsmom", "clenow")),
    ("RSI/均值回归", ("rsi", "connors", "mean_reversion", "williams", "wavetrend", "fisher", "stoch", "kdj")),
    ("突破/波动", ("breakout", "turtle", "donchian", "keltner", "squeeze", "darvas", "volatility", "dual_thrust",
                 "opening_range", "week_high", "livermore", "wyckoff", "renko", "fibonacci", "td_seq", "gap", "retest")),
    ("均线/趋势", ("ema", "hma", "kaufman", "supertrend", "heikin", "trend", "vegas", "alligator", "ichimoku",
                 "parabolic", "adx", "vortex", "schaff", "weinstein", "vwap")),
    ("组合/实盘镜像", ("combo", "ensemble", "mirror", "pingkai", "heartbeat", "cs_ls")),
]
_META: dict = {}


def _meta(name: str, fn) -> dict:
    if name in _META:
        return _META[name]
    cat = "其他"
    for c, kws in CATS:
        if any(k in name for k in kws):
            cat = c
            break
    flag, doc, mtf_flag = None, "", False
    try:
        mod = inspect.getmodule(fn)
        src = inspect.getsource(mod)
        tree = ast.parse(src)
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | \
                {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)} | \
                {a.asname or a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
        consts = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
        mtf_flag = bool(consts & {"240", "D", "1D", "720"})
        if names & {"funding", "open_interest"}:
            flag = "ext"      # 依赖资金费率/持仓量历史，回测引擎暂未提供
        elif getattr(mod, "NEEDS_UNIVERSE", False) and ("universe_returns" in names or "universe" in names):
            flag = "uni"      # 需要整个币种池做横截面排名，单品种回测不适用
        d = (ast.get_docstring(tree) or "").strip().split("\n")
        doc = next((x.strip() for x in d if x.strip() and not x.strip().startswith(("#", "-", "=", "!"))), "")[:110]
    except Exception:
        pass
    _META[name] = {"cat": cat, "flag": flag, "doc": doc, "mtf": mtf_flag}
    return _META[name]


def list_strategies() -> List[dict]:
    from .retired import RETIRED   # 2026-10-18：负收益策略已淘汰，不在策略库里出现
    out = [{"id": k, "title": v["title"], "desc": v["desc"], "params": v["params"], "source": "builtin",
            "cat": "内置", "flag": None} for k, v in BUILTIN.items() if k not in RETIRED]
    try:
        from strategy_engine.strategies import STRATEGIES
        for k in sorted(STRATEGIES):
            if k.startswith("_") or k in BUILTIN or k in RETIRED:
                continue
            m = _meta(k, STRATEGIES[k])
            hm = re.search(r"_(1m|5m|15m|30m|45m|1h|2h|4h|6h|8h|12h|1d|90m|150m)$", k)
            out.append({"id": k, "title": k, "desc": m["doc"], "params": {}, "source": "repo",
                        "cat": m["cat"], "flag": m["flag"], "mtf": m["mtf"], "hint": hm.group(1) if hm else ("1d" if "residual" in k else None)})
    except Exception as e:  # 策略库缺失不影响内置策略
        out.append({"id": "_repo_error", "title": "策略库未加载", "desc": str(e)[:120], "params": {}, "source": "error",
                    "cat": "其他", "flag": None})
    return out


# ---------------- 历史K线注入（让策略内部取行情时只能看到"回测时点前已收盘"的数据） ----------------
INTERVAL_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000,
               "4h": 14_400_000, "6h": 21_600_000, "8h": 28_800_000, "12h": 43_200_000, "1d": 86_400_000, "1w": 604_800_000}
_tls = threading.local()


class Hist:
    """按 (品种,周期) 懒加载并缓存历史K线；get_bars 只返回 t+周期<=当前回测K线收盘时刻 的部分。"""

    def __init__(self, loader, span_ms: int):
        self.loader, self.span, self._c = loader, span_ms, {}

    def get(self, symbol: str, interval: str):
        k = (symbol.upper(), interval)
        if k not in self._c:
            pm = INTERVAL_MS.get(interval)
            bars = []
            if pm:
                try:
                    bars = self.loader(k[0], interval, min(6000, int(self.span / pm) + 600))
                except Exception:
                    bars = []
            self._c[k] = (bars, [b["t"] + (pm or 0) for b in bars])
        return self._c[k]


def _install_patch():
    try:
        from strategy_engine import klines
    except Exception:
        return
    if getattr(klines, "_uq_patched", False):
        return
    orig = klines.get_bars

    def patched(symbol, interval, limit=300, start_time_ms=None, end_time_ms=None):
        sim = getattr(_tls, "sim", None)
        if sim is None:
            return orig(symbol, interval, limit, start_time_ms, end_time_ms)
        bars, closes = sim["hist"].get(symbol, str(interval).lower())
        i = bisect.bisect_right(closes, sim["now_close"])
        return bars[max(0, i - int(limit or 300)):i]

    klines.get_bars = patched
    klines._uq_patched = True


def resolve(name: str) -> Callable:
    if name in BUILTIN:
        return BUILTIN[name]["fn"]
    from strategy_engine.strategies import STRATEGIES
    if name not in STRATEGIES:
        raise KeyError(f"未知策略 {name}")
    return STRATEGIES[name]


# ---------------- 回放 ----------------
def _check_exit(pos, bar):
    side, stop, tp = pos["side"], pos.get("stop"), pos.get("tp")
    if side == "LONG":
        if stop is not None and bar["l"] <= stop:
            return stop, "硬止损"
        if tp is not None and bar["h"] >= tp:
            return tp, "止盈"
    else:
        if stop is not None and bar["h"] >= stop:
            return stop, "硬止损"
        if tp is not None and bar["l"] <= tp:
            return tp, "止盈"
    return None


def run(bars: List[dict], strategy: str, params: Optional[dict] = None, mtf: Optional[Dict[str, List[dict]]] = None,
        equity0: float = 10000.0, leverage: float = 1.0, warmup: int = 60, window: int = 600,
        symbol: Optional[str] = None, hist: Optional[Hist] = None, long_only: bool = False) -> dict:
    if symbol and "symbol" not in (params or {}):
        params = {**(params or {}), "symbol": symbol.upper()}
    if hist is not None:
        _install_patch()
        _tls.sim = {"hist": hist, "now_close": 0}
    try:
        return _run(bars, strategy, params, mtf, equity0, leverage, warmup, window, long_only)
    finally:
        _tls.sim = None


def _run(bars, strategy, params, mtf, equity0, leverage, warmup, window, long_only=False) -> dict:
    fn = resolve(strategy)
    bar_ms0 = (bars[1]["t"] - bars[0]["t"]) if len(bars) > 1 else 14400000
    equity = equity0
    pos = None
    trades: List[dict] = []
    curve: List[dict] = []
    peak = equity0
    max_dd = 0.0
    in_pos_bars = 0
    c0 = bars[warmup]["c"] if len(bars) > warmup else bars[0]["c"]
    order = None                      # 2026-10-18 突破挂单：上一根收盘后挂、只在下一根有效

    def close(px, t, why, reason=None):
        nonlocal pos, equity
        d = 1 if pos["side"] == "LONG" else -1
        gross = (px - pos["entry"]) * pos["qty"] * d
        fee = (pos["entry"] + px) * pos["qty"] * FEE
        pnl = gross - fee
        equity += pnl
        trades.append({"side": pos["side"], "entry_t": pos["t"], "exit_t": t, "entry": pos["entry"], "exit": px,
                       "stop": pos.get("stop"), "pnl": round(pnl, 2), "ret": round(pnl / pos["notional"] * 100, 2),
                       "why": why, "bars": pos["bars"], "fee": round(fee, 2),
                       "open_reason": pos.get("reason", ""), "close_reason": reason or why})
        pos = None

    for i in range(warmup, len(bars)):
        bar = bars[i]
        _sim = getattr(_tls, 'sim', None)
        if _sim:
            _sim['now_close'] = bar['t'] + bar_ms0
        if pos:
            pos["bars"] += 1
            in_pos_bars += 1
            ex = _check_exit(pos, bar)
            if ex:
                close(ex[0], bar["t"], ex[1], f"跌破硬止损 {ex[0]:.4g}" if ex[1] == "硬止损" else None)
        if order is not None:
            o, order = order, None
            lng = o["side"] == "LONG"
            if not pos and ((lng and bar["h"] >= o["price"]) or (not lng and bar["l"] <= o["price"])):
                px = max(bar["o"], o["price"]) if lng else min(bar["o"], o["price"])
                notional = equity * leverage * max(0.0, min(1.0, float(o.get("size_frac") or 1.0)))
                sd = o.get("stop_dist")
                stp = (px - sd if lng else px + sd) if sd else None
                pos = {"side": o["side"], "entry": px, "t": bar["t"], "qty": notional / px, "notional": notional,
                       "stop": stp, "tp": None, "bars": 0, "brk": True,
                       "reason": o.get("reason", "") + f"；盘中触价成交 {px:.4g}" + (f"；均线跟上前靠硬止损 {stp:.4g} 保护" if stp else "")}
                if stp is not None and ((lng and bar["l"] <= stp) or (not lng and bar["h"] >= stp)):
                    close(stp, bar["t"], "硬止损", f"当天突破进场又{'跌破' if lng else '涨破'}硬止损 {stp:.4g}")
        window_bars = bars[max(0, i + 1 - window): i + 1]
        by_tf = {"base": window_bars}
        for tf, tb in (mtf or {}).items():
            pm = _PERIOD_MS.get(tf, 0)
            by_tf[tf] = [b for b in tb if b["t"] + pm <= bar["t"]]
        try:
            sig = fn(by_tf, params, {"side": pos["side"], "entry_price": pos["entry"], "entry_bar_time": pos["t"],
                                     "stop": pos.get("stop"), "brk": bool(pos.get("brk"))} if pos else None)
        except Exception:
            sig = None
        if sig:
            act = str(sig.get("action", "")).upper()
            px = float(sig.get("price") or bar["c"])
            if long_only and act == "SHORT":
                if pos and pos["side"] == "LONG":
                    close(px, bar["t"], "平多")
            elif act in ("LONG", "SHORT"):
                if pos and pos["side"] != act:
                    close(px, bar["t"], "平多" if pos["side"] == "LONG" else "平空", sig.get("reason"))
                if not pos:
                    # 方案F 的策略按波动率给仓位比例(size_frac≤1)，其余策略照旧满仓
                    notional = equity * leverage * max(0.0, min(1.0, float(sig.get("size_frac") or 1.0)))
                    pos = {"side": act, "entry": px, "t": bar["t"], "qty": notional / px, "notional": notional,
                           "stop": sig.get("stop_loss"), "tp": sig.get("tp1"), "bars": 0, "reason": sig.get("reason", "")}
            elif act == "ORDER":
                if not pos:
                    order = {"side": sig.get("order_side"), "price": float(sig["order_price"]), "stop_dist": sig.get("stop_dist"),
                             "size_frac": sig.get("size_frac"), "reason": sig.get("reason", "")}
            elif act == "CLEAR_STOP":
                if pos:
                    pos["stop"] = None; pos["brk"] = False; pos["reason"] += sig.get("reason", "")
            elif act.startswith("CLOSE") and pos:
                close(px, bar["t"], "平多" if pos["side"] == "LONG" else "平空", sig.get("reason"))
        mark = equity
        if pos:
            d = 1 if pos["side"] == "LONG" else -1
            mark = equity + (bar["c"] - pos["entry"]) * pos["qty"] * d
        peak = max(peak, mark)
        dd = (peak - mark) / peak if peak else 0
        max_dd = max(max_dd, dd)
        curve.append({"t": bar["t"], "v": round(mark, 2), "dd": round(dd * 100, 3), "bh": round(equity0 * bar["c"] / c0, 2)})

    open_pos = None
    if pos:
        last = bars[-1]["c"]
        d = 1 if pos["side"] == "LONG" else -1
        open_pos = {"side": pos["side"], "entry": pos["entry"], "entry_t": pos["t"], "stop": pos.get("stop"),
                    "float_pnl": round((last - pos["entry"]) * pos["qty"] * d, 2)}

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gp, gl = sum(t["pnl"] for t in wins), -sum(t["pnl"] for t in losses)

    def side_stats(side):
        ts = [t for t in trades if t["side"] == side]
        w = [t for t in ts if t["pnl"] > 0]
        return {"trades": len(ts), "win_rate": round(len(w) / len(ts) * 100, 1) if ts else 0,
                "pnl": round(sum(t["pnl"] for t in ts), 2),
                "avg": round(sum(t["pnl"] for t in ts) / len(ts), 2) if ts else 0}

    def streak(flag):
        best = cur = 0
        for t in trades:
            cur = cur + 1 if (t["pnl"] > 0) == flag else 0
            best = max(best, cur)
        return best

    bar_ms = (bars[1]["t"] - bars[0]["t"]) if len(bars) > 1 else 14400000
    per_year = 365 * 86400000 / bar_ms
    rets = [(curve[i]["v"] / curve[i - 1]["v"] - 1) for i in range(1, len(curve)) if curve[i - 1]["v"]]
    sharpe = sortino = None
    if len(rets) > 5:
        m = sum(rets) / len(rets)
        sd = math.sqrt(sum((r - m) ** 2 for r in rets) / (len(rets) - 1))
        dsd = math.sqrt(sum(min(r, 0) ** 2 for r in rets) / len(rets))
        sharpe = round(m / sd * math.sqrt(per_year), 2) if sd else None
        sortino = round(m / dsd * math.sqrt(per_year), 2) if dsd else None
    days = max((bars[-1]["t"] - bars[warmup]["t"]) / 86400000, 1e-9) if len(bars) > warmup else 1
    tot = equity / equity0
    ann = (tot ** (365 / days) - 1) * 100 if tot > 0 and days >= 5 else None
    avg_win = gp / len(wins) if wins else 0
    avg_loss = gl / len(losses) if losses else 0
    stats = {
        "trades": len(trades), "win_rate": round(len(wins) / len(trades) * 100, 1) if trades else 0,
        "total_return": round((tot - 1) * 100, 2), "final_equity": round(equity, 2),
        "annualized": round(ann, 2) if ann is not None else None,
        "max_drawdown": round(max_dd * 100, 2), "profit_factor": round(gp / gl, 2) if gl else None,
        "avg_trade": round(sum(t["pnl"] for t in trades) / len(trades), 2) if trades else 0,
        "sharpe": sharpe, "sortino": sortino,
        "calmar": round(ann / (max_dd * 100), 2) if ann is not None and max_dd > 0 else None,
        "avg_win": round(avg_win, 2), "avg_loss": round(-avg_loss, 2),
        "payoff": round(avg_win / avg_loss, 2) if avg_loss else None,
        "expectancy": round(sum(t["pnl"] for t in trades) / len(trades), 2) if trades else 0,
        "best": round(max((t["pnl"] for t in trades), default=0), 2),
        "worst": round(min((t["pnl"] for t in trades), default=0), 2),
        "max_win_streak": streak(True), "max_loss_streak": streak(False),
        "avg_hold_bars": round(sum(t["bars"] for t in trades) / len(trades), 1) if trades else 0,
        "exposure": round(in_pos_bars / max(len(bars) - warmup, 1) * 100, 1),
        "fees": round(sum(t["fee"] for t in trades), 2),
        "stop_hits": sum(1 for t in trades if t["why"] == "硬止损"),
        "buy_hold": round((bars[-1]["c"] / c0 - 1) * 100, 2) if len(bars) > warmup else 0,
        "bars": len(bars) - warmup, "days": round(days, 1),
    }
    if len(curve) > 4:
        m = len(curve) // 2
        stats["ret_h1"] = round((curve[m]["v"] / equity0 - 1) * 100, 2)
        stats["ret_h2"] = round((curve[-1]["v"] / curve[m]["v"] - 1) * 100, 2)
        bpk, bdd = 0.0, 0.0
        for x in curve:
            bpk = max(bpk, x["bh"])
            bdd = max(bdd, (bpk - x["bh"]) / bpk if bpk else 0)
        stats["bh_dd"] = round(bdd * 100, 2)
    return {"stats": stats, "long": side_stats("LONG"), "short": side_stats("SHORT"), "trades": trades,
            "equity": curve, "open": open_pos}


# ---------------- 2026-10-18：工作台只保留最优方案(宝贝："负收益的都删除，只留下最佳收益的") ----------------
from .plan import BUILTINS as _PLAN_BUILTINS, OPTIMAL  # noqa: E402

BUILTIN.update(_PLAN_BUILTINS)


def list_strategies() -> List[dict]:  # noqa: F811  覆盖上面的全库列表
    return [{"id": k, "title": BUILTIN[k]["title"], "desc": BUILTIN[k]["desc"], "params": BUILTIN[k]["params"],
             "source": "builtin", "cat": "最优方案", "flag": None, "hint": "1d"} for k in OPTIMAL]
