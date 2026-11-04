#!/usr/bin/env python3
"""推送「▍账户」行专用（2026-11-01 加：此前巡逻手工查余额出现「主 未查到」）。只读。
输出 JSON：line 字段直接贴进推送；失败的号会重试一次再标「查询失败」，不会静默漏掉。"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rpcpool import batch
A = json.load(open('$AIDBOT_HOME/baw/accounts/accounts.json'))
U = '0x55d398326f99059fF775485246999027B3197955'
def q():
    return batch([c for a in A for c in (('eth_getBalance', [a['bsc'], 'latest']),
                  ('eth_call', [{'to': U, 'data': '0x70a08231' + a['bsc'][2:].lower().rjust(64, '0')}, 'latest']))], tolerant=True)
r = q()
if any(x is None for x in r):
    r2 = q(); r = [x if x is not None else y for x, y in zip(r, r2)]
out, parts = {}, []
for i, a in enumerate(A):
    b, u = r[2 * i], r[2 * i + 1]
    if b is None: parts.append(f"{a['label']} 查询失败"); out[a['label']] = None; continue
    bnb = int(b, 16) / 1e18; usdt = int(u, 16) / 1e18 if u else None
    out[a['label']] = {'bnb': round(bnb, 4), 'usdt': round(usdt, 2) if usdt is not None else None}
    parts.append(f"{a['label']} {bnb:.4f}" + (' ⚠️' if bnb < 0.005 else ''))
print(json.dumps({'line': f"▍账户：BNB " + ' · '.join(parts), 'accounts': out}, ensure_ascii=False))
