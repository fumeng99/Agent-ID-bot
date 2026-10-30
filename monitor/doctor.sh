#!/usr/bin/env bash
# 环境自检：依赖、baw、各账号会话、BNB 余额提醒。只读。
H=${AIDBOT_HOME:-$HOME/.agent-id-bot}; HERE=$(cd "$(dirname "$0")" && pwd); ok=1
for c in node python3 git curl; do command -v $c >/dev/null || { echo "缺少 $c"; ok=0; }; done
BAW=$H/baw/node_modules/.bin/baw; [ -x "$BAW" ] || { echo "未安装 baw：$BAW"; ok=0; }
dirs=$(python3 "$HERE/accounts.py" 2>/dev/null | awk '{print $1}'); [ -z "$dirs" ] && dirs=$(ls "$H/baw/accounts" 2>/dev/null)
for d in $dirs; do
  [ -f "$H/baw/accounts/$d/instance_id" ] || echo "[$d] 缺 instance_id（会假掉线）"
  echo "[$d] $("$HERE/../baw/safe_session.sh" check "$d" 2>/dev/null || "$H/baw/safe_session.sh" check "$d")"
done
[ $ok = 1 ] && echo "自检完成"
