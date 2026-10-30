#!/usr/bin/env bash
# 每日自动更新：Clawby 技能、binance-skills-hub、@binance/agentic-wallet CLI（用户 2026-10-30 授权自动更新）
set -u; out=""
# 1. Clawby
S=/workspace/work/clawby/clawby-data; base=https://www.openclawby.com/clawby-skill
curl -s $base/manifest.json -o /tmp/cm.json && rv=$(python3 -c "import json;print(json.load(open('/tmp/cm.json'))['version'])") || rv=""
lv=$(python3 -c "import json;print(json.load(open('$S/manifest.json'))['version'])")
if [ -n "$rv" ] && [ "$rv" != "$lv" ]; then
  tar czf /tmp/clawby_backup_$lv.tgz -C /workspace/work/clawby clawby-data
  for f in $(python3 -c "import json;print(' '.join(json.load(open('/tmp/cm.json'))['managed']))"); do curl -s --create-dirs "$base/$f" -o "$S/$f"; done
  cp /tmp/cm.json $S/manifest.json; out="$out Clawby $lv→$rv;"
else touch $S/manifest.json; fi
# 2. binance-skills-hub
cd /workspace/work/binance-skills-hub && o=$(git rev-parse --short HEAD) && timeout 90 git pull -q --ff-only && n=$(git rev-parse --short HEAD) && [ "$o" != "$n" ] && out="$out skills-hub $o→$n;"
# 3. baw CLI
cd /workspace/work/baw; lb=$(node -p "require('./node_modules/@binance/agentic-wallet/package.json').version"); rb=$(npm view @binance/agentic-wallet version 2>/dev/null)
if [ -n "$rb" ] && [ "$rb" != "$lb" ]; then
  cp -a node_modules/@binance/agentic-wallet /tmp/baw_backup_$lb
  npm_config_cache=/tmp/npmcache npm install -q @binance/agentic-wallet@$rb --no-audit --no-fund >/dev/null 2>&1
  cp /workspace/work/baw/baw-wrapper.sh node_modules/.bin/baw; chmod +x node_modules/.bin/baw
  if HOME=/workspace/work/baw/accounts/fumeng timeout 60 node_modules/.bin/baw wallet status --json | grep -q '"success": *true'; then out="$out baw $lb→$rb;"
  else rm -rf node_modules/@binance/agentic-wallet && cp -a /tmp/baw_backup_$lb node_modules/@binance/agentic-wallet; cp baw-wrapper.sh node_modules/.bin/baw; out="$out baw $rb 新版自检失败已回滚到 $lb;"; fi
fi
echo "${out:-全部已是最新}"
