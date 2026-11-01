#!/usr/bin/env bash
bash $AIDBOT_DATA/fmkick.sh > $AIDBOT_DATA/discover_out/fmkick.json 2>&1  # 2026-11-01 常驻快速进度看门狗（巡逻每轮也拉起一次，与快速进度任务错开 → 持续无空档）
# 并行跑所有发现类脚本（只读，不签名不交易），避免串行超时导致 TG/内盘/官网监控整轮没跑。
# 输出：$AIDBOT_DATA/discover_out/<步骤>.json(.err)，以及 summary.txt（每步 exit code 和耗时）。
# 用法：bash discover_all.sh   （总用时约等于最慢一步，≤200 秒）
# 2026-11-01 学  v2.5.0「只允许一份在跑」：两轮重叠会同时写 state.json/found.json（chainscan 曾写坏过 state）。已有一份在跑就直接退出，沿用它的输出。
exec 9>/tmp/discover_all.lock
if ! flock -n 9; then echo "discover_all 已有一份在跑：等它跑完（≤220 秒）直接用它的输出，不重复启动"; flock -w 220 9; cat $AIDBOT_DATA/discover_out/summary.txt 2>/dev/null; exit 0; fi
D=$AIDBOT_DATA; O=$D/discover_out; mkdir -p "$O"; rm -f "$O"/*.json "$O"/*.err "$O"/summary.txt
run() { # name timeout dir cmd...
  local n=$1 t=$2 dir=$3; shift 3
  ( s=$(date +%s); cd "$dir" && timeout "$t" "$@" > "$O/$n.json" 2> "$O/$n.err"; rc=$?
    echo "$n exit=$rc ${t}s上限 用时=$(( $(date +%s)-s ))s" >> "$O/summary.txt" ) &
}
run chainscan 200 $D/chainscan env SCAN_BUDGET=60 python3 scan.py
run regscan 110 $D/chainscan env REG_DEADLINE=95 python3 regscan.py  # 免费登记/领取（无 Transfer 事件，如 BossFreeRegister register()），候选同样并入 candidates.json
run sitescan 110 $D python3 sitescan.py
run tg 90 $D python3 tg/tgcall.py agentidcall dashutiaozi
run platforms 90 $D python3 platforms/platforms.py
run watch_sites 110 $D python3 watch_sites.py
run fairmint 110 $D/fairmint env FM_DEADLINE=100 python3 fairmint.py --minutes 30  # 含「已部署未开铸」预警与快到门槛 20 秒重读
run heatscan 100 $D/fairmint env FM_DEADLINE=90 python3 heatscan.py  # 2026-11-01 学  第3/5/6层：QuickNode 400 块一段扫铸造事件，Agent ID 新项目 + 付费盘并入 found.json
run opencheck 90 $D python3 opencheck.py
run approvals 60 $D python3 approvals_watch.py  # 2026-11-01 学  v0.4.1：授权巡检（只读 baw approvals list），新增授权→alerts，只提醒不撤销
run claimscan 110 $D python3 claimscan.py 80  # 可领取奖励（只读）
wait
# 免费候选预检：依赖 chainscan 结果，所以放在 wait 之后
( s=$(date +%s); cd $D && timeout 100 python3 precheck.py > $O/precheck.json 2> $O/precheck.err; echo "precheck exit=$? 100s上限 用时=$(( $(date +%s)-s ))s" >> $O/summary.txt )
# 2026-11-01 审计：开池进度高频检查（只读 eth_call，5 秒左右）：跨 30/60/70%、提前开池、速度/预计到 70% 分钟数、待预审列表
( s=$(date +%s); cd $D/fairmint && timeout 40 python3 fastprogress.py > $O/fastprogress.json 2> $O/fastprogress.err; rc=$?; cp $O/fastprogress.json $D/fairmint/fastprogress_last.json 2>/dev/null; echo "fastprogress exit=$rc 40s上限 用时=$(( $(date +%s)-s ))s" >> $O/summary.txt )
cat "$O/summary.txt"
