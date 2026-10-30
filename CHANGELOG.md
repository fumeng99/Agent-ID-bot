# 更新日志
## 2026-10-30
- 改成通用版：去掉平台专属路径，`AIDBOT_HOME` / `AIDBOT_DATA` 环境变量 + `config/config.json`；新增 `AGENTS.md`、`CLAUDE.md`、`skills/agent-id-bot/SKILL.md`，任何 Agent 都能读。
- 新增签名项目目录订阅（`monitor/feed/catalog_feed.sh`）。
- 新增每步留痕 `log.py`、环境自检 `doctor.sh`、从 config 读账号 `accounts.py`；所有状态可通过 `AIDBOT_DATA` 统一存放。
- AGENTS.md 加决策三档、付费项目权限/貔貅/资金去向检查、执行前复查报价。
- 新增项目官网指南监控、mint 机制核验清单、Four.meme/Gang 内盘、TG 频道、技能自动更新。
