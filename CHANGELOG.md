# 更新日志

本项目的重要变更记录于此。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，按日期组织。

## [2026-11-01]

### 新增
- `monitor/precheck.py`：免费 mint 候选预检（只读 `eth_call`），五号逐一模拟并解码自定义错误（如 `SoldOut()`、`WrongPayment()`），按可铸 / 付费 / 售罄 / 其他排序；售罄写入本地黑名单不再重试。`discover_all.sh` 在发现步骤结束后自动运行。
- `monitor/quota.py`：按北京时间自然日输出当日 30U 与 100U 额度用量，作为推送「额度」一栏的唯一来源。

### 修复
- 跨日后推送仍显示前一日额度用量的问题。
- 免费候选因已售罄被反复预演、导致可铸项目当轮未尝试的问题。

## [2026-10-31]

### 新增
- `monitor/rpcpool.py`：11 个 BSC 公共节点的轮换池，遇 429 / 5xx / 超时自动切换并冷却 60 秒，支持批量请求；可通过 `AIDBOT_RPC` 指定自有节点。
- `monitor/fairmint/fairmint.py`：基于 `Minted` 事件全链发现 Jager / AGI 类付费分档 fair mint，输出单价、进度与开池状态。
- `monitor/clawby.sh`：Clawby 数据接口封装，从 `CLAWBY_API_KEY` 读取密钥，串行限速以避免 IP 封禁。
- `SECURITY.md`：安全策略与敏感信息处理说明。
- 巡逻与每日维护提示词（`docs/prompts/`）及完整运行规则（`docs/RULES.md`）。

### 变更
- `gas_topup.py`、`bawlib.py`、`backfill.py`、`flap.mjs`、`powmint.mjs` 改用多节点 RPC，余额查询合并为单次批量请求。
- `watch_sites.py`：额外抓取首页、脚本及一层 import（如 `config.mjs`）中的合约地址，并读取 `/deployment.json`。
- 巡逻规则：回查范围扩大到「待查」项目；`seen.json` 按合约地址去重；汇报中交易链接紧随账号。
- `AGENTS.md`：补充面试 / EIP-712 签名型免费 Mint 流程。
- README、SETUP、DESIGN 文档重写。

### 修复
- `scan.py` 超时死循环：被外层 `timeout` 杀掉时进度不保存，下一轮重扫同一 35 分钟窗口再次超时。现增加总时限 `SCAN_DEADLINE`（默认 150 秒），到点前停手并保存已扫到的区块（未扫部分下轮接续，不跳块）；`eth_getLogs` 4 线程并发，报错段顺序补扫一次；部署者查询最多读 15 张收据；聪明钱名单时间不足时沿用旧名单。
- `scan.py` 将预建空池误判为已开池的问题（现要求储备 > 0）。
- 合约地址藏在前端配置文件中导致漏报的问题。

## [2026-10-30]

### 新增
- 通用化改造：以 `AIDBOT_HOME` / `AIDBOT_DATA` 与 `config/config.json` 取代平台专属路径；新增 `AGENTS.md`、`CLAUDE.md` 与 `skills/agent-id-bot/SKILL.md`。
- `monitor/bawlib.py`：共用的预演、执行与干净判定。
- `monitor/phase_mint.py`：定时开放抢铸。
- `monitor/backfill.py`：新账号补领，自动排除含签名或证明的调用。
- `monitor/gas_topup.py`：BNB 不足时自动以 USDT 兑换，阈值、金额与每日次数可配置。
- `monitor/riskcheck.py`：合约权限检查与貔貅模拟。
- `monitor/feed/catalog_feed.sh`：签名校验的项目目录订阅。
- `monitor/log.py`、`monitor/doctor.sh`、`monitor/accounts.py`：留痕、环境自检与账号读取。
- 官网指南监控、mint 机制核验清单、Four.meme / Gang.fun 内盘、TG 频道监控与技能自动更新。

### 变更
- `AGENTS.md`：新增决策三档、付费项目权限 / 貔貅 / 资金去向检查、执行前复查报价、自动补 Gas 及解题 / 签名型 Mint 的自动尝试与通知规则。
