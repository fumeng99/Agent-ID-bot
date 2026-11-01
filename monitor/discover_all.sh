#!/usr/bin/env bash
bash $AIDBOT_DATA/fmkick.sh > $AIDBOT_DATA/discover_out/fmkick.json 2>&1  # 2026-11-01 常驻快速进度看门狗（巡逻每轮也拉起一次，与快速进度任务错开 → 持续无空档）
# 并行跑所有发现类脚本（只读，不签名不交易），避免串行超时导致 TG/内盘/官网监控整轮没跑。
# 输出：$AIDBOT_DATA/discover_out/<步骤>.json(.err)，以及 summary.txt（每步 exit code 和耗时）。
# 用法：bash discover_all.sh   （总用时约等于最慢一步，≤200 秒）
# 2026-11-01 学  v2.5.0「只允许一份在跑」：两轮重叠会同时写 state.json/found.json（chainscan 曾写坏过 state）。已有一份在跑就直接退出，沿用它的输出。
exec 9>/tmp/discover_all.lock
if ! flock -n 9; then echo "discover_all 已有一份在跑：等它跑完（≤220 秒）直接用它的输出，不重复启动"; flock -w 220 9; cat $AIDBOT_DATA/discover_out/summary.txt 2>/dev/null; exit 0; fi
D=$AIDBOT_DATA; O=$D/discover_out; mkdir -p "$O"; find "$O" -maxdepth 1 -type f \( -name '*.json' -o -name '*.err' -o -name summary.txt \) ! -name fail_streak.json ! -name fp_live.json ! -name possell_live.json ! -name possell_daemon_out.json ! -name possell_reported.json ! -name autofill_fast.json ! -name notify_last_sent.json -delete  # 2026-11-01 20:50：常驻进程的心跳/未汇报卖出结果和失败计数不再每轮删掉
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
# 2026-11-01 20:50：未验证但很热的免费盘（30 分钟 ≥50 钱包、value=0、规则 B 跳过）→ hotfree.json，巡逻放「⚠️ 需处理」问是否破例（每项目一次）
( s=$(date +%s); cd $D && timeout 100 python3 hotfree_unverified.py > $O/hotfree.json 2> $O/hotfree.err; echo "hotfree exit=$? 100s上限 用时=$(( $(date +%s)-s ))s" >> $O/summary.txt )
# 2026-11-01 20:50：fastprogress 自带看门狗（32 秒输出上一轮安全结果）；last 只由它自己在成功时写。外层仍超时/空输出 → 用上一轮结果兜底并标 stale，不留空文件
( s=$(date +%s); cd $D/fairmint && timeout 40 python3 fastprogress.py > $O/fastprogress.json 2> $O/fastprogress.err; rc=$?
  if ! python3 -c "import json,sys;json.load(open(sys.argv[1]))" $O/fastprogress.json 2>/dev/null; then
    python3 -c "import json,sys;o=json.load(open(sys.argv[1]));o.update(crossings=[],hot_alert=[],fastfill_started=[],urgent=False,stale=True,note='本轮输出为空，沿用上一轮结果');json.dump(o,open(sys.argv[2],'w'),ensure_ascii=False)" $D/fairmint/fastprogress_last.json $O/fastprogress.json 2>/dev/null || echo '{"stale":true,"rpc_degraded":true,"crossings":[],"hot":[]}' > $O/fastprogress.json
  fi
  echo "fastprogress exit=$rc 40s上限 用时=$(( $(date +%s)-s ))s" >> $O/summary.txt )
# 2026-11-01 20:47 去噪：每步失败（exit≠0 或输出为空/非 JSON）连续计数，连续 ≥3 轮才进 alert，巡逻只报 alert 里的步骤
python3 - "$O" <<'PY'
import json, os, re, sys
O = sys.argv[1]; F = os.path.join(O, 'fail_streak.json')
try: st = json.load(open(F))
except Exception: st = {}
streak = st.get('streak', {}); last = st.get('last_err', {})
for ln in open(os.path.join(O, 'summary.txt')):
    m = re.match(r'(\S+) exit=(\d+)', ln)
    if not m: continue
    n, rc = m.group(1), int(m.group(2)); out = os.path.join(O, n + '.json'); ok = rc == 0
    try: json.load(open(out))
    except Exception: ok = False
    if ok: streak[n] = 0; last.pop(n, None)
    else:
        streak[n] = streak.get(n, 0) + 1
        try: last[n] = (open(os.path.join(O, n + '.err')).read().strip().splitlines() or [''])[-1][:200] or f'exit={rc}'
        except Exception: last[n] = f'exit={rc}'
alert = [{'step': n, 'rounds': c, 'last_err': last.get(n, '')} for n, c in streak.items() if c >= 3]
json.dump({'streak': streak, 'last_err': last, 'alert': alert}, open(F, 'w'), ensure_ascii=False, indent=1)
print('fail_streak alert:', json.dumps(alert, ensure_ascii=False))
PY
cat "$O/summary.txt"
