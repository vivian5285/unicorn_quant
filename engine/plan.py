# -*- coding: utf-8 -*-
"""方案F 两条腿的单品种版本，给回测工作台/图表信号用(2026-10-18 宝贝要求"只留下最佳收益的")。
规则与 engine/optimal.py(最优方案页)一致；区别只在于这里是单品种、按收盘价成交(工作台口径)。
  plan_f_crypto：日线EMA8/30多空 + BTC减半周期(牛只多/熊只空/筑底都做；熊市段遇极端底部反转可带3ATR止损做多) + 山寨币跟BTC日线同方向 + 仓位=min(1, 3%÷ATR%)
  plan_f_stock ：美股日线EMA8/30只做多 + 当月行业轮动前5(optimal_data/rotation.json) + 3×ATR硬止损 + 仓位=min(1, 3%÷ATR%)
"""
from __future__ import annotations

import json
import os
import time

from . import bottom, ta
from .optimal import BRK_N, OUT, PHASE_RULE, SEC_OF, phase_of

VOL_TGT = 0.03
_ROT = {"mt": 0, "data": {}}


def _rotation():
    p = os.path.join(OUT, "rotation.json")
    try:
        mt = os.path.getmtime(p)
        if mt != _ROT["mt"]:
            with open(p, "r", encoding="utf-8") as f:
                _ROT["data"] = json.load(f)
            _ROT["mt"] = mt
    except Exception:
        pass
    return _ROT["data"]


def _day(ms):
    return time.strftime("%Y-%m-%d", time.gmtime(ms / 1000))


def _sig(bar, action, reason, **kw):
    return {"price": bar["c"], "bar_time": bar["t"], "action": action, "reason": reason, **kw}


def plan_f_crypto(bars_by_tf, params, position):
    p = params or {}
    bars = bars_by_tf["base"]
    if len(bars) < 40:
        return None
    c = [x["c"] for x in bars]
    e8, e30 = ta.ema(c, 8), ta.ema(c, 30)
    if e8[-1] is None or e30[-1] is None:
        return None
    b = bars[-1]
    up = e8[-1] > e30[-1]
    raw = 1 if up else -1
    ph, mo = phase_of(b["t"])
    ph_txt = f"减半后第{mo:.0f}个月·{ph}市({PHASE_RULE[ph]})"
    ema_txt = f"{_day(b['t'])}收盘 EMA8{'>' if up else '<'}EMA30"
    allowed = not ((ph == "牛" and raw < 0) or (ph == "熊" and raw > 0))
    sym = str(p.get("symbol") or "").upper()
    btc_ok, btc_txt, bu = True, "", None
    if sym and sym != "BTCUSDT":
        try:
            from strategy_engine import klines        # 回测时已被替换成"只看得到当时已收盘"的历史
            bb = klines.get_bars("BTCUSDT", "1d", 80)
            bc = [x["c"] for x in bb]
            b8, b30 = ta.ema(bc, 8), ta.ema(bc, 30)
            bu = b8[-1] > b30[-1]
            btc_txt = f"BTC日线{'多头' if bu else '空头'}"
            btc_ok = (raw > 0) == bu
        except Exception:
            btc_ok, btc_txt = False, "BTC日线数据缺失"
    side = {"LONG": 1, "SHORT": -1}.get((position or {}).get("side"))
    a = ta.atr(bars, 14)[-1] or 0
    if side and (position or {}).get("brk"):       # 突破单：均线跟上就转成趋势单(撤硬止损)，跟上之前继续拿(靠3×ATR止损)
        if raw == side:
            return _sig(b, "CLEAR_STOP", f"；{_day(b['t'])} 均线跟上，转为趋势单(撤掉硬止损)")
        return None
    size = min(1.0, VOL_TGT / (a / b["c"])) if a else 1.0
    word = lambda d: "多" if d > 0 else "空"
    extra = {}
    ex_txt = ""
    if ph == "熊" and raw > 0:                     # 熊市段唯一例外：极端底部反转(engine/bottom.py)
        ent_t = (position or {}).get("entry_bar_time")
        if side == 1 and ent_t and phase_of(int(ent_t))[0] == "熊":
            return None                            # 已持有的底部反转多单：均线还是多头就继续拿
        if side != 1:
            ok_b, br_txt = bottom.check(bars)
            if ok_b:
                allowed = True
                extra = {"stop_loss": b["c"] - 3.0 * a} if a else {}
                ex_txt = f"；熊市段底部反转例外【{br_txt}】；3×ATR硬止损"
    open_reason = f"{ema_txt} → 开{word(raw)}；{ph_txt}" + (f"；{btc_txt}同向" if btc_txt else "") + ex_txt + f"；ATR%={100 * a / b['c']:.1f}，仓位×{size:.2f}"
    if side:
        if raw == side and allowed:
            return None
        if raw != side and allowed and btc_ok:
            return _sig(b, "LONG" if raw > 0 else "SHORT", "反手：" + open_reason, size_frac=size, **extra)
        if raw != side and not allowed:
            why = f"{ema_txt}，均线翻{word(raw)}；{ph_txt}，不开{word(raw)}，只平{word(side)}"
        elif raw != side:
            why = f"{ema_txt}，均线翻{word(raw)}，但{btc_txt}不同向，只平仓不反手"
        else:
            why = f"进入{ph_txt}，不再持{word(side)}，平仓"
        return _sig(b, "CLOSE_QUICK_EXIT", why)
    if allowed and btc_ok:
        return _sig(b, "LONG" if raw > 0 else "SHORT", open_reason, size_frac=size, **extra)
    # 2026-10-18 同步 optimal.py 盘中突破单：均线还没交叉，先挂明天的突破单(前BRK_N日高/低)，触价就进场+3×ATR硬止损
    if len(bars) > BRK_N and a:
        is_btc = sym in ("", "BTCUSDT")
        if raw < 0 and (ph != "熊" or bottom.check(bars)[0]) and (is_btc or bu):
            lvl = max(x["h"] for x in bars[-BRK_N:])
            return {"price": b["c"], "bar_time": b["t"], "action": "ORDER", "order_side": "LONG", "order_price": lvl, "stop_dist": 3.0 * a, "size_frac": size,
                    "reason": f"{_day(b['t'])}收盘后挂突破单：明天涨破前{BRK_N}日最高 {lvl:.6g} 就开多(不等EMA8上穿EMA30)；{ph_txt}；ATR%={100 * a / b['c']:.1f}，仓位×{size:.2f}"}
        if raw > 0 and ph != "牛" and (is_btc or bu is False):
            lvl = min(x["l"] for x in bars[-BRK_N:])
            return {"price": b["c"], "bar_time": b["t"], "action": "ORDER", "order_side": "SHORT", "order_price": lvl, "stop_dist": 3.0 * a, "size_frac": size,
                    "reason": f"{_day(b['t'])}收盘后挂突破单：明天跌破前{BRK_N}日最低 {lvl:.6g} 就开空(不等EMA8下穿EMA30)；{ph_txt}；ATR%={100 * a / b['c']:.1f}，仓位×{size:.2f}"}
    return None


def plan_f_stock(bars_by_tf, params, position):
    p = params or {}
    bars = bars_by_tf["base"]
    if len(bars) < 40:
        return None
    c = [x["c"] for x in bars]
    e8, e30 = ta.ema(c, 8), ta.ema(c, 30)
    if e8[-1] is None or e30[-1] is None:
        return None
    b = bars[-1]
    up = e8[-1] > e30[-1]
    tk = str(p.get("symbol") or "").upper()
    tk = tk[:-4] if tk.endswith("USDT") else tk
    sec = SEC_OF.get(tk)
    rot = _rotation()
    g = time.gmtime(b["t"] / 1000)
    month = "%04d-%02d" % (g.tm_year, g.tm_mon)
    chosen = (rot.get("months") or {}).get(month)
    if sec and chosen is not None:
        in_rot = sec in chosen
        rot_txt = f"{month}行业前5：{'、'.join(chosen)}；{tk}属于{sec}" + ("" if in_rot else "(不在前5)")
    else:
        in_rot = True                                  # 不在78只行业表里的股票：只看均线
        rot_txt = f"{tk}不在行业轮动表里，只按均线"
    ema_txt = f"{_day(b['t'])}收盘 EMA8{'>' if up else '<'}EMA30"
    if position:
        if not up:
            return _sig(b, "CLOSE_QUICK_EXIT", f"{ema_txt}，平多")
        if not in_rot:
            return _sig(b, "CLOSE_QUICK_EXIT", f"{month} {sec}掉出行业前5(新名单：{'、'.join(chosen)})，平多")
        return None
    if up and in_rot:
        a = ta.atr(bars, 14)[-1] or 0
        if not a:
            return None
        size = min(1.0, VOL_TGT / (a / b["c"]))
        return _sig(b, "LONG", f"{rot_txt}；{ema_txt} → 做多；ATR%={100 * a / b['c']:.1f}，仓位×{size:.2f}",
                    size_frac=size, stop_loss=b["c"] - 3.0 * a)
    return None



# ---------------- 2026-10-17 B方案的两条 8h 子腿(2026-10-19 起三条子腿各占币圈腿 1/3) ----------------
import math as _math


def _st_dir_list(bars, n=10, m=3.0):
    A = ta.atr(bars, n); d = [1] * len(bars); ub = lb = None
    for i, x in enumerate(bars):
        a = A[i] or 0; hl = (x["h"] + x["l"]) / 2; u = hl + m * a; l = hl - m * a
        if i == 0:
            ub, lb = u, l; continue
        pc = bars[i - 1]["c"]
        ub = u if (u < ub or pc > ub) else ub; lb = l if (l > lb or pc < lb) else lb
        d[i] = 1 if x["c"] > ub else (-1 if x["c"] < lb else d[i - 1])
    return d


def _bb_state_list(bars, n=25, k=1.25):   # 2026-10-19 宝贝采纳：布林(20,2)→(25,1.25)，/tmp/bb_grid.py+bb_fine.py 周期20~35×倍数1.0~1.5 整片稳定区12/12过关，取区中心；比值5.39→5.57
    c = [x["c"] for x in bars]; out = [0] * len(c); st = 0
    for i in range(n - 1, len(c)):
        w = c[i - n + 1: i + 1]; mid = sum(w) / n; sd = (sum((v - mid) ** 2 for v in w) / n) ** .5
        if c[i] > mid + k * sd: st = 1
        elif c[i] < mid - k * sd: st = -1
        elif (st > 0 and c[i] < mid) or (st < 0 and c[i] > mid): st = 0
        out[i] = st
    return out


def _plan_8h(kind, bars_by_tf, params, position):
    p = params or {}
    bars = bars_by_tf["base"]
    if len(bars) < 80:
        return None
    d = (_st_dir_list(bars) if kind == "st" else _bb_state_list(bars))[-1]
    b = bars[-1]
    close_ms = b["t"] + 8 * 3600000
    ph, mo = phase_of(close_ms)
    ph_txt = f"减半后第{mo:.0f}个月·{ph}市({PHASE_RULE[ph]})"
    ts = time.strftime("%Y-%m-%d %H:%M", time.gmtime(close_ms / 1000))
    name = "8h超级趋势" if kind == "st" else "8h布林"
    sig_txt = (f"{ts} {name}{'转多' if d > 0 else '转空'}" if kind == "st" else
               f"{ts} 8h收盘" + {1: "突破布林上轨", -1: "跌破布林下轨", 0: "回到布林中轨"}[d])
    allowed = lambda x: not ((ph == "牛" and x < 0) or (ph == "熊" and x > 0))
    side = {"LONG": 1, "SHORT": -1}.get((position or {}).get("side"))
    if side:
        if d != side:
            return _sig(b, "CLOSE_QUICK_EXIT", sig_txt + "，离场")
        if not allowed(side):
            return _sig(b, "CLOSE_QUICK_EXIT", f"进入{ph_txt}，不再持{'多' if side > 0 else '空'}，平仓")
        return None
    if d == 0 or not allowed(d):
        return None
    sym = str(p.get("symbol") or "").upper()
    btc_txt = ""
    if sym and sym != "BTCUSDT":
        try:
            from strategy_engine import klines
            bb = [x for x in klines.get_bars("BTCUSDT", "1d", 80) if x["t"] + 86400000 <= close_ms]
            bc = [x["c"] for x in bb]; b8, b30 = ta.ema(bc, 8), ta.ema(bc, 30)
            bu = 1 if b8[-1] > b30[-1] else -1
        except Exception:
            return None
        if bu != d:
            return None
        btc_txt = "；BTC日线同向"
    a = ta.atr(bars, 14)[-1] or 0
    if not a:
        return None
    size = min(1.0, VOL_TGT * _math.sqrt(8 / 24) / (a / b["c"]))
    return _sig(b, "LONG" if d > 0 else "SHORT", f"{sig_txt} → 开{'多' if d > 0 else '空'}；{ph_txt}{btc_txt}；ATR%(8h)={100 * a / b['c']:.1f}，仓位×{size:.2f}",
                size_frac=size)


def plan_f_st8h(bars_by_tf, params, position):
    return _plan_8h("st", bars_by_tf, params, position)


def plan_f_bb8h(bars_by_tf, params, position):
    return _plan_8h("bb", bars_by_tf, params, position)


BUILTINS = {
    "plan_f_crypto": {"fn": plan_f_crypto, "title": "方案F·币圈腿 日线EMA8/30多空", "params": {},
                      "desc": "最优方案币圈腿：日线EMA8/30多空+BTC减半周期+山寨跟BTC同向+按波动率调仓(1天周期)"},
    "plan_f_stock": {"fn": plan_f_stock, "title": "方案F·美股腿 行业轮动只做多", "params": {},
                     "desc": "最优方案美股腿：当月行业前5+日线EMA8/30只做多+3ATR止损+按波动率调仓(1天周期)"},
}
BUILTINS["plan_f_st8h"] = {"fn": plan_f_st8h, "title": "方案F·币圈腿B 8h超级趋势", "params": {},
                           "desc": "币圈腿B的1/3：8h超级趋势(10,3)+严格减半周期+山寨跟BTC日线同向+按波动率调仓(8时周期)"}
BUILTINS["plan_f_bb8h"] = {"fn": plan_f_bb8h, "title": "方案F·币圈腿C 8h布林突破", "params": {},
                           "desc": "币圈腿C的1/3：8h收盘突破布林(25,1.25)上/下轨开仓、回中轨离场+严格减半周期+BTC同向+按波动率调仓(8时周期)"}
OPTIMAL = list(BUILTINS)
