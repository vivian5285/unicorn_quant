# -*- coding: utf-8 -*-
"""独角兽量化 — 后端（Flask）。Python做行情/指标/复盘/模拟盘/交易所适配，前端用图表库绘制。"""
from __future__ import annotations

import hmac
import json
import os
import secrets
import threading
import time
from functools import wraps

from flask import Flask, jsonify, request, send_from_directory, session

from engine import backtest, data, exchange, paper, scanner, stocks, ta
from engine.retired import RETIRED, NOT_OPTIMAL, GENERATED as RETIRED_AT

HERE = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=os.path.join(HERE, "static"), static_url_path="/static")
app.secret_key = os.environ.get("UQ_SECRET") or secrets.token_hex(32)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax", PERMANENT_SESSION_LIFETIME=86400 * 7)
PASSWORD = os.environ.get("UQ_PASSWORD", "")
_bt_sem = threading.Semaphore(2)
_fails: dict = {}
_bt_cache: dict = {}
_bt_lock = threading.Lock()

MTF_NAME = {"240": "4h", "D": "1d", "1D": "1d", "60": "1h", "720": "12h"}


def _hist(bars):
    return backtest.Hist(lambda sy, iv, n: data.get_klines(sy, iv, n, closed_only=True), bars[-1]["t"] - bars[0]["t"])


def api(fn):
    @wraps(fn)
    def w(*a, **kw):
        public_get = request.method == "GET" and request.path in {
            "/api/symbols", "/api/tickers", "/api/chart", "/api/bar",
            "/api/strategies", "/api/stocks", "/api/scanner",
            "/api/sweep/status", "/api/matrix/status",
            "/api/optimal", "/api/optimal/overlay", "/api/optimal/bars", "/api/retired", "/api/optimal/recon", "/api/optimal/autofix", "/api/planf/live", "/api/planf/track", "/api/sndk_solo",
        }
        public_get = public_get or (request.method == "GET" and request.path.startswith("/api/universe"))   # 2026-10-18 全品种复盘只读
        public_analysis = request.method == "POST" and request.path in {
            "/api/backtest", "/api/compare",
        }
        if not session.get("ok") and not (public_get or public_analysis):
            return jsonify(error="此操作需要登录"), 401
        try:
            return fn(*a, **kw)
        except (ValueError, KeyError) as e:
            return jsonify(error=str(e)), 400
        except PermissionError as e:
            return jsonify(error=str(e)), 403
        except Exception as e:
            app.logger.exception("api error")
            return jsonify(error=f"{type(e).__name__}: {str(e)[:200]}"), 500
    return w


@app.after_request
def headers(r):
    r.headers["X-Content-Type-Options"] = "nosniff"
    r.headers["X-Frame-Options"] = "DENY"
    if request.path.startswith("/api/"):
        r.headers["Cache-Control"] = "no-cache" if r.headers.get("ETag") else "no-store"   # 有ETag的可以缓存但每次先问服务器
    return r


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


# ---------- 登录 ----------
@app.post("/api/login")
def login():
    ip = request.headers.get("X-Real-IP") or request.remote_addr
    n, t0 = _fails.get(ip, (0, time.time()))
    if time.time() - t0 > 600:
        n, t0 = 0, time.time()
    if n >= 8:
        return jsonify(error="尝试过多，请10分钟后再试"), 429
    pw = (request.get_json(silent=True) or {}).get("password", "")
    if PASSWORD and hmac.compare_digest(pw.encode(), PASSWORD.encode()):
        session.permanent = True
        session["ok"] = True
        _fails.pop(ip, None)
        return jsonify(ok=True)
    _fails[ip] = (n + 1, t0)
    return jsonify(error="密码错误"), 401


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@app.get("/api/me")
def me():
    return jsonify(ok=bool(session.get("ok")), configured=bool(PASSWORD), viewer=True)


# ---------- 行情/指标 ----------
@app.get("/api/symbols")
@api
def symbols():
    return jsonify(symbols=data.symbols(), intervals=data.INTERVALS)


@app.get("/api/tickers")
@api
def tickers():
    return jsonify(data.tickers_24h())


def _pack(inds, bars):
    out = []
    for spec in [s for s in inds.split(",") if s]:
        for item in ta.compute(spec, bars):
            def series(arr):
                return [[bars[i]["t"], round(v, 8)] for i, v in enumerate(arr) if v is not None]
            o = {"id": item["id"], "pane": item["pane"], "name": item["name"],
                 "lines": {k: series(v) for k, v in item["lines"].items()}}
            if "hist" in item:
                o["hist"] = series(item["hist"])
            if "dirs" in item:
                o["dirs"] = [[bars[i]["t"], d] for i, d in enumerate(item["dirs"]) if d]
            out.append(o)
    return out


@app.get("/api/chart")
@api
def chart():
    sym = request.args.get("symbol", "BTCUSDT")
    itv = request.args.get("interval", "4h")
    limit = int(request.args.get("limit", 500))
    bars = data.get_klines(sym, itv, limit)
    return jsonify(symbol=sym.upper(), interval=itv, bars=bars,
                   indicators=_pack(request.args.get("ind", ""), bars))


@app.get("/api/bar")
@api
def last_bars():
    sym = request.args.get("symbol", "BTCUSDT")
    itv = request.args.get("interval", "4h")
    return jsonify(bars=data.get_klines(sym, itv, 10)[-2:])


# ---------- 策略/复盘 ----------
@app.get("/api/strategies")
@api
def strategies():
    return jsonify(strategies=backtest.list_strategies())


@app.post("/api/backtest")
@api
def run_backtest():
    b = request.get_json(silent=True) or {}
    sym, itv, name = b.get("symbol", "BTCUSDT"), b.get("interval", "4h"), b.get("strategy", "dual_ema_band")
    limit = max(100, min(int(b.get("limit", 1500)), 6000))
    if name in RETIRED:
        raise ValueError(f"{name} 已淘汰(负收益，不再使用)：{RETIRED[name]}")
    if name in NOT_OPTIMAL:
        raise ValueError(f"{name} 已下架(不是最优)：{NOT_OPTIMAL[name]}")
    lev = float(b.get("leverage", 1))
    if not (0 < lev <= 20):
        raise ValueError("杠杆范围 0-20x")
    bars = data.get_klines(sym, itv, limit, closed_only=True)
    # 2026-10-18 币安美股永续上线晚(MUUSDT 2026-05起)：回测美股腿时，上线前用正股日线补上，预热和开仓与 /best 一致
    if name == "plan_f_stock" and itv == "1d" and sym.upper().endswith("USDT") and bars:
        try:
            yb = stocks.get_klines(sym.upper()[:-4], "1d", 600, closed_only=True)
            pre = [{"t": x["t"] // 86400000 * 86400000, "o": x["o"], "h": x["h"], "l": x["l"], "c": x["c"], "v": x.get("v", 0)}
                   for x in yb if x["t"] // 86400000 * 86400000 < bars[0]["t"]]
            bars = pre + list(bars)
        except Exception:
            pass
    if len(bars) < 100:
        raise ValueError("K线不足，无法复盘")
    mtf = None
    if name not in backtest.BUILTIN and not stocks.is_stock(sym):
        names = {"240", "D"}  # 多周期策略常用的4H/日线，默认都给
        try:
            from strategy_engine.symbol_registry import get_symbol_config
            cfg = get_symbol_config(sym).get("mtf") or {}
            names |= set(cfg.keys() if isinstance(cfg, dict) else cfg)
        except Exception:
            pass
        mtf = {}
        for n in names:
            iv = MTF_NAME.get(str(n), str(n))
            if iv in data.INTERVALS:
                try:
                    mtf[n] = data.get_klines(sym, iv, 1500, closed_only=True)
                except Exception:
                    pass
    key = (sym.upper(), itv, name, json.dumps(b.get("params") or None, sort_keys=True), limit, lev, bool(b.get("long_only")),
           float(b.get("equity", 10000)), bars[-1]["t"])
    with _bt_lock:
        hit = _bt_cache.get(key)
    if hit and time.time() - hit[0] < 300:
        return jsonify(hit[1])
    with _bt_sem:
        res = backtest.run(bars, name, b.get("params") or None, mtf, float(b.get("equity", 10000)), lev,
                           window=(600 if name.startswith("plan_f") else 260) if name in backtest.BUILTIN else 600, symbol=sym, hist=_hist(bars),
                           long_only=bool(b.get("long_only")))
    res["symbol"], res["interval"], res["strategy"] = sym.upper(), itv, name
    res["range"] = [bars[0]["t"], bars[-1]["t"]]
    with _bt_lock:
        _bt_cache[key] = (time.time(), res)
        if len(_bt_cache) > 60:
            for k in sorted(_bt_cache, key=lambda k: _bt_cache[k][0])[:20]:
                _bt_cache.pop(k, None)
    return jsonify(res)


@app.post("/api/compare")
@api
def compare():
    """同一品种+周期下批量跑多个策略，返回统计对比（最多10个）。"""
    b = request.get_json(silent=True) or {}
    sym, itv = b.get("symbol", "BTCUSDT"), b.get("interval", "4h")
    names = [n for n in (b.get("strategies") or []) if n not in RETIRED and n not in NOT_OPTIMAL][:10]
    limit = max(100, min(int(b.get("limit", 1500)), 6000))
    bars = data.get_klines(sym, itv, limit, closed_only=True)
    rows = []
    with _bt_sem:
        for n in names:
            try:
                p = backtest.BUILTIN[n]["params"] if n in backtest.BUILTIN else None
                r = backtest.run(bars, n, p, None, 10000, 1.0)
                rows.append({"id": n, "stats": r["stats"]})
            except Exception as e:
                rows.append({"id": n, "error": str(e)[:80]})
    return jsonify(rows=rows, symbol=sym.upper(), interval=itv)


# ---------- 全库扫描（后台子进程，结果落盘，多worker共享） ----------
SWEEP_DIR = os.path.join(HERE, "sweeps")


def _sweep_path(sym, itv, limit):
    os.makedirs(SWEEP_DIR, exist_ok=True)
    return os.path.join(SWEEP_DIR, f"{sym.upper()}_{itv}_{limit}.json")


def _read_sweep(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            st = json.load(f)
    except Exception:
        return None
    if st.get("status") == "running" and time.time() - st.get("updated", 0) > 180:
        st["status"] = "stalled"
    return st


@app.post("/api/sweep/start")
@api
def sweep_start():
    import subprocess
    import sys
    b = request.get_json(silent=True) or {}
    sym, itv = b.get("symbol", "BTCUSDT").upper(), b.get("interval", "4h")
    limit = max(300, min(int(b.get("limit", 1000)), 3000))
    path = _sweep_path(sym, itv, limit)
    cur = _read_sweep(path)
    if cur and cur.get("status") == "running":
        return jsonify(cur)
    running = [f for f in os.listdir(SWEEP_DIR) if f.endswith(".json") and (_read_sweep(os.path.join(SWEEP_DIR, f)) or {}).get("status") == "running"]
    if running:
        raise ValueError("已有一个扫描在运行，请等它结束")
    subprocess.Popen([sys.executable, os.path.join(HERE, "sweep.py"), sym, itv, str(limit)], cwd=HERE,
                     stdout=subprocess.DEVNULL, stderr=open(os.path.join(SWEEP_DIR, "sweep.err"), "a"),
                     start_new_session=True)
    time.sleep(0.6)
    return jsonify(_read_sweep(path) or {"status": "starting", "rows": [], "done": 0, "total": 0})


@app.get("/api/sweep/status")
@api
def sweep_status():
    sym, itv = request.args.get("symbol", "BTCUSDT").upper(), request.args.get("interval", "4h")
    limit = int(request.args.get("limit", 1000))
    return jsonify(_read_sweep(_sweep_path(sym, itv, limit)) or {"status": "none", "rows": [], "done": 0, "total": 0})


@app.get("/api/stocks")
@api
def stock_list():
    from concurrent.futures import ThreadPoolExecutor
    syms = list(stocks.SYMBOLS)
    with ThreadPoolExecutor(6) as ex:
        qs = list(ex.map(stocks.quote, syms))
    return jsonify(stocks=[{"symbol": k, **stocks.SYMBOLS[k], **(q or {})} for k, q in zip(syms, qs)],
                   intervals=stocks.INTERVALS)


def _matrix_path(itv, limit):
    os.makedirs(SWEEP_DIR, exist_ok=True)
    return os.path.join(SWEEP_DIR, f"US_{itv}_{limit}.json")


@app.post("/api/matrix/start")
@api
def matrix_start():
    import subprocess
    import sys
    b = request.get_json(silent=True) or {}
    itv = b.get("interval", "1d")
    if itv not in stocks.INTERVALS:
        raise ValueError("周期不支持")
    limit = max(300, min(int(b.get("limit", 1260)), 3000))
    syms = ",".join(x.upper() for x in (b.get("symbols") or []))
    path = _matrix_path(itv, limit)
    cur = _read_sweep(path)
    if cur and cur.get("status") == "running":
        return jsonify({k: v for k, v in cur.items() if k != "rows"})
    for f in os.listdir(SWEEP_DIR):
        if f.endswith(".json") and (_read_sweep(os.path.join(SWEEP_DIR, f)) or {}).get("status") == "running":
            raise ValueError("已有一个扫描在运行，请等它结束")
    subprocess.Popen([sys.executable, os.path.join(HERE, "sweep_stocks.py"), itv, str(limit), syms], cwd=HERE,
                     stdout=subprocess.DEVNULL, stderr=open(os.path.join(SWEEP_DIR, "sweep.err"), "a"),
                     start_new_session=True)
    time.sleep(0.8)
    cur = _read_sweep(path) or {"status": "starting", "done": 0, "total": 0}
    return jsonify({k: v for k, v in cur.items() if k != "rows"})


@app.get("/api/matrix/status")
@api
def matrix_status():
    itv, limit = request.args.get("interval", "1d"), int(request.args.get("limit", 1260))
    st = _read_sweep(_matrix_path(itv, limit)) or {"status": "none", "rows": [], "done": 0, "total": 0}
    if request.args.get("lite"):
        st = {k: v for k, v in st.items() if k != "rows"}
    return jsonify(st)


# ---------- 美股趋势突破扫描器 ----------
def _sc_lock():
    return os.path.join(scanner.DIR, "running.lock")


@app.get("/api/scanner")
@api
def sc_get():
    scan = None
    try:
        with open(os.path.join(scanner.DIR, "latest.json"), "r", encoding="utf-8") as f:
            scan = json.load(f)
    except Exception:
        pass
    running = os.path.exists(_sc_lock()) and time.time() - os.path.getmtime(_sc_lock()) < 600
    return jsonify(scan=scan, paper=scanner.paper_view(scan), running=running)


@app.post("/api/scanner/refresh")
@api
def sc_refresh():
    import subprocess
    import sys
    os.makedirs(scanner.DIR, exist_ok=True)
    if os.path.exists(_sc_lock()) and time.time() - os.path.getmtime(_sc_lock()) < 600:
        return jsonify(running=True)
    subprocess.Popen([sys.executable, os.path.join(HERE, "scanner_run.py")], cwd=HERE,
                     stdout=open(os.path.join(scanner.DIR, "scanner.log"), "a"), stderr=subprocess.STDOUT, start_new_session=True)
    return jsonify(running=True)


@app.post("/api/scanner/reset")
@api
def sc_reset():
    if not (request.get_json(silent=True) or {}).get("confirm"):
        raise ValueError("需要确认")
    scanner.reset_paper()
    return jsonify(ok=True)


# ---------- 模拟盘 ----------
@app.get("/api/paper")
@api
def paper_state():
    return jsonify(paper.snapshot())


@app.post("/api/paper/open")
@api
def paper_open():
    b = request.get_json(silent=True) or {}
    if stocks.is_stock(b.get("symbol", "")):
        raise ValueError("美股仅支持看图与回测，暂不支持下单")
    r = paper.open_pos(b["symbol"], b["side"], float(b["usdt"]), float(b.get("leverage", 1)))
    return jsonify(r)


@app.post("/api/paper/close")
@api
def paper_close():
    return jsonify(paper.close_pos((request.get_json(silent=True) or {})["symbol"]))


@app.post("/api/paper/reset")
@api
def paper_reset():
    paper.reset()
    return jsonify(ok=True)


# ---------- 交易所 ----------
@app.get("/api/exchange/status")
@api
def ex_status():
    return jsonify(exchange.status())


@app.post("/api/exchange/order")
@api
def ex_order():
    b = request.get_json(silent=True) or {}
    if stocks.is_stock(b.get("symbol", "")):
        raise ValueError("美股仅支持看图与回测，暂不支持下单")
    return jsonify(exchange.place_market(b["symbol"], b["side"], float(b["usdt"]), float(b.get("leverage", 1)),
                                         bool(b.get("confirm"))))


# ---------- 最优方案(方案F)复盘，数据由 optimal_run.py 每天生成 ----------
OPT_DIR = os.path.join(HERE, "optimal_data")


_gz_cache: dict = {}


def _json_file(p):
    """返回 JSON 文件；浏览器支持时用 gzip(复盘数据约1.4MB)。按文件修改时间缓存压缩结果。"""
    import gzip
    mt = os.path.getmtime(p)
    hit = _gz_cache.get(p)
    if not hit or hit[0] != mt:
        with open(p, "rb") as f:
            raw = f.read()
        hit = (mt, raw, gzip.compress(raw, 6))
        _gz_cache[p] = hit
    etag = f'"{os.path.basename(p)}-{int(mt)}"'          # 2026-10-19 数据没变就回304，刷新页面不用重新下载几MB
    if etag in (request.headers.get("If-None-Match") or ""):
        r = app.response_class(status=304); r.headers["ETag"] = etag; return r
    if "gzip" in (request.headers.get("Accept-Encoding") or ""):
        r = app.response_class(hit[2], mimetype="application/json")
        r.headers["Content-Encoding"] = "gzip"
        r.headers["Vary"] = "Accept-Encoding"
    else:
        r = app.response_class(hit[1], mimetype="application/json")
    r.headers["ETag"] = etag
    return r


@app.get("/best")
def best_page():
    return send_from_directory(app.static_folder, "best.html")


@app.get("/api/optimal")
@api
def optimal_get():
    p = os.path.join(OPT_DIR, "optimal.json")
    if not os.path.exists(p):
        raise ValueError("最优方案数据还没生成(optimal_run.py)")
    return _json_file(p)


@app.get("/api/optimal/overlay")
@api
def optimal_overlay():
    """2026-11-01 第四批候选叠加(真假突破4h+布林擠壓8h，已按"两者合并1%"上E账户实盘)：主方案日收益 + 叠加日收益(回测，2022-03~2026-09)，
    叠加数据以外的日子 = 主方案本身。FO=合并1%(实盘口径)，FO2=合并2%。另给同窗口(叠加数据覆盖的那段)对比。"""
    from engine import optimal as O
    d = json.load(open(os.path.join(OPT_DIR, "optimal.json"), encoding="utf-8"))
    ovp = os.path.join(OPT_DIR, "overlay_series.json")
    if not os.path.exists(ovp):
        raise ValueError("叠加回测数据还没上传")
    ov = json.load(open(ovp, encoding="utf-8"))
    brk = {t // O.DAY: v for t, v in ov["brk"]}; mio = {t // O.DAY: v for t, v in ov["mio"]}
    F = {t // O.DAY: v for t, v in d["series"]["F"]["series"]}
    ks = sorted(F); out = {}
    for name, m in (("FO", 0.5), ("FO2", 1.0)):
        cur = {ks[0]: 1.0}
        for a, b in zip(ks, ks[1:]):
            r = F[b] / F[a] - 1 + m * (brk.get(b, 0.0) + mio.get(b, 0.0))
            cur[b] = cur[a] * (1 + r)
        out[name] = O.stats(cur)
    w0, w1 = min(brk), max(brk)

    def win(curve):
        kk = [k for k in sorted(curve) if w0 - 1 <= k <= w1]; v = [curve[k] for k in kk]
        pk = dd = 0.0
        for x in v:
            pk = max(pk, x); dd = max(dd, 1 - x / pk)
        cg = (v[-1] / v[0]) ** (365.25 / (kk[-1] - kk[0])) - 1
        return {"cagr": round(100 * cg, 1), "max_dd": round(100 * dd, 1), "ratio": round(cg / dd, 2) if dd else None}
    curves = {"F": F}
    for name in ("FO", "FO2"):
        curves[name] = {t // O.DAY: v for t, v in out[name]["series"]}
    out["window"] = {"start": ov["start"], "end": ov["end"], **{k: win(c) for k, c in curves.items()}}
    out["note"] = ov.get("note")
    return jsonify(out)


@app.get("/api/optimal/bars")
@api
def optimal_bars():
    import re
    sym = request.args.get("symbol", "").upper()
    if not re.fullmatch(r"[A-Z0-9]{1,12}", sym):
        raise ValueError("品种代码不合法")
    p = os.path.join(OPT_DIR, f"bars_{sym}.json")
    if not os.path.exists(p):
        raise ValueError("没有这个品种的复盘K线")
    return _json_file(p)


@app.get("/api/optimal/recon")
@api
def optimal_recon():
    p = os.path.join(OPT_DIR, "recon.json")
    if not os.path.exists(p):
        raise ValueError("对账还没生成")
    return _json_file(p)


# ---------- 2026-10-18 全品种复盘：数据 = optimal.json 同一份交易，engine/universe.py 汇总 ----------
@app.get("/universe")
def universe_page():
    return send_from_directory(app.static_folder, "universe.html")


@app.get("/u/<sym>")
def sym_page(sym):
    return send_from_directory(app.static_folder, "sym.html")


@app.get("/api/universe")
@api
def universe_get():
    p = os.path.join(OPT_DIR, "universe.json")
    if not os.path.exists(p):
        raise ValueError("全品种复盘数据还没生成(optimal_run.py)")
    return _json_file(p)


_uidx = {"mt": 0, "v": None}


@app.get("/api/universe/index")
@api
def universe_index():
    p = os.path.join(OPT_DIR, "universe.json")
    mt = os.path.getmtime(p)
    if _uidx["mt"] != mt:
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
        _uidx.update(mt=mt, v=[[s["sym"], s["sector"], s["kind"], s["tradable"]] for s in d["symbols"]])
    return jsonify(symbols=_uidx["v"])


@app.get("/api/universe/<sym>")
@api
def universe_sym(sym):
    import re
    sym = sym.upper()
    if not re.fullmatch(r"[A-Z0-9]{1,12}", sym):
        raise ValueError("品种代码不合法")
    p = os.path.join(OPT_DIR, "u", f"{sym}.json")
    if not os.path.exists(p):
        raise ValueError(f"没有 {sym} 的复盘数据")
    return _json_file(p)


@app.get("/api/optimal/autofix")
@api
def optimal_autofix():
    """2026-10-19 擂台↔回测 自动对账修复器的状态和最近记录(engine/reconciler.py 每小时写)"""
    def rd(name, default):
        try:
            with open(os.path.join(OPT_DIR, name), "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
    h = rd("recon_health.json", {}); h.pop("history", None)
    return jsonify(health=h, actions=rd("recon_actions.json", [])[-40:][::-1], overrides=rd("recon_override.json", {}),
                   data_health=rd("data_health.json", {}))     # 2026-10-19 复刻健康检查(engine/health.py)


@app.get("/api/planf/track")
@api
def planf_track():
    """2026-10-30 方案F实盘跟踪：回测↔擂台↔实盘三层偏差 + 实盘收益拆解(engine/planf_track.py 每小时写，只读)"""
    try:
        with open(os.path.join(OPT_DIR, "planf_track.json"), "r", encoding="utf-8") as f:
            return jsonify(json.load(f))
    except Exception:
        return jsonify(ok=False, error="还没有跟踪数据")


@app.get("/api/planf/live")
@api
def planf_live():
    """2026-10-19 方案F实盘执行器状态(/var/lib/planf，执行器以账户用户身份写；不含任何密钥)"""
    def rd(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    t = rd("/var/lib/planf/targets.json") or {}
    return jsonify(E=rd("/var/lib/planf/live_E_status.json"), signals={k: t.get(k) for k in ("asof", "ok", "problems", "vol_scale", "coin_weights_no_btc", "legs")})


# ---------- 2026-11-01 SNDK单票方案 单独复盘页(1h K线)：engine/sndk_solo_view.py，和实盘执行器同一套重放，10 分钟内复用缓存 ----------
@app.get("/sndk")
def sndk_page():
    return send_from_directory(app.static_folder, "sndk.html")


@app.get("/api/sndk_solo")
@api
def sndk_solo_get():
    from engine import sndk_solo_view
    return _json_file(sndk_solo_view.ensure())


@app.get("/api/retired")
@api
def retired_get():
    return jsonify(retired=RETIRED, not_optimal=NOT_OPTIMAL, generated=RETIRED_AT)


if __name__ == "__main__":
    app.run("127.0.0.1", int(os.environ.get("PORT", 8890)), debug=False)
