# -*- coding: utf-8 -*-
"""美股数据层：Yahoo Finance 公开图表接口（日线多年、小时线约2年），磁盘缓存，输出与币安K线同格式。

价格口径：Yahoo默认返回"拆股调整后"的OHLC（不含分红再投资），回测不含分红收益，对高股息股票略偏保守。
时间：美股常规交易时段（不含盘前盘后）。
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Dict, List, Optional

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.environ.get("UQ_STOCK_CACHE") or os.path.join(HERE, "..", "stock_cache")
UA = {"User-Agent": "Mozilla/5.0"}

# 知名美股 / ETF（可自行增删）。第三项为板块，用于结果分组
UNIVERSE = [
    ("AAPL", "苹果", "科技"), ("MSFT", "微软", "科技"), ("NVDA", "英伟达", "半导体"), ("GOOGL", "谷歌", "科技"),
    ("AMZN", "亚马逊", "科技"), ("META", "Meta", "科技"), ("TSLA", "特斯拉", "汽车"), ("AMD", "AMD", "半导体"),
    ("AVGO", "博通", "半导体"), ("TSM", "台积电", "半导体"), ("ASML", "ASML", "半导体"), ("MU", "美光", "半导体"),
    ("INTC", "英特尔", "半导体"), ("NFLX", "奈飞", "科技"), ("ORCL", "甲骨文", "科技"), ("CRM", "Salesforce", "科技"),
    ("PLTR", "Palantir", "科技"), ("COIN", "Coinbase", "金融科技"), ("JPM", "摩根大通", "金融"), ("V", "Visa", "金融"),
    ("UNH", "联合健康", "医疗"), ("LLY", "礼来", "医疗"), ("XOM", "埃克森美孚", "能源"), ("WMT", "沃尔玛", "消费"),
    ("COST", "好市多", "消费"), ("BA", "波音", "工业"), ("DIS", "迪士尼", "消费"),
    ("SPY", "标普500 ETF", "ETF"), ("QQQ", "纳指100 ETF", "ETF"), ("IWM", "罗素2000 ETF", "ETF"),
]
SYMBOLS: Dict[str, dict] = {s: {"name": n, "sector": sec} for s, n, sec in UNIVERSE}
# 趋势突破扫描器的扩展标的：这几年跌得惨/横盘的蓝筹也放进来，降低"只看赢家"的幸存者偏差
SCAN_EXTRA = [("PYPL", "PayPal", "金融科技"), ("NKE", "耐克", "消费"), ("SBUX", "星巴克", "消费"), ("VZ", "威瑞森", "通信"),
              ("PFE", "辉瑞", "医疗"), ("CVS", "CVS", "医疗"), ("KO", "可口可乐", "消费"), ("PEP", "百事", "消费"),
              ("MCD", "麦当劳", "消费"), ("HD", "家得宝", "消费"), ("MRK", "默沙东", "医疗"), ("CSCO", "思科", "科技"),
              ("IBM", "IBM", "科技"), ("GE", "通用电气", "工业"), ("CAT", "卡特彼勒", "工业"), ("GS", "高盛", "金融"),
              ("BAC", "美国银行", "金融"), ("MA", "万事达", "金融"), ("ADBE", "Adobe", "科技"), ("QCOM", "高通", "半导体"),
              ("TXN", "德州仪器", "半导体"), ("UBER", "Uber", "科技"), ("T", "AT&T", "通信"), ("MMM", "3M", "工业"),
              ("DOW", "陶氏", "材料")]
EXTRA_META: Dict[str, dict] = {s: {"name": n, "sector": sec} for s, n, sec in SCAN_EXTRA}
WATCH = [s for s, _, _ in UNIVERSE if s not in ("SPY", "QQQ", "IWM")] + [s for s, _, _ in SCAN_EXTRA]


def meta(symbol: str) -> dict:
    return SYMBOLS.get(symbol.upper()) or EXTRA_META.get(symbol.upper()) or {"name": symbol.upper(), "sector": ""}
INTERVALS = ["1h", "1d", "1w"]
_Y = {"1h": ("60m", "2y"), "1d": ("1d", "10y"), "1w": ("1wk", "20y")}
_TTL = {"1h": 600, "1d": 1800, "1w": 3600}
_lock = threading.Lock()
_mem: dict = {}


def is_stock(symbol: str) -> bool:
    """美股/ETF/商品期货(GC=F)/Yahoo币对(ETH-USD) 走Yahoo；*USDT 走币安"""
    import re
    s = symbol.upper()
    if s in SYMBOLS:
        return True
    return (not s.endswith("USDT")) and re.fullmatch(r"[A-Z0-9^][A-Z0-9.\-=]{0,9}", s) is not None


def _path(symbol: str, interval: str) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, f"{symbol.upper()}_{interval}.json")


def _download(symbol: str, interval: str) -> List[dict]:
    yi, rng = _Y[interval]
    last = None
    for i in range(5):
        host = "query1" if i % 2 == 0 else "query2"
        url = f"https://{host}.finance.yahoo.com/v8/finance/chart/{symbol.upper()}"
        try:
            r = requests.get(url, params={"range": rng, "interval": yi, "includePrePost": "false"}, headers=UA, timeout=20)
            if r.status_code == 404:
                raise ValueError(f"找不到美股代码 {symbol}")
            if r.status_code == 429:
                last = "429限流"
                time.sleep(3 * (i + 1))
                continue
            r.raise_for_status()
            res = (r.json().get("chart") or {}).get("result")
            if not res:
                raise ValueError(f"{symbol} 无数据")
            res = res[0]
            ts, q = res.get("timestamp") or [], res["indicators"]["quote"][0]
            out = []
            for k, t in enumerate(ts):
                o, h, l, c, v = q["open"][k], q["high"][k], q["low"][k], q["close"][k], q["volume"][k]
                if None in (o, h, l, c):
                    continue
                out.append({"t": int(t) * 1000, "o": float(o), "h": float(h), "l": float(l), "c": float(c), "v": float(v or 0)})
            out.sort(key=lambda b: b["t"])
            time.sleep(0.4)  # 对Yahoo温和一点
            return out
        except ValueError:
            raise
        except Exception as e:
            last = e
            time.sleep(1 + i)
    raise RuntimeError(f"美股行情拉取失败: {last}")


def get_klines(symbol: str, interval: str, limit: int = 500, closed_only: bool = False) -> List[dict]:
    symbol = symbol.upper()
    if interval not in INTERVALS:
        raise ValueError(f"美股只支持 {'/'.join(INTERVALS)} 周期（Yahoo数据限制）")
    key = (symbol, interval)
    now = time.time()
    with _lock:
        hit = _mem.get(key)
    if not hit or now - hit[0] > _TTL[interval]:
        p = _path(symbol, interval)
        bars = None
        try:
            if os.path.exists(p) and now - os.path.getmtime(p) < _TTL[interval]:
                with open(p, "r", encoding="utf-8") as f:
                    bars = json.load(f)
        except Exception:
            bars = None
        if bars is None:
            bars = _download(symbol, interval)
            try:
                with open(p + ".tmp", "w", encoding="utf-8") as f:
                    json.dump(bars, f)
                os.replace(p + ".tmp", p)
            except Exception:
                pass
        hit = (now, bars)
        with _lock:
            _mem[key] = hit
    bars = hit[1]
    if closed_only and bars:
        # 日/周线：当天(本周)还没收盘的最后一根丢掉。盘中判断：最后一根的日期==今天(美东) 且 美东时间<16:05
        last = bars[-1]
        age = now - last["t"] / 1000
        span = {"1h": 3600, "1d": 7 * 3600, "1w": 4 * 86400 + 7 * 3600}[interval]
        if age < span:
            bars = bars[:-1]
    return bars[-limit:] if limit else bars


def quote(symbol: str) -> Optional[dict]:
    """最新价与日涨跌（来自日线缓存）"""
    try:
        b = get_klines(symbol, "1d", 5)
        if len(b) >= 2:
            return {"price": b[-1]["c"], "chg": (b[-1]["c"] / b[-2]["c"] - 1) * 100, "t": b[-1]["t"]}
    except Exception:
        return None
    return None
