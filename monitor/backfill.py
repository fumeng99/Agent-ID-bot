#!/usr/bin/env python3
"""新号补领历史免费 Mint：拿已成功账号的 mint 交易 calldata 作模板，换成新号地址后逐个预演，干净才执行（value=0）。
输入：$AIDBOT_DATA/seen.json，列表里每项 {"name","type":"免费...","accounts":{"<label>":"<txhash>"}}。
安全：calldata 里含签名、Merkle proof、长动态数据的项目不盲目替换（签名/证明绑定原地址，换了也会失败或有风险），列出来交给人工/官网流程。
用法：python3 backfill.py [--dry]"""
import json, os, re, sys
from concurrent.futures import ThreadPoolExecutor
import bawlib as L
DRY = '--dry' in sys.argv; SEEN = os.path.join(L.DATA, 'seen.json')
S = json.load(open(SEEN)); A = {a['label']: a for a in L.accounts('--auto')}
MAX_WORDS = 6  # 超过 6 个参数字（或带动态 bytes）视为可能含签名/证明
def risky(inp):
    body = inp[10:]; words = [body[i:i + 64] for i in range(0, len(body), 64)]
    if len(words) > MAX_WORDS: return '参数过长，可能含签名/证明'
    if any(w == '0' * 62 + '41' for w in words): return '含 65 字节签名'
    return None
jobs, manual = [], []
for x in S:
    if not isinstance(x, dict) or '免费' not in str(x.get('type', '')): continue
    acc = x.get('accounts') or {}
    tmpl = next(((l, m.group(0)) for l, v in acc.items() if l in A for m in [re.search(r'0x[0-9a-fA-F]{64}', str(v))] if m), None)
    missing = [l for l in A if l not in acc]
    if not tmpl or not missing: continue
    tx = L.rpc('eth_getTransactionByHash', [tmpl[1]])
    if not tx or int(tx['value'], 16) != 0: continue
    inp = tx['input'].lower(); why = risky(inp)
    if why: manual.append((x.get('name'), why)); continue
    src = A[tmpl[0]]['address'][2:].lower()
    for l in missing: jobs.append((x.get('name'), l, tx['to'], inp.replace(src, A[l]['address'][2:].lower())))
print('补领任务', len(jobs), '需人工/官网流程', len(manual), flush=True)
for n, why in manual: print('人工', n, why)
by = {}
for j in jobs: by.setdefault(j[1], []).append(j)
def lane(js): return [(n, l, *L.free_mint(A[l], to, data, dry=DRY)) for n, l, to, data in js]
res = []
with ThreadPoolExecutor(max(1, len(by))) as ex:
    for r in ex.map(lane, by.values()): res += r
for r in res: print(*r, flush=True)
if not DRY:
    for n, l, k, v in res:
        if k == 'exec':
            h = re.search(r'0x[0-9a-fA-F]{64}', v)
            for x in S:
                if isinstance(x, dict) and x.get('name') == n: x.setdefault('accounts', {})[l] = h.group(0) if h else 'submitted'
    json.dump(S, open(SEEN, 'w'), ensure_ascii=False, indent=1)
