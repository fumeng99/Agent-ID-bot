#!/usr/bin/env python3
"""每步留痕：python3 log.py <项目> <步骤> "<内容>" [--tx 0x..] [--contract 0x..] [--source url] [--value 金额] [--wallet 账号]
步骤：discover verify preview execute confirm skip abort brief approve。只追加，写到 $AIDBOT_DATA/events.jsonl（未设则本目录）。"""
import os, sys, json, time
a = sys.argv[1:]
if len(a) < 3 or a[1] not in 'discover verify preview execute confirm skip abort brief approve'.split(): sys.exit(__doc__)
e = {'ts': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'project': a[0], 'step': a[1], 'msg': a[2]}
r = a[3:]
for i in range(0, len(r) - 1, 2):
    if r[i].startswith('--'): e[r[i][2:]] = r[i + 1]
if e['step'] == 'execute' and not e.get('tx'): sys.exit('execute 必须带 --tx')
d = os.environ.get('AIDBOT_DATA') or os.path.dirname(os.path.abspath(__file__)); os.makedirs(d, exist_ok=True)
open(os.path.join(d, 'events.jsonl'), 'a').write(json.dumps(e, ensure_ascii=False) + '\n'); print('ok')
