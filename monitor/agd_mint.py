#!/usr/bin/env python3
"""Agentic Digger（AGD）A 档：押 0.002 BNB/份，24h 后 claimA 全退 + AGD。
2026-11-07 01:35 用户原话「打 先打主 然后走走主的邀请码再打」（回答「六号今天各打 5 份 A，24h 后自动领回并全卖？」）。
主号先打（referrer=0），其余号 referrer=主号。只打 A，不打 B。unknown 不重发。usage: agd_mint.py [--dry]"""
import sys, os, json, time
args = sys.argv[1:]
M = '0x9a467a8844d1394a84bc502669faa30573576bac'
sys.argv = ['fair_buy.py', M, M, '10'] + (['--dry'] if '--dry' in args else [])
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fair_buy.py')).read()
exec(src.split('from concurrent.futures import ThreadPoolExecutor')[0])
MAINA = next(a for a in A if a['label'] == '主')
order = [MAINA] + [a for a in A if a is not MAINA]
KEEP = 3 * 10**15; PRICE = 2 * 10**15
res = []
for a in order:
    pad = a['bsc'][2:].lower().rjust(64, '0')
    q = call(M, '0x' + sel('quota(address)') + pad); w = [int(q[2 + 64 * i:66 + 64 * i], 16) for i in range(5)]
    n = w[0] - w[1]
    b = int(rpc('eth_getBalance', [a['bsc'], 'latest']), 16)
    if n <= 0: res.append((a['label'], 'quota_full', w)); continue
    if b - n * PRICE - 10**15 < KEEP: res.append((a['label'], 'low_bnb', b / 1e18)); continue
    qa = int(call(M, '0x' + sel('quoteA(uint256)') + u(n))[2:66], 16)
    ref = '0' * 64 if a is MAINA else MAINA['bsc'][2:].lower().rjust(64, '0')
    dl = int(time.time()) + 600
    data = '0x' + sel('mintA(uint256,address,uint256,uint256)') + u(n) + ref + u(qa * 99 // 100) + u(dl)
    st = go(a, M, data, value=str(n * PRICE))
    print(a['label'], n, st[0], str(st[1])[:100], flush=True)
    res.append((a['label'], n, st[0], st[1]))
    if a is MAINA:
        if st[0] != 'ok' and '--dry' not in args: break
        time.sleep(5)
print(json.dumps(res, ensure_ascii=False))
