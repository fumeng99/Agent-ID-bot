#!/usr/bin/env bash
# 签名验证的公开 Agent ID 项目目录（官网/官推/合约/费用/初筛）。只读，不碰钱包。
# 需设置 AIDBOT_FEED_REPO=<目录客户端 git 地址>；未设置则跳过。首次自动 clone 并建 venv（锁哈希安装）。
# 用法: catalog_feed.sh                 -> 新项目 JSON（overview/outbox，展示后 --ack 确认）
#       catalog_feed.sh --view --format chat --include-unscreened --include-low
#       catalog_feed.sh --ack ID1,ID2
# 转述项目卡时保留卡片自带的来源行。
set -eu
[ -n "${AIDBOT_FEED_REPO:-}" ] || { echo '{"status":"skipped","error":"AIDBOT_FEED_REPO not set"}'; exit 0; }
W=${AIDBOT_HOME:-$HOME/.agent-id-bot}; D=$W/catalog-feed; ST=${AIDBOT_DATA:-$HOME/.agent-id-bot/data}/feed/state.json
[ -d "$D" ] || git clone -q --depth 1 "$AIDBOT_FEED_REPO" "$D"
[ -x "$D/.venv/bin/python" ] || { python3 -m venv "$D/.venv" && "$D/.venv/bin/pip" -q install --require-hashes -r "$D/requirements.txt"; }
mkdir -p "$(dirname "$ST")"; cd "$D"
if [ $# -eq 0 ]; then exec .venv/bin/python scripts/feed_client.py --state "$ST" --subscribe --include-unscreened --include-low; fi
exec .venv/bin/python scripts/feed_client.py --state "$ST" "$@"
