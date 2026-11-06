#!/bin/bash
# 2026-10-19 擂台/网站守护 + 自动对账修复 + 复刻健康检查(root crontab 每小时 :35)。详见 engine/reconciler.py、engine/health.py
D=/home/stratroster/unicorn_quant/optimal_data
PY=/home/stratroster/eth-webhook-server/venv/bin/python
for s in strategy-roster roster-dashboard unicorn-quant nginx; do
  if ! systemctl is-active --quiet "$s"; then
    systemctl restart "$s"
    echo "{\"ts\": $(date +%s), \"service\": \"$s\"}" >> "$D/guard_events.jsonl"
    chown stratroster:stratroster "$D/guard_events.jsonl"
    echo "【方案F纸面·⚠️】服务 $s 停了，已自动重启" | python3 -c 'import json,sys;p="'"$D"'/alerts_pending.json";a=[]
try: a=json.load(open(p))
except Exception: pass
a.append(sys.stdin.read().strip());json.dump(a,open(p,"w"),ensure_ascii=False)'
    chown stratroster:stratroster "$D/alerts_pending.json"
  fi
done
# 2026-10-19 方案F实盘：发布器/执行器 是 enabled(没被人为停用)却没在跑 → 拉起来并告警
for s in planf-publisher planf-live-E; do
  if systemctl is-enabled --quiet "$s" && ! systemctl is-active --quiet "$s"; then
    systemctl restart "$s"
    echo "{\"ts\": $(date +%s), \"service\": \"$s\"}" >> "$D/guard_events.jsonl"
    echo "【方案F实盘·⚠️】$s 停了，已自动重启" | python3 -c 'import json,sys;p="'"$D"'/alerts_pending.json";a=[]
try: a=json.load(open(p))
except Exception: pass
a.append(sys.stdin.read().strip());json.dump(a,open(p,"w"),ensure_ascii=False)'
    chown stratroster:stratroster "$D/alerts_pending.json"
  fi
done
cd /home/stratroster/unicorn_quant && sudo -u stratroster nice -n 10 $PY -m engine.reconciler >> "$D/reconciler.log" 2>&1
# 2026-10-19 复刻健康检查：数据新鲜度、擂台参数=回测参数、现货K线、美股永续名单变化、前视检验、自然一致率
cd /home/stratroster/unicorn_quant && sudo -u stratroster nice -n 10 $PY -m engine.health >> "$D/health.log" 2>&1
# 2026-10-30 方案F实盘跟踪：先以账户身份拉已实现盈亏/手续费/资金费(只读)，再算 回测↔擂台↔实盘 三层偏差(engine/planf_track.py)
( cd /home/binanceE/planf_live && sudo -u binanceE PLANF_ACCOUNT=E nice -n 10 /home/binanceE/binance-engine/venv/bin/python planf_income.py >> "$D/planf_income.log" 2>&1 )
cd /home/stratroster/unicorn_quant && sudo -u stratroster nice -n 10 $PY -m engine.planf_track >> "$D/planf_track.log" 2>&1
# 有待发告警就走看门狗现成的钉钉机器人(加签密钥只在 /root/watchdog/.env，不复制)
if [ -s "$D/alerts_pending.json" ] && [ "$(cat $D/alerts_pending.json)" != "[]" ]; then
  ( set -a; . /root/watchdog/.env; set +a
    /home/binanceB/binance-engine/venv/bin/python - "$D/alerts_pending.json" <<'EOF'
import json, sys
sys.path.insert(0, "/root/watchdog")
from dingtalk_notify import send_text
p = sys.argv[1]
try:
    items = json.load(open(p, encoding="utf-8"))
except Exception:
    items = []
if items:
    text = "方案F纸面模拟 · 复刻健康检查\n" + "\n".join(items[:20]) + "\n详情：http://187.77.130.144:8890/best"
    if send_text(text):
        json.dump([], open(p, "w"))
EOF
  ) >> "$D/health.log" 2>&1
fi
