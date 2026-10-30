# 每日更新任务提示词（每天 09:00 CST）

> 给其他机器人用时：建一个每天 09:00 触发的定时任务，把下面整段作为提示词；路径按你的环境替换，巡逻任务 id 换成你自己的。

用户授权「全自动优化更新」：Clawby 技能、Binance 技能库（binance-skills-hub）和币安 Agentic Wallet CLI（baw）有新版本就自动更新；巡逻工具自动优化并同步到用户私有 GitHub 仓库，不必再问。

1. 更新：运行 `bash $AIDBOT_DATA/update_skills.sh`（background 运行并等待）。脚本会安装/更新 Clawby managed 文件（先备份）、git pull binance-skills-hub、npm 升级 @binance/agentic-wallet 并重装 baw-wrapper.sh、自检失败自动回滚。不要删除 accounts/*/instance_id，禁止运行 baw auth signin/signout，不要调用 平台的「报错暂停」功能。Clawby 或 binance-agentic-wallet 的 SKILL.md 有变化时，读新版本，把影响巡逻做法的要点补进 RULES.md 末尾「技能更新记录」，必要时修正巡逻脚本里过时的命令。

2. 优化：读免费Mint巡逻任务过去 24 小时的运行记录，找出反复出现的脚本报错、超时、接口失效、误报/漏报。只修发现与投研类脚本（chainscan/scan.py、replay.py、tg/tgcall.py、platforms/platforms.py、flap/flap.mjs、probe.py、watch_sites.py）：修完用一次真实运行验证。新的可靠发现来源可以加进 platforms.py。绝不放宽 RULES.md 的安全边界：自动执行只限 value=0 且 preview 干净的免费 Mint；付费、买入、approve、permit、链下签名永远需用户确认；不要改授权规则、不要碰钱包会话文件。

3. 同步：运行 `bash $AIDBOT_DATA/sync_github.sh "<一句话说明本次改了什么>"`（需 GitHub 凭据）。脚本会替换钱包地址和 .ai 名，不上传状态、二维码、会话、instance_id；无变化不提交。推送前用 git diff 看一眼，发现新的自己钱包地址或任何密钥字样就先补进脚本的替换列表再推送。GitHub 报 401/404 时只说明需要重新授权，不要反复重试。

4. 汇报：什么都没变时不发消息。有更新、优化或回滚时发一条简短中文消息：更新了什么（版本号）、修了什么、对巡逻的影响、GitHub 提交。
