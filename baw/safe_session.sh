#!/usr/bin/env bash
# 安全会话管理：唯一允许调用 `baw auth signin` 的入口。
# 用法: safe_session.sh check <dir>     -> 输出 CONNECTED / UNCONNECTED(确认3次) / PENDING(已有未过期配对码)
#       safe_session.sh signin <dir>    -> 仅在确认失效且无未过期配对码时：备份会话后生成新配对码+二维码
#       safe_session.sh verify <dir>    -> 用户扫码后完成登录并核对 agentId
#       safe_session.sh backup          -> 备份所有 CONNECTED 账号的 session.json
# <dir> = fumeng | fumeng001 | fumeng002 | fumeng003
set -u
BASE=/workspace/work/baw
BAW=$BASE/node_modules/.bin/baw
LM=/workspace/work/launch-monitor
LOCK=$BASE/.session.lock
cmd=${1:-}; acc=${2:-}
home() { echo "$BASE/accounts/$1"; }
status() { HOME=$(home "$1") timeout 40 "$BAW" wallet status --json 2>/dev/null | python3 -c "import json,sys
try:
  d=json.load(sys.stdin); st=d.get('data',{}).get('status')
  print(st if d.get('success') is True and st in ('CONNECTED','UNCONNECTED') else 'UNKNOWN')
except Exception: print('UNKNOWN')"; }
pending() { f=$LM/so_$1.json; [ -f "$f" ] && python3 -c "import json,time,sys
d=json.load(open('$f'))['data']; sys.exit(0 if int(d['expireAt'])/1000>time.time()+20 else 1)"; }

exec 9>"$LOCK"; flock -w 120 9 || { echo LOCKED; exit 1; }

case "$cmd" in
 check)
  # 只有 3 次都明确返回 success:true + status:UNCONNECTED 才算掉线；超时/报错/非JSON = UNKNOWN，什么都不做
  n=0; for i in 1 2 3; do s=$(status "$acc"); [ "$s" = CONNECTED ] && { echo CONNECTED; exit 0; }; [ "$s" = UNCONNECTED ] && n=$((n+1)); [ $i -lt 3 ] && sleep 60; done
  [ $n -lt 3 ] && { echo UNKNOWN; exit 0; }
  pending "$acc" && echo PENDING || echo UNCONNECTED ;;
 signin)
  s=$(status "$acc"); [ "$s" = CONNECTED ] && { echo CONNECTED; exit 0; }
  [ "$s" != UNCONNECTED ] && { echo UNKNOWN; exit 0; }
  pending "$acc" && { echo PENDING; cat "$LM/so_$acc.json"; exit 0; }
  h=$(home "$acc"); ts=$(date -u +%Y%m%dT%H%M%SZ)
  [ -f "$h/.baw/session.json" ] && cp -p "$h/.baw/session.json" "$h/.baw/session.json.bak-$ts"
  HOME=$h "$BAW" auth signin --json > "$LM/so_$acc.json" 2>&1
  url=$(python3 -c "import json;print(json.load(open('$LM/so_$acc.json'))['data']['urlForWeb'])")
  "$BASE/node_modules/.bin/qrcode" -o "$LM/qr_$acc.png" "$url" >/dev/null 2>&1
  cat "$LM/so_$acc.json"; echo "QR: $LM/qr_$acc.png" ;;
 verify)
  h=$(home "$acc"); q=$(python3 -c "import json;print(json.load(open('$LM/so_$acc.json'))['data']['qrCodeId'])")
  HOME=$h timeout 100 "$BAW" auth verify --qrCodeId "$q" --json 2>&1 | tail -c 600; echo
  echo "status: $(status "$acc")"
  HOME=$h timeout 40 "$BAW" wallet agent-id --json 2>&1 | tr -d '\n ' | head -c 300; echo
  [ "$(status "$acc")" = CONNECTED ] && { cp -p "$h/.baw/session.json" "$h/.baw/session.json.good"; rm -f "$LM/so_$acc.json"; } ;;
 backup)
  for a in fumeng fumeng001 fumeng002 fumeng003; do [ "$(status $a)" = CONNECTED ] && cp -p "$(home $a)/.baw/session.json" "$(home $a)/.baw/session.json.good" && echo "$a backed up"; done ;;
 *) echo "usage: $0 check|signin|verify <acc> | backup"; exit 2 ;;
esac
