#!/bin/bash
# Clawby 数据接口调用：clawby.sh <接口名> '<json 参数>'，接口见 $AIDBOT_HOME/clawby/clawby-data/api/catalog/
# 密钥读环境变量 CLAWBY_API_KEY（openclawby.com 免费登录获取），不落盘。
# 免费档 5 次/秒、20 次/分，超限会临时封 IP：串行加锁，每次间隔 ≥3.2 秒（≤18 次/分）。
[ -n "${CLAWBY_API_KEY:-}" ] && H=(-H "X-API-Key: $CLAWBY_API_KEY") || H=()
exec 9>/tmp/clawby.lock; flock 9
last=$(cat /tmp/clawby.last 2>/dev/null || echo 0); now=$(date +%s.%N)
sleep "$(python3 -c "print(max(0,3.2-($now-$last)))")"
curl -s -X POST https://api.openclawby.com/api/relay "${H[@]}" -H "Content-Type: application/json" \
  -d "{\"name\":\"$1\",\"params\":${2:-{\}}}"
date +%s.%N > /tmp/clawby.last
