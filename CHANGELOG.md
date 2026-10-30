# 更新日志
## 2026-10-30
- 新增 `monitor/bawlib.py`（共用预演/执行/干净判定）、`monitor/phase_mint.py`（定时开放抢铸，通用版）、`monitor/backfill.py`（新号补领，含签名/证明的自动排除）。
- 新增 `monitor/gas_topup.py`：BNB 不足时自动用 USDT 换 BNB（阈值、金额、每日次数可配）。
- 补上 `monitor/riskcheck.py`（权限 + 貔貅模拟），上次提交说明里写了但漏推。
- AGENTS.md 加自动补 gas、解题/签名型免费 Mint 自动尝试与通知规则、风险检查用法。
- 改成通用版：去掉平台专属路径，`AIDBOT_HOME` / `AIDBOT_DATA` 环境变量 + `config/config.json`；新增 `AGENTS.md`、`CLAUDE.md`、`skills/agent-id-bot/SKILL.md`，任何 Agent 都能读。
- 新增签名项目目录订阅（`monitor/feed/catalog_feed.sh`）。
- 新增每步留痕 `log.py`、环境自检 `doctor.sh`、从 config 读账号 `accounts.py`；所有状态可通过 `AIDBOT_DATA` 统一存放。
- AGENTS.md 加决策三档、付费项目权限/貔貅/资金去向检查、执行前复查报价。
- 新增项目官网指南监控、mint 机制核验清单、Four.meme/Gang 内盘、TG 频道、技能自动更新。
