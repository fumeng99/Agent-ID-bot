# 安装与部署

本文说明如何在一台新机器上部署 Agent ID Bot，并接入任意 AI Agent 的定时任务。

## 1. 前置条件

| 项目 | 要求 |
|---|---|
| 操作系统 | macOS、Linux 或 Windows WSL |
| 运行时 | Node.js ≥ 18、Python ≥ 3.11、git |
| 钱包 | 币安 App 中已开通 Agentic Wallet 并持有 `.ai` Agent ID；已开启**开发者模式**（合约调用必需，过期需在 App 内续期） |
| 网络 | 可访问 BSC 公共 RPC、npm 与 GitHub |

## 2. 获取代码与目录约定

```sh
git clone https://github.com/fumeng99/Agent-ID-bot.git
cd Agent-ID-bot
export REPO=$(pwd)
export AIDBOT_HOME=~/.agent-id-bot
export AIDBOT_DATA=$AIDBOT_HOME/data
mkdir -p $AIDBOT_HOME/baw $AIDBOT_DATA
cp config/config.example.json config/config.json
```

建议将上述 `export` 写入 shell 配置文件，确保定时任务中同样生效。

## 3. 安装钱包 CLI

```sh
cd $AIDBOT_HOME/baw
npm install @binance/agentic-wallet
cp $REPO/baw/baw-wrapper.sh node_modules/.bin/baw
chmod +x node_modules/.bin/baw
```

包装器会从 `$HOME/instance_id` 读取固定设备编号并注入 `BINANCE_INSTANCE_ID`，避免运行环境变化导致会话失效。

## 4. 登录账号

每个账号使用独立目录与固定设备编号：

```sh
mkdir -p $AIDBOT_HOME/baw/accounts/main
uuidgen > $AIDBOT_HOME/baw/accounts/main/instance_id

$REPO/baw/safe_session.sh signin main   # 生成配对码与二维码
# 在币安 App 中切换到对应账号后扫码
$REPO/baw/safe_session.sh verify main   # 核对 Agent ID
```

注意事项：

- 不要直接运行 `baw auth signin` / `baw auth signout`，统一使用 `safe_session.sh`，以免挤掉正在使用的会话。
- 不要删除或修改 `instance_id`。
- 同一钱包同一时间只能在一处登录。
- 多账号重复以上步骤，并在 `config/config.json` 的 `accounts` 中登记 `dir`、`label`、`agentId`、`address`。

## 5. 安装依赖与技能库

```sh
# 币安官方技能（baw 用法、代币审计等）
git clone https://github.com/binance/binance-skills-hub $AIDBOT_HOME/binance-skills-hub

# PoW 型 Mint 依赖
cd $REPO/agenthash && npm install

# 可选：Clawby 数据接口
export CLAWBY_API_KEY=<你的密钥>
bash $REPO/monitor/update_skills.sh
```

## 6. 自检

```sh
bash $REPO/monitor/doctor.sh
```

自检覆盖基础依赖（node、python3、git、curl）、钱包 CLI 安装、各账号 `instance_id` 与会话状态。全部通过后再接入定时任务。

## 7. 接入定时任务

推荐节奏：巡逻每 10 分钟一次，维护每日一次。

| 平台 | 巡逻 | 维护 |
|---|---|---|
| Claude Code | `/loop 10m` + `docs/prompts/PATROL_PROMPT.md`，或 cron 调用 `claude -p` | cron 每日调用 `claude -p` + `DAILY_UPDATE_PROMPT.md` |
| OpenAI Codex CLI | cron 调用 `codex exec "<PATROL_PROMPT>"` | cron 调用 `codex exec "<DAILY_UPDATE_PROMPT>"` |
| OpenClaw 等常驻 Agent | 安装 `skills/agent-id-bot/`，使用平台的定时 / 心跳功能 | 同左 |
| 无 AI | cron 运行发现脚本，人工查看输出（不会自动执行任何交易） | cron 运行 `monitor/update_skills.sh` |

cron 示例（仅发现层）：

```cron
*/10 * * * * cd ~/Agent-ID-bot/monitor/chainscan && SCAN_BUDGET=60 timeout 200 python3 scan.py >> $AIDBOT_DATA/scan.log 2>&1
0 9 * * *    bash ~/Agent-ID-bot/monitor/update_skills.sh >> $AIDBOT_DATA/update.log 2>&1
```

## 8. 部署前检查清单

- [ ] `config/config.json` 中的账号与地址正确，`spending.allowAutoPaid` 保持 `false`（除非已获书面授权）
- [ ] 提示词与 `docs/RULES.md` 中的授权条款已替换为你自己的
- [ ] `doctor.sh` 全部通过
- [ ] 各账号 BNB 余额足以支付 gas（或已配置 Gas 自动补充）
- [ ] 未将任何密钥、会话文件或 `instance_id` 提交到仓库

## 常见问题

| 现象 | 处理 |
|---|---|
| RPC 返回 429 | 脚本已内置多节点轮换；仍频繁出现时通过 `AIDBOT_RPC` 配置自有节点 |
| 会话显示 `UNKNOWN` | 网络抖动导致，按可用处理；`safe_session.sh check` 连续 3 次失败才判定掉线 |
| 合约调用报开发者模式错误 | 在币安 App 中续期开发者模式 |
| Clawby 返回 `RATE_LIMIT_BANNED` | 免费档频率超限，等待解封；`monitor/clawby.sh` 已串行限速 |
