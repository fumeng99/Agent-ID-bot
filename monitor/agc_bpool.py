#!/usr/bin/env python3
"""AGC B 档 mintPool 打满。2026-11-05 02:33 用户原话「全部打满b」。usage: agc_bpool.py [--dry] [labels...]"""
import sys, os, json, time
args = sys.argv[1:]
DRYF = '--dry' in args
labels = [x for x in args if not x.startswith('--')]
M = '0x6288dfe86f22b53eeb6886ae2d78941de7483743'
sys.argv = ['fair_buy.py', M, M, '10'] + (['--dry'] if DRYF else [])
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fair_buy.py')).read()
exec(src.split('from concurrent.futures import ThreadPoolExecutor')[0])
R = '0x10ED43C718714eb63d5aA57B78B54704E256024E'
W = 'bb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c'; T = '63dea5acdbe1f27c968907d08610a5f0a0284ba6'
MAINA = next(a for a in A if a['label'] == '主')
KEEP = 3 * 10**15; GAS = 2 * 10**15; P = 2 * 10**15
res = []
for a in A:
    if labels and a['label'] not in labels: continue
    addr = a['bsc']
    q = call(M, '0x' + sel('quota(address,uint256)') + addr[2:].lower().rjust(64, '0') + u(0))
    w = [int(q[2 + 64 * i:66 + 64 * i], 16) for i in range(17)]
    bLeft, opened, paused = w[6], w[15], w[16]
    b = int(rpc('eth_getBalance', [addr, 'latest']), 16)
    n = min(bLeft, max(0, (b - KEEP - GAS) // P))
    if paused or not opened or n <= 0:
        res.append((a['label'], 'skip', 'bLeft', bLeft, 'bal', b / 1e18)); continue
    half = n * P // 2
    o = call(R, '0x' + sel('getAmountsOut(uint256,address[])') + u(half) + u(64) + u(2) + W.rjust(64, '0') + T.rjust(64, '0'))
    minOut = int(o[-64:], 16) * 90 // 100
    ref = '0' * 64 if a is MAINA else MAINA['bsc'][2:].lower().rjust(64, '0')
    data = '0x' + sel('mintPool(uint256,address,uint256)') + u(n) + ref + u(minOut)
    st = go(a, M, data, value=str(n * P))
    print(a['label'], 'bLeft', bLeft, 'n', n, st[0], str(st[1])[:160], flush=True)
    res.append((a['label'], n, n * P / 1e18, st[0], st[1]))
json.dump({'time': time.strftime('%m-%d %H:%M'), 'res': res}, open('discover_out/agc_bpool.json', 'w'), ensure_ascii=False)
print(json.dumps(res, ensure_ascii=False))
