# -*- coding: utf-8 -*-
"""极端底部反转判断(2026-10-17)——减半周期"熊市段只做空"的唯一例外。

宝贝："熊市也要看什么底部做多：周线/月线级别 CCI/RSI/KDJ 超卖、MACD 拐头、双均线站上、足够的量能、
相对历史低价，存在极端反转可能，当然可以做多"。

规则(调用方负责"日线EMA8>EMA30"和"山寨币跟BTC同向")，只用信号日之前已收盘的周线：
  ① 近8根已收盘周线里，周RSI14<35 / 周CCI20<-100 / 周KDJ的J<0，三项至少两项出现过
  ② 最后一根已收盘周线 MACD(12,26,9) 柱子比前一根大(拐头)
  ③ 近3天任一天成交量 > 前20日均量 × 1.5(放量)
  ④ 收盘价 ≤ 近365天最高价 × 0.65(离年内高点跌超35%，相对低价)
进场后 3×ATR 硬止损，日线均线翻空就离场。

回测(/root/research/bottom_reversal_20261017.py，主流5个日线2017起，复利，每币1/5资金)：
  现状 收益/回撤3.11 → 加这条例外 3.18，回撤25.1%→25.2%，2026年 +16%→+34%；
  共28笔、胜率21%，靠少数大反转(2019-01 BNB +201%、2026-07 ETH +30%、2026-08 BNB +25%)，2022年熊市12笔几乎全亏但都是小亏。
  放宽(只要1项超卖 / 不看放量)会变差，所以条件必须严。
"""
from __future__ import annotations

import time
from typing import List, Optional, Tuple

DAY = 86400000


def _ema(xs, n):
    k = 2 / (n + 1); o = []; e = None
    for x in xs:
        e = x if e is None else e + k * (x - e); o.append(e)
    return o


def _rsi(c, n=14):
    o = [50.0] * len(c); g = l = None
    for i in range(1, len(c)):
        d = c[i] - c[i - 1]; up, dn = max(d, 0), max(-d, 0)
        g = up if g is None else (g * (n - 1) + up) / n; l = dn if l is None else (l * (n - 1) + dn) / n
        o[i] = 100.0 if l == 0 else 100 - 100 / (1 + g / l)
    return o


def _cci(h, l, c, n=20):
    tp = [(a + b + z) / 3 for a, b, z in zip(h, l, c)]; o = [0.0] * len(c)
    for i in range(n - 1, len(c)):
        w = tp[i - n + 1: i + 1]; m = sum(w) / n; md = sum(abs(v - m) for v in w) / n
        o[i] = (tp[i] - m) / (0.015 * md) if md else 0
    return o


def _kdj_j(h, l, c, n=9):
    k = d = 50.0; o = []
    for i in range(len(c)):
        hh = max(h[max(0, i - n + 1): i + 1]); ll = min(l[max(0, i - n + 1): i + 1])
        rsv = 100 * (c[i] - ll) / (hh - ll) if hh > ll else 50
        k = 2 / 3 * k + rsv / 3; d = 2 / 3 * d + k / 3; o.append(3 * k - 2 * d)
    return o


def check(bars: List[dict]) -> Tuple[bool, str]:
    """bars：日线(含 t/h/l/c/v)，最后一根是信号日。返回(是否放行, 说明文字)。"""
    if len(bars) < 260:
        return False, "日线不足260根，无法判断底部"
    wk = lambda t: (int(t) // DAY + 3) // 7                     # 周一为一周开始
    cur = wk(bars[-1]["t"])
    W = []
    for x in bars:
        k = wk(x["t"])
        if k >= cur:
            break                                               # 只用信号日之前已收盘的周线
        if W and W[-1][0] == k:
            w = W[-1]; w[1] = max(w[1], x["h"]); w[2] = min(w[2], x["l"]); w[3] = x["c"]
        else:
            W.append([k, x["h"], x["l"], x["c"]])
    if len(W) < 35:
        return False, "周线不足35根"
    h, l, c = [w[1] for w in W], [w[2] for w in W], [w[3] for w in W]
    r, cc, j = _rsi(c), _cci(h, l, c), _kdj_j(h, l, c)
    m12, m26 = _ema(c, 12), _ema(c, 26); dif = [a - z for a, z in zip(m12, m26)]; dea = _ema(dif, 9)
    hist = [a - z for a, z in zip(dif, dea)]
    lo = range(len(W) - 8, len(W))
    rmin, cmin, jmin = min(r[i] for i in lo), min(cc[i] for i in lo), min(j[i] for i in lo)
    osc = (rmin < 35) + (cmin < -100) + (jmin < 0)
    macd_up = hist[-1] > hist[-2]
    vol = [float(x.get("v") or 0) for x in bars]
    vr = 0.0
    for k in range(3):
        i = len(bars) - 1 - k
        base = sum(vol[i - 20:i]) / 20 if i >= 20 else 0
        if base > 0:
            vr = max(vr, vol[i] / base)
    hi365 = max(x["h"] for x in bars[-365:])
    off = 1 - bars[-1]["c"] / hi365
    ok = osc >= 2 and macd_up and vr > 1.5 and off >= 0.35
    txt = (f"周RSI最低{rmin:.0f}{'✓' if rmin < 35 else '✗'} 周CCI最低{cmin:.0f}{'✓' if cmin < -100 else '✗'} "
           f"周KDJ-J最低{jmin:.0f}{'✓' if jmin < 0 else '✗'}(超卖{osc}/3) · 周MACD{'拐头✓' if macd_up else '未拐头✗'} · "
           f"量能{vr:.1f}倍{'✓' if vr > 1.5 else '✗'} · 距年高-{100 * off:.0f}%{'✓' if off >= 0.35 else '✗'}")
    return ok, txt
