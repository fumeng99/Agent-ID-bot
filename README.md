# Agent ID bot（Hark 巡逻导出）

币安 Agentic Wallet（BAW）多账号 Agent ID 免费 Mint 巡逻与投研工具。由 Hark 维护，已去掉地址、Agent ID、会话与设备编号。

## 目录
- `monitor/RULES.md`：巡逻规则（自动/需确认边界、防钓鱼过滤、各项目笔记）
- `monitor/chainscan/scan.py`：BSC 链上新合约发现（免费 mint 候选，风险标记）；`replay.py` 替换地址重放 calldata 并标记禁止照抄的参数
- `monitor/tg/tgcall.py`：Telegram 频道 t.me/s/agentidcall 新帖抓取
- `monitor/platforms/platforms.py`：Four.meme OpenFour Agent ID 专区 + Gang.fun 新币监控（借鉴 EZbot）
- `monitor/flap/flap.mjs`：Flap Agent Curve 监控
- `monitor/probe.py`：读项目官网 skill.md / llms.txt / agent.md / api/state
- `monitor/update_skills.sh`：Clawby、binance-skills-hub、baw CLI 自动更新（失败回滚）
- `agenthash/`：PoW 挖矿型免费 Mint（Agent Hash Apes 类）
- `baw/`：baw 包装器（固定 instance_id）、会话检查脚本

## 安全边界
- 自动执行仅限 value=0、preview 模拟通过、无授权/风险变化的免费 Mint
- 付费 mint、买入、swap、approve、permit、链下签名一律需人工确认
- 状态文件、二维码、会话、instance_id 不入库（见 .gitignore）
