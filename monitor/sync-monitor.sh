#!/usr/bin/env bash
# 把仓库 monitor/ 铺到 $AIDBOT_DATA：discover_all.sh 与提示词都按 $AIDBOT_DATA/<脚本名> 找脚本。
# git pull 之后重跑本脚本即可同步。
set -euo pipefail
AIDBOT_HOME=${AIDBOT_HOME:-$HOME/.agent-id-bot}
AIDBOT_DATA=${AIDBOT_DATA:-$AIDBOT_HOME/data}
REPO=${REPO:-$(cd "$(dirname "$0")/.." && pwd)}
mkdir -p "$AIDBOT_DATA"
cp -a "$REPO"/monitor/. "$AIDBOT_DATA"/
[ -d "$REPO/agenthash" ] && cp -a "$REPO/agenthash" "$AIDBOT_DATA"/
echo "synced $REPO/monitor -> $AIDBOT_DATA"