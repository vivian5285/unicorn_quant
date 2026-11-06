# 独角兽量化 Unicorn Quant

Python 后端（行情/指标/复盘/模拟盘/交易所适配）+ Lightweight Charts 前端，Uniswap 风格 UI。

- 线上：http://187.77.130.144:8890 （systemd: `unicorn-quant`，配置 `/etc/unicorn-quant.env`，端口 8890）
- 复盘复用仓库 `strategy_engine/strategies` 全部策略 + 内置双均线带(EMA7/25)等
- 默认模拟盘；实盘仅"验证单"(`/fapi/v1/order/test`)。真实下单需 `UQ_LIVE_ENABLED=1` + `UQ_BINANCE_KEY/SECRET` + 请求 confirm=true
- 本地：`UQ_PASSWORD=xxx python unicorn_quant/server.py`

> 接手前先读 [SYSTEM_OVERVIEW.md](SYSTEM_OVERVIEW.md)（系统说明：硬规则、架构、账户、服务、定时任务、开关护栏）和 [CLAUDE_CHANGES.md](CLAUDE_CHANGES.md)（改动记录与撤回方法）。
