#!/usr/bin/env python3
"""分阶段/定时开放的免费 Mint 抢铸（value=0）。到点前几秒开始，循环预演，干净即执行，已持有的账号跳过。
用法：
  python3 phase_mint.py --contract 0x.. --data 0x5b70ea9f --at 1793462941        # 已知开放时间（unix 秒）
  python3 phase_mint.py --contract 0x.. --data 0x5b70ea9f --at-selector 0x0fec01a3 # 从合约读开放时间
可选：--window 600（抢多久，秒） --dry
建议用定时任务在开放前 3 分钟启动；10 分钟巡逻覆盖不了几分钟就抢光的阶段。"""
import argparse, json, os, time
from concurrent.futures import ThreadPoolExecutor
import bawlib as L
ap = argparse.ArgumentParser(); ap.add_argument('--contract', required=True); ap.add_argument('--data', required=True)
ap.add_argument('--at', type=int); ap.add_argument('--at-selector'); ap.add_argument('--window', type=int, default=600); ap.add_argument('--dry', action='store_true')
o = ap.parse_args(); C = o.contract
ST = os.path.join(L.DATA, f'phase_{C.lower()}.json'); st = json.load(open(ST)) if os.path.exists(ST) else {}
A = L.accounts('--auto')
start = o.at or (L.call(C, o.at_selector) if o.at_selector else time.time())
print('开放时间', time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(start)), flush=True)
while time.time() < start - 3: time.sleep(min(30, max(1, start - 3 - time.time())))
deadline = time.time() + o.window
while time.time() < deadline:
    for a in A:
        try:
            if st.get(a['label']) != 'done' and L.bal721(C, a['address']) > 0: st[a['label']] = 'done'
        except Exception: pass
    todo = [a for a in A if st.get(a['label']) != 'done']
    if not todo: break
    with ThreadPoolExecutor(min(8, len(todo))) as ex:
        for a, (k, msg) in zip(todo, ex.map(lambda a: L.free_mint(a, C, o.data, dry=o.dry), todo)):
            print(time.strftime('%H:%M:%S'), a['label'], k, msg, flush=True)
            if k == 'exec': st[a['label']] = 'submitted'
    json.dump(st, open(ST, 'w'), ensure_ascii=False)
    if o.dry: break
    time.sleep(8)
for a in A:
    try: print(a['label'], '持有', L.bal721(C, a['address']))
    except Exception as e: print(a['label'], '查询失败', e)
json.dump(st, open(ST, 'w'), ensure_ascii=False)
