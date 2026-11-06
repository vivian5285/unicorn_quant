"""2026-10-31 方案F实盘 5 分钟看门狗(root，systemd planf-watchdog.timer)。
recon_guard.sh 每小时才跑一次、只看服务 active；这里补"在跑但卡死"的情况，并给每小时守护本身做死人开关：
· 发布器/执行器 enabled 却没在跑 → 重启；在跑但状态文件超过 10 分钟没更新(正常每 30 秒一次) → 判定卡死、重启
· 1 小时内同一服务重启 ≥3 次 → 不再重启(避免反复重启掩盖真问题)，钉钉报警等人工
· 每小时守护(health.log)超过 2 小时没写 → 报警
不碰 ARMED/STOP(下单开关只归宝贝)；被人为 disable 的服务不拉起。告警走看门狗现成的钉钉机器人。"""
import json, os, subprocess, sys, time

D = "/home/stratroster/unicorn_quant/optimal_data"
ST = os.path.join(D, "planf_watchdog_state.json")
SVC = {"planf-publisher": "/var/lib/planf/targets.json", "planf-live-E": "/var/lib/planf/live_E_status.json"}
STALE_SEC, MAX_RESTARTS, GUARD_STALE_SEC, REMIND_SEC = 600, 3, 7200, 3 * 3600


def rd(p, d):
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return d


def sh(*a):
    return subprocess.run(list(a), capture_output=True, text=True).returncode == 0


def ding(text):
    sys.path.insert(0, "/root/watchdog")
    for line in open("/root/watchdog/.env", encoding="utf-8"):
        k, _, v = line.strip().partition("=")
        if k and not k.startswith("#") and v:
            os.environ.setdefault(k, v.strip().strip('"').strip("'"))
    from dingtalk_notify import send_text
    return send_text("方案F实盘 · 5分钟看门狗\n" + text)


def main():
    now = time.time(); st = rd(ST, {}); msgs = []
    for svc, path in SVC.items():
        if not sh("systemctl", "is-enabled", "--quiet", svc):
            continue                                                   # 人为停用的不管
        s = st.setdefault(svc, {"restarts": []})
        s["restarts"] = [t for t in s["restarts"] if now - t < 3600]
        active = sh("systemctl", "is-active", "--quiet", svc)
        age = now - float((rd(path, {}) or {}).get("ts") or 0)
        why = "没在跑" if not active else (f"卡死(状态 {age / 60:.0f} 分钟没更新)" if age > STALE_SEC else None)
        if not why:
            if s.get("alarm"):
                msgs.append(f"✅ {svc} 已恢复正常"); s["alarm"] = False
            continue
        if len(s["restarts"]) >= MAX_RESTARTS:
            if now - float(s.get("last_alarm") or 0) > REMIND_SEC:
                msgs.append(f"⛔ {svc} {why}，1小时内已自动重启 {len(s['restarts'])} 次仍不好，停止自动重启，需要人工检查")
                s["last_alarm"] = now; s["alarm"] = True
            continue
        sh("systemctl", "restart", svc); s["restarts"].append(now); s["alarm"] = True
        msgs.append(f"⚠️ {svc} {why}，已自动重启(1小时内第 {len(s['restarts'])} 次)")
    g = st.setdefault("guard", {})
    try:
        gage = now - os.path.getmtime(os.path.join(D, "health.log"))
    except OSError:
        gage = 1e9
    if gage > GUARD_STALE_SEC:
        if now - float(g.get("last_alarm") or 0) > REMIND_SEC:
            msgs.append(f"⚠️ 每小时守护(recon_guard.sh)已 {gage / 3600:.1f} 小时没跑，对账/体检/跟踪都停了，检查 root crontab")
            g["last_alarm"] = now; g["alarm"] = True
    elif g.get("alarm"):
        msgs.append("✅ 每小时守护已恢复"); g["alarm"] = False
    if msgs:
        print(time.strftime("%F %T"), " | ".join(msgs), flush=True)
        try:
            ding("\n".join(msgs))
        except Exception as e:
            print("钉钉发送失败", type(e).__name__, e, flush=True)
    json.dump(st, open(ST, "w", encoding="utf-8"), ensure_ascii=False)


if __name__ == "__main__":
    main()
