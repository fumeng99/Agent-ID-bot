#!/bin/bash
python3 -c "import Crypto" 2>/dev/null || pip install -q pycryptodome >/dev/null 2>&1
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
# 2026-11-02 16:3x（用户「确保不会遗漏」）：常驻发现进程 discoverd.py（约 60 秒一轮扫新 Minted / 新部署合约 / PairCreated，块游标持久化不跳块；
# 每小时自己拉起一次对账）。不设寿命（游标在状态文件里，重启不丢块）；心跳超 180 秒或进程不在就重启；文件锁保证只有一份。
cd $AIDBOT_DATA
hb=$(python3 -c "import json,time;print(int(time.time()-json.load(open('discover_out/discoverd_hb.json')).get('heartbeat',0)))" 2>/dev/null || echo 999)
dpid=$(cat discover_out/discoverd.pid 2>/dev/null)
if [ -n "$dpid" ] && kill -0 "$dpid" 2>/dev/null && [ "$hb" -lt 180 ]; then echo "{\"discoverd\":\"running\",\"pid\":$dpid,\"heartbeat_age_s\":$hb}"
else
  [ -n "$dpid" ] && kill "$dpid" 2>/dev/null && sleep 1
  python3 - <<'P'
import subprocess
subprocess.Popen(['bash','-c','cd $AIDBOT_DATA/fairmint && exec python3 discoverd.py --daemon --every 60 >> ../discover_out/discoverd.log 2>&1'], start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
P
  sleep 3; echo "{\"discoverd\":\"started\",\"pid\":$(cat discover_out/discoverd.pid 2>/dev/null || echo null),\"prev_heartbeat_age_s\":$hb}"
fi
# 每种常驻进程只留一份：同名进程多于一份时，保留持锁的那份（pid 文件/心跳里的 pid），其余结束
for spec in "fastprogress.py --daemon:discover_out/fp_daemon.pid" "possell.py --daemon:discover_out/possell_live.json" "discoverd.py --daemon:discover_out/discoverd.pid"; do
  pat=${spec%%:*}; pf=${spec#*:}
  keep=$(python3 -c "import json,sys;p=sys.argv[1];t=open(p).read().strip();print(json.loads(t).get('pid','') if t.startswith('{') else t)" "$pf" 2>/dev/null)
  pids=$(pgrep -f "^python3 $pat" || true); n=$(echo "$pids" | grep -c . || true)
  if [ "${n:-0}" -gt 1 ] && [ -n "$keep" ]; then for p in $pids; do [ "$p" != "$keep" ] && kill "$p" 2>/dev/null; done; echo "{\"dedup\":\"$pat\",\"had\":$n,\"kept\":$keep}"; fi
done
# 2026-11-10 用户「改」（PEPE AGENT 漏报）：TG 快速通道 tgfast.py 常驻，每 15 秒拉频道，新帖带网址/合约/「进度XX%」立刻扫官网读进度
hb=$(python3 -c "import json,time;print(int(time.time()-json.load(open('discover_out/tgfast_hb.json')).get('heartbeat',0)))" 2>/dev/null || echo 999)
if [ "$hb" -lt 120 ]; then echo "{\"tgfast\":\"running\",\"heartbeat_age_s\":$hb}"
else
  pkill -f "^python3 tgfast.py --daemon" 2>/dev/null; sleep 1
  python3 -c "import subprocess;subprocess.Popen(['bash','-c','cd $AIDBOT_DATA/tg && exec python3 tgfast.py --daemon --every 15 >> ../discover_out/tgfast.log 2>&1'],start_new_session=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)"
  echo "{\"tgfast\":\"started\",\"prev_heartbeat_age_s\":$hb}"
fi
# 2026-11-10 AMNT（agentmining.fun）Plan B CPU 挖矿常驻：value=0 免费，按 0 元 Mint 规则 preview 干净才执行
if ! pgrep -f "^python3 amnt_mine.py" >/dev/null; then
  python3 -c "import subprocess;subprocess.Popen(['bash','-c','cd $AIDBOT_DATA && TZ=Asia/Shanghai exec python3 amnt_mine.py >> discover_out/amnt_mine.out 2>&1'],start_new_session=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)"
  echo '{"amnt_mine":"started"}'
else echo '{"amnt_mine":"running"}'; fi
