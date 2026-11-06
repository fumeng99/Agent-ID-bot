#!/usr/bin/env python3
# 只输出 salearm_events.jsonl 中尚未汇报的新行（JSON 列表），并推进偏移。无新行输出空列表 []。
import json, time, os
D = os.path.dirname(os.path.abspath(__file__)) + '/discover_out/'
L = open(D + 'salearm_events.jsonl', errors='ignore').read().splitlines()
try: n = json.load(open(D + 'salearm_reported.json')).get('lines', 0)
except Exception: n = len(L)
new = [x for x in L[n:] if x.strip()]
json.dump({'lines': len(L), 'updated': time.strftime('%Y-%m-%d %H:%M CST', time.gmtime(time.time() + 8 * 3600))}, open(D + 'salearm_reported.json', 'w'))
print(json.dumps([json.loads(x) for x in new], ensure_ascii=False))
