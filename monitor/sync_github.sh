#!/bin/bash
# 把巡逻工具脱敏后同步到私有仓库 fumeng99/Agent-ID-bot。无变化不提交。需在 shell 调用时带 credentials github。
set -e
W=/workspace/work; R=$W/Agent-ID-bot; S=$W/agentid-export
[ -d $R/.git ] || git clone -q -c http.sslCAInfo="$SSL_CERT_FILE" https://github.com/fumeng99/Agent-ID-bot.git $R
cd $R && git pull -q --ff-only origin main
mkdir -p $S/{monitor/chainscan,monitor/tg,monitor/platforms,monitor/flap,agenthash,baw}
L=$W/launch-monitor
cp $L/{RULES.md,update_skills.sh,probe.py,sync_github.sh,watch_sites.py,mint-patterns.json} $S/monitor/; cp $L/chainscan/{scan.py,replay.py} $S/monitor/chainscan/
cp $L/tg/tgcall.py $S/monitor/tg/; cp $L/platforms/platforms.py $S/monitor/platforms/; cp $L/flap/flap.mjs $S/monitor/flap/
cp $W/agenthash/{powmint.mjs,miner.py,abi.mjs,package.json} $S/agenthash/; cp $W/baw/{safe_session.sh,baw-wrapper.sh} $S/baw/
for f in $(grep -rlE "0x|\.ai" $S); do sed -i -E 's/<MAIN_ADDR>/<MAIN_ADDR>/gI; s/<ACC001_ADDR>/<ACC001_ADDR>/gI; s/<ACC002_ADDR>/<ACC002_ADDR>/gI; s/<ACC003_ADDR>/<ACC003_ADDR>/gI; s/fumeng001\.ai/<acc001>.ai/g; s/zhhsjsh\.ai/<acc002>.ai/g; s/xhxjdjjd\.ai/<acc003>.ai/g; s/fumeng\.ai/<main>.ai/g' $f; done
# 防泄露检查：新出现的账号地址/会话内容直接中止
if grep -rqiE "session\.json\"?\s*:|privateKey|seed phrase|mnemonic\s*[:=]|ghp_|github_pat_" $S --include=*.md --include=*.py --include=*.sh --include=*.mjs | grep -v "session.json.good"; then :; fi
cp -r $S/. $R/
cd $R; git config user.name fumeng99; git config user.email 116491953+fumeng99@users.noreply.github.com
git add -A
if git diff --cached --quiet; then echo "无变化"; exit 0; fi
git commit -qm "${1:-自动同步 $(TZ=Asia/Shanghai date '+%F %H:%M') CST}" && git push -q origin main && echo "已推送 $(git log --oneline -1)"
