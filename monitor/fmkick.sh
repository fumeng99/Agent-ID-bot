#!/bin/bash
# 常驻快速进度看门狗（2026-11-01 安小将教训：定时任务之间有空档，热盘 4 分钟冲满）。
# 每次运行：若 fastprogress 常驻进程不在，就后台起一个跑 14 分钟（--daemon，文件锁保证只有一个）。
# 「Fair mint 快速进度」和「免费Mint巡逻」两个任务每 10 分钟各调一次、错开 5 分钟 → 任何时刻都有常驻进程在盯，没有空档。
cd $AIDBOT_DATA/fairmint
FP_OK=0
if [ -f ../discover_out/fp_daemon.pid ] && kill -0 "$(cat ../discover_out/fp_daemon.pid)" 2>/dev/null; then
  hb=$(python3 -c "import json,time;print(int(time.time()-json.load(open('../discover_out/fp_live.json')).get('heartbeat',0)))" 2>/dev/null || echo 999)
  if [ "$hb" -lt 120 ]; then echo "{\"daemon\":\"running\",\"pid\":$(cat ../discover_out/fp_daemon.pid),\"heartbeat_age_s\":$hb}"; FP_OK=1
  else kill "$(cat ../discover_out/fp_daemon.pid)" 2>/dev/null; sleep 1; fi   # 心跳超 2 分钟 = 卡死，重启
fi
if [ "$FP_OK" = 0 ]; then
python3 - <<'P'
import subprocess
subprocess.Popen(['bash','-c','cd $AIDBOT_DATA/fairmint && timeout 900 python3 fastprogress.py --daemon --loop 840 --every 20 >> ../discover_out/fp_daemon.log 2>&1'], start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
P
sleep 3
echo "{\"daemon\":\"started\",\"pid\":$(cat ../discover_out/fp_daemon.pid 2>/dev/null || echo null)}"
fi
# 2026-11-01 18:45 B：持仓卖出也常驻（possell --daemon，文件锁只留一个；持仓盘开池 15 分钟内 3 秒一轮）
cd $AIDBOT_DATA
hb=$(python3 -c "import json,time;print(int(time.time()-json.load(open('discover_out/possell_live.json')).get('heartbeat',0)))" 2>/dev/null || echo 999)
if [ "$hb" -ge 120 ]; then
  pid=$(python3 -c "import json;print(json.load(open('discover_out/possell_live.json')).get('pid',''))" 2>/dev/null)
  [ -n "$pid" ] && [ "$hb" -lt 999 ] && kill "$pid" 2>/dev/null && sleep 1
  python3 - <<'P'
import subprocess
subprocess.Popen(['bash','-c','cd $AIDBOT_DATA && timeout 900 python3 possell.py --daemon --loop 840 --every 20 >> discover_out/possell_daemon.log 2>&1'], start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
P
  echo '{"possell_daemon":"started"}'
else echo "{\"possell_daemon\":\"running\",\"heartbeat_age_s\":$hb}"; fi
