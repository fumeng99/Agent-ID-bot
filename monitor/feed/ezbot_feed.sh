#!/usr/bin/env bash
# EZbot Agent ID 生态目录（@0xquqi，https://ezbot.vip/signals）：签名验证的公开项目目录，约 270 个项目，带官网/官推/合约/费用/初筛。
# 首次自动 clone https://github.com/0xquqi/ezbot-agent-id-feed-skill 并建 venv（锁哈希安装）。只读，不碰钱包。
# 用法: ezbot_feed.sh            -> 新增项目 JSON（outbox，展示后用 --ack 确认）
#       ezbot_feed.sh --view --format chat --include-unscreened --include-low   -> 看当前目录
#       ezbot_feed.sh --ack ID1,ID2
# 转发项目卡时必须保留来源三行（作者 @0xquqi、数据来源 https://ezbot.vip/signals、自主投研提示）。
set -eu
W=${AIDBOT_HOME:-$HOME/.agent-id-bot}; D=$W/ezbot-agent-id-feed-skill; ST=${AIDBOT_DATA:-$HOME/.agent-id-bot/data}/feed/state.json
[ -d "$D" ] || git clone -q --depth 1 https://github.com/0xquqi/ezbot-agent-id-feed-skill.git "$D"
[ -x "$D/.venv/bin/python" ] || { python3 -m venv "$D/.venv" && "$D/.venv/bin/pip" -q install --require-hashes -r "$D/requirements.txt"; }
mkdir -p "$(dirname "$ST")"; cd "$D"
if [ $# -eq 0 ]; then exec .venv/bin/python scripts/feed_client.py --state "$ST" --subscribe --include-unscreened --include-low; fi
exec .venv/bin/python scripts/feed_client.py --state "$ST" "$@"
