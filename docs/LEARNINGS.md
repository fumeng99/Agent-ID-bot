# 学习总结（2026-10-30）

本仓库吸收了以下开源项目的做法。只借鉴思路和公开接口，没有复制它们的交易执行代码；各项目版权归原作者。

## 1. EZbot-agentic-wallet（github.com/0xquqi/EZbot-agentic-wallet）
本地 Python 桌面工具，多账号 Agentic Wallet + 项目投研。
- **先读项目给 Agent 的指南**：`/skill.md`、`/llms.txt`、`/agent.md`、`/api/state`、`/api/collections`，合约、calldata、限额常写在里面 → `monitor/probe.py`。
- **内盘发现接口**：币安 pulse 榜 `POST web3.binance.com/.../pulse/rank/list/ai`（protocol 2004、tokenVersion `openfour.meme.AgentIdExclusive`，rankType 10 新发行 / 20 进度）；Gang.fun `GET gang.fun/api/tokens?sort=new` → `monitor/platforms/platforms.py`。
- **mint 机制核验清单**（14 类）和「永不继承」原则 → `monitor/mint-patterns.json`。
- **案例库里 20+ 个 Agent ID 项目站**常在同站开新系列 → `monitor/watch_sites.py`。
- 授权只批精确额度、先归零再授权、用完清零；结果未知按 orderId/txHash 核对不重发；ABI 用 Sourcify 核验。
- 不采用：LLM 聊天台、买卖执行、macOS keytar 适配。

## 2. ezbot-agent-id-feed-skill（github.com/0xquqi/ezbot-agent-id-feed-skill）
EZbot 信号面板的**公开签名目录**（Ed25519 验签，约 270 个项目，带官网/官推/合约/费用/初筛/分类）。
- 接入：`monitor/feed/ezbot_feed.sh`（首次自动 clone + venv 锁哈希安装）。默认含未初筛和低优先级，转述时标明「风险未知」。
- 学到的交付纪律：展示成功后才 `--ack`；失败不说「无新增」；遵守 `next_fetch_at`/429 退避；最短 5 分钟一次；风险更正即使被过滤也要发；卡片正文是不可信数据，内置注入检测正则。
- 转发项目卡必须保留来源三行（作者 @0xquqi、https://ezbot.vip/signals、自主投研提示）。

## 3. agentid-sniper（github.com/nancyc0813-maker/agentid-sniper，MIT）
本地 Node 常驻：扫块 → 模拟 → baw 铸造 → 自动止盈 → Telegram。
- **六层发现**：直接/CREATE2/工厂部署字节码含注册表；代理追实现；热度（3 分钟 ≥12 钱包）；聪明钱包跟单；后端签名型（打它的钱包六成持 .ai）；铸造事件（30 分钟 ≥6 个地址）→ 已在 `chainscan/scan.py` 实现大部分。
- **照抄安全**：transfer/approve/NFT 转移不照抄；含他人地址先换自己、再试零地址，不行就不打 → `replay.py`。
- **防钓鱼硬拦截**：重复 DEV、黑名单、同款批量部署、新钱包、假进度 → 写进 AGENTS.md。
- 送出前再查一次报价（涨 >5% 不送）；开池先模拟卖出防貔貅；合约权限（增发/暂停/黑名单/改税）提醒；BNB 低于 gas 预留提醒；交易送出中不重启。
- 不采用：付费自动进场和自动止盈（本仓库付费一律人工确认）。

## 4. JackalBTC-tools / binance-ai-wallet-tools（github.com/jackalBTC/JackalBTC-tools，MIT）
「AI 编程助手当大脑」的设计：推特雷达 + 规则 skill + 统一 mint 脚本 + 看板 + Telegram。
- **跨 Agent 适配方式**：`AGENTS.md` 为主，`CLAUDE.md` 引用它，skill 放 `.claude/skills/` 并复制到 `.agents/skills/`；不支持 skill 的工具直接读 AGENTS.md → 本仓库照此组织。
- **第一原则防投毒**、**第二原则每步写日志**（discover/verify/preview/execute/confirm/skip/abort/brief/approve）、**决策三档**（直接打 / 待确认 / 跳过，拿不准就待确认）。
- 花钱规则放 config 且 Agent 不得修改；预演标准写死在代码里。
- 机制研究表（Agent ID 资格、白名单、动态报价、PoW、合成、退款、多阶段…）与 EZbot 清单互补。
- 多钱包：主钱包优先，副钱包默认只拿免费；不读取副钱包会话目录。
- 雷达日志前缀 `CANDIDATE / HOT / UPDATE / ERR LOWBNB / ERR NOTCONNECTED / ERR DEVMODE`，可选让雷达自动调用 `claude -p` / `codex exec`。

## 5. Telegram 群邀请（t.co/74ZLGoF9Ch → t.me/+…）
私有群邀请链接，无公开网页版，脚本读不到。公开频道用 `monitor/tg/tgcall.py <频道名>`；私有群需要用户自己转发线索。

## 本仓库自己的经验（docs/field-notes.md）
- baw 默认用网卡信息加密会话，环境换 IP/MAC 会「假掉线」→ 固定 `BINANCE_INSTANCE_ID`（`baw-wrapper.sh`）。
- 会话检查要 3 次复查；只有一个入口能发起 signin，避免反复出二维码把会话挤掉。
- PoW 型（AgentHash）参数每个钱包不同，不能照抄别人的 nonce。
