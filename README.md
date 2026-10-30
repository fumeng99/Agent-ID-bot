# Agent ID Bot

币安 Agentic Wallet（`.ai` Agent ID）生态的**免费 Mint 巡逻 + 项目投研**工具包。
不绑定任何 AI 平台：Claude Code、OpenAI Codex、OpenClaw、Cursor、Gemini CLI、Hermes 等会读 `AGENTS.md` / `SKILL.md` 的 Agent 都能直接用；没有 AI 也能用 cron 跑发现脚本。

> ⚠️ 链上有风险，不构成投资建议。默认只自动打**严格 0 元**且预演干净的 Mint；付费、买入、授权、签名一律要人确认。

## 能做什么
| 模块 | 文件 | 说明 |
|---|---|---|
| 链上发现 | `monitor/chainscan/scan.py` | 扫 BSC 新合约 / 铸造事件 / 热度，找 Agent ID 专属 mint（字节码含注册表 `0x60B9…022F`、代理追实现、铸造事件 ≥6 个地址等），带风险标记 |
| 调用重放 | `monitor/chainscan/replay.py` | 学别人成功的 mint calldata，替换成自己地址，标记「禁止照抄 / 含他人地址 / 可能含签名」 |
| EZbot 目录 | `monitor/feed/ezbot_feed.sh` | 订阅 @0xquqi 的签名公开目录（约 270 个项目，官网/官推/合约/费用/初筛） |
| TG 频道 | `monitor/tg/tgcall.py` | 抓公开频道 `t.me/s/<频道>` 新帖（默认 agentidcall） |
| 内盘平台 | `monitor/platforms/platforms.py` | Four.meme OpenFour Agent ID 专区（币安 pulse 榜）+ Gang.fun 新币 |
| Flap | `monitor/flap/flap.mjs` | Flap Agent Curve 监控 |
| 官网指南 | `monitor/probe.py` / `monitor/watch_sites.py` | 读项目 `/skill.md` `/llms.txt` `/agent.md` `/api/state`；盯 20+ 已知项目站开新系列 |
| PoW 免费 Mint | `agenthash/powmint.mjs` + `miner.py` | AgentHash 式挖矿 mint（challengeOf / verify / mine） |
| 会话 | `baw/safe_session.sh` `baw/baw-wrapper.sh` | 多账号会话检查、固定 instance_id 防假掉线、唯一允许 signin 的入口 |
| 自动更新 | `monitor/update_skills.sh` | 更新 binance-skills-hub、baw CLI（失败回滚）、可选 Clawby |
| 核验清单 | `monitor/mint-patterns.json` | 14 类 mint 机制要核验的问题（源自 EZbot） |

## 快速开始
1. 看 [`docs/SETUP.md`](docs/SETUP.md) 装 baw、建账号目录、复制 `config/config.example.json`。
2. 让你的 Agent 读 [`AGENTS.md`](AGENTS.md)（Claude Code 自动读 `CLAUDE.md`；支持 skill 的工具已自动发现 `.claude/skills/`、`.agents/skills/`，其他平台复制 `skills/agent-id-bot/`）。
3. 用你的平台的定时功能（cron、Claude Code `/loop`、Codex 自动化、OpenClaw heartbeat 等）每 10 分钟让 Agent 跑一轮「巡逻」。

## 环境变量
| 变量 | 默认 | 作用 |
|---|---|---|
| `AIDBOT_HOME` | `~/.agent-id-bot` | 放 `baw/`（CLI + accounts/<名>/）、技能库、EZbot 目录客户端 |
| `AIDBOT_DATA` | `~/.agent-id-bot/data` | 状态、seen.json、二维码 |
| `AIDBOT_MAIN_ACCOUNT` | `main` | 主账号目录名 |

学到了什么、来自哪些项目：[`docs/LEARNINGS.md`](docs/LEARNINGS.md)。实战笔记：[`docs/field-notes.md`](docs/field-notes.md)。
