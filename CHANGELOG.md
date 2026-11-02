# 更新日志

## 2026-11-02
### 修复
- `minters.py`：Agent ID 铸造钱包数读失败被当成「不足 100」。根因是段长 10000 块超过节点 5000 块上限；改为 publicnode 4000 块/段（429 退避）+ 48.club 500 块/段兜底，逐段落盘缓存；读不到返回 `status: unknown`，不再返回 None。
- `autofill.py`：铸造钱包数 / 可升级检查 / 源码验证读失败时换节点重试 3 次，仍失败标 unknown（本轮不买、不当不达标），写 `discover_out/autofill_recheck.json`，`fastprogress` 常驻 45–60 秒后重跑 autofill（全部闸门不变）。
- `autoaudit.py`：每项检查区分「不合格 / 读失败」；读失败重试（≥60% 的盘 `--priority` 重试 5 次、独立 RPC 冷却表），只有读失败时不写 `audit_ok.json`，改写 `audit_pending.json`，60 秒内重审；国库抽成在 found.json 缺失时链上直读；非 Jager 模板用 `--report` 出通用审计到 `audit_report.json`（不进 autofill）。
- `fastprogress.py`：自动审计无结论（读失败或超时）60 秒后重审；≥60% 非 Jager 盘出通用审计；核心字段读空的盘同轮换节点再读一次。
- `verify_source.py`：Sourcify 无记录 + OKLink 读失败原来被当「未验证」，现返回 `verified: null`（不缓存，下轮重查）；代理槽读失败不再当「非代理」放过。
- `riskcheck.py`：限流/节点错误不再被当成 revert；貔貅模拟只有明确 revert 才判貔貅，读失败记 `read_errors`。
- `precheck.py` / `hotfree_unverified.py` / `opencheck.py` / `possell.py` / `fast_sell.py`：读失败重试并标 unknown（`unknown` 状态、`verify_pending`、`price_unknown`、报价读失败不计入放弃次数），不再默默丢弃。`opencheck.py` 价格改为 DexScreener/Gecko 批量查询。
### 新增
- `hotprogress.py`：免费盘铸造进度（已铸/总量/%、近 30 分钟铸造次数），常见函数 → 子合约 → 签名库函数名三级读取；`hotfree_unverified.py` 每行带进度，读不到写「进度读不到」。
- `backtest/collect.py`、`backtest/evaluate.py`：近 48 小时被跳过项目的只读回测。

## 2026-11-01
### 新增
- approvals_watch.py：授权巡检（只读 baw approvals list，新增授权只提醒不撤销），接入 discover_all
- minters.py/autofill.py：单个地址铸造占比 >30% 视为自灌进度，不自动打满
### 修复
- fairmint.py：安静盘不重复读（刷新数大幅减少）；超时看门狗输出安全的 timeout JSON，避免外层 timeout 杀掉后输出为空
- codeutil.py：代理合约实现地址按字节码逐条解析（旧正则会错位漏判）


本项目的重要变更记录于此。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，按日期组织。

## [2026-11-01]

### 变更
- `monitor/fairmint/fairmint.py`：猜测总量小于已售（超卖）时按卖完 100% 计，不再丢弃（参考社区同类工具的超卖修正；仅影响提醒）。

### 新增
- `monitor/precheck.py`：免费 mint 候选预检（只读 `eth_call`），五号逐一模拟并解码自定义错误（如 `SoldOut()`、`WrongPayment()`），按可铸 / 付费 / 售罄 / 其他排序；售罄写入本地黑名单不再重试。`discover_all.sh` 在发现步骤结束后自动运行。
- `monitor/quota.py`：按北京时间自然日输出当日 30U 与 100U 额度用量，作为推送「额度」一栏的唯一来源。
- `monitor/fairmint/fairmint.py`「已部署未开铸」预警：扫 PancakeV2 `PairCreated`，对在部署交易里建池的新代币，按 `CREATE(部署者, nonce)` 推算同一部署者前后部署的合约，合约码含 Agent ID 登记合约且含 `Minted` 事件 / `costOf` 的即加入观察（`premint_new`），开铸时输出 `mint_started`；其他新部署的 Agent ID 合约输出 `agentid_deployed`。部署者盯 48 小时，已知 fair mint 的部署者同样盯。
- `monitor/codeutil.py`：代理合约追到底（EIP-1967、Beacon、EIP-1167、合约码内写死的实现地址）、CREATE 地址推算、自动找总量函数、退款函数检测。
- `monitor/riskcheck.py` 新增 `upgradeable_proxy`、`has_refund` 参考字段，`--mint <合约>` 同时检查 fair mint 合约。

### 变更
- `fairmint.py` 改为批量 JSON-RPC：未开池项目每轮全部刷新（原为最近 15 个），开池进度 50–70% 或刚开铸的项目在本轮剩余时间内每 20 秒重读；读不到开池份数时自动猜总量函数，结果单独列为 `near_open_guess`，`autofill.py` 不据此自动打满。
- `opencheck.py` 在 DexScreener 无数据时改查 GeckoTerminal，V2 报价币增加 USD1；`chainscan/scan.py` 识别「直接转 BNB 铸造」并按付费处理。
- `price_trigger.py` 价格取流动性最大的池子，连续两次触发才卖出。
- `discover_all.sh` 加文件锁，同一时间只运行一份。

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
