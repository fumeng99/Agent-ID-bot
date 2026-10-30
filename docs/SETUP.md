# 安装

需要 Node 18+、Python 3.11+、git；macOS / Linux / WSL。

```sh
git clone https://github.com/fumeng99/Agent-ID-bot.git && cd Agent-ID-bot
export AIDBOT_HOME=~/.agent-id-bot AIDBOT_DATA=~/.agent-id-bot/data
mkdir -p $AIDBOT_HOME/baw $AIDBOT_DATA && cp config/config.example.json config/config.json   # 改成自己的账号

# 1. 币安 Agentic Wallet CLI（官方 npm 包）
cd $AIDBOT_HOME/baw && npm install @binance/agentic-wallet
cp <仓库>/baw/baw-wrapper.sh node_modules/.bin/baw && chmod +x node_modules/.bin/baw

# 2. 每个账号一个目录 + 固定设备编号
mkdir -p accounts/main && uuidgen > accounts/main/instance_id
<仓库>/baw/safe_session.sh signin main     # 出配对码/二维码，在币安 App 里切到对应账号扫码
<仓库>/baw/safe_session.sh verify main

# 3. 币安官方技能（baw 用法、代币审计等）
git clone https://github.com/binance/binance-skills-hub $AIDBOT_HOME/binance-skills-hub

# 4. 依赖
cd <仓库>/agenthash && npm install
```

App 里要打开开发者模式（合约调用需要），过期要续。

## 让 Agent 定时巡逻
- **Claude Code**：在仓库里开会话，`/loop 10m 按 AGENTS.md 跑一轮巡逻`，或 cron 调 `claude -p "按 AGENTS.md 跑一轮巡逻"`。
- **Codex CLI**：cron 调 `codex exec "按 AGENTS.md 跑一轮巡逻"`。
- **OpenClaw / 其他常驻 Agent**：把 `skills/agent-id-bot/` 装进技能目录，用它的定时/心跳功能每 10 分钟触发。
- **无 AI**：cron 只跑发现脚本（scan.py、ezbot_feed.sh、tgcall.py、platforms.py），自己看输出；不会自动铸造。

每天跑一次 `monitor/update_skills.sh` 保持 baw 和技能库最新（失败自动回滚）。
