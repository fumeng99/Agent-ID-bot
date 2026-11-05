#!/usr/bin/env bash
# 2026-11-05：发码后立刻后台等扫码（auth verify 阻塞到扫码或约 5 分钟过期），成功后马上补跑 AGC 挖矿（refundmine），用户不必回「扫好了」。
acc=$1; LM=$AIDBOT_DATA
out=$(bash $AIDBOT_HOME/baw/safe_session.sh verify "$acc" 2>&1)
echo "$(date '+%m-%d %H:%M:%S') $acc $out" | tail -c 1500 >> $LM/discover_out/relogin.log
if echo "$out" | grep -q "status: CONNECTED"; then
  cd $LM && flock -w 60 /tmp/refundmine.lock timeout 300 python3 refundmine.py >> discover_out/refundmine.log 2>&1
fi
