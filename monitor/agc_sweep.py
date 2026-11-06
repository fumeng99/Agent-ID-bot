#!/usr/bin/env python3
# 2026-11-06 用户「彻底解决卡单」：兜底扫尾。每轮读六号 AGC 链上余额，价值 ≥0.3U 且当前没有卖单在跑 → 起 sell_retry 补卖（按用户「领取了立马卖」规则，滑点 ≤8%）。
import json, os, subprocess, sys, time, fcntl
D = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, D)
from rpcpool import batch
T = '0x63dea5acdbe1f27c968907d08610a5f0a0284ba6'
A = json.load(open('$AIDBOT_HOME/baw/accounts/accounts.json'))
lk = open(f'/tmp/sell_{T[2:10]}.lock', 'w')
try: fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError: sys.exit(0)  # 有卖单在跑，下轮再看
fcntl.flock(lk, fcntl.LOCK_UN); lk.close()
R = '0x10ED43C718714eb63d5aA57B78B54704E256024E'
def amt_out(a):  # AGC→WBNB→USDT 报价
    p = ['63dea5acdbe1f27c968907d08610a5f0a0284ba6', 'bb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c', '55d398326f99059ff775485246999027b3197955']
    data = '0xd06ca61f' + hex(a)[2:].rjust(64, '0') + hex(64)[2:].rjust(64, '0') + hex(3)[2:].rjust(64, '0') + ''.join(x.rjust(64, '0') for x in p)
    o = batch([('eth_call', [{'to': R, 'data': data}, 'latest'])])[0]; return int(o[-64:], 16) / 1e18
bals = batch([('eth_call', [{'to': T, 'data': '0x70a08231' + a['bsc'][2:].lower().rjust(64, '0')}, 'latest']) for a in A], tolerant=True)
todo = []
for a, b in zip(A, bals):
    if not b: continue
    v = int(b, 16)
    if v > 10**18 and amt_out(v) >= 0.3: todo.append(a['label'])
if todo:
    lf = open(os.path.join(D, 'discover_out/agc_sell.log'), 'a')
    for L in todo:
        lf.write(time.strftime('%m-%d %H:%M:%S ', time.gmtime(time.time() + 8 * 3600)) + f'sweep 残余 {L} -> sell {T}\n'); lf.flush()
        subprocess.Popen(['python3', 'sell_retry.py', T, '100', '--slip', '8', L], stdout=lf, stderr=lf, cwd=D, start_new_session=True)
print(json.dumps({'sweep': todo}, ensure_ascii=False))
