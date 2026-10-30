#!/usr/bin/env python3
"""自动补 gas：账号 BNB < GAS_MIN_BNB 且 USDT >= GAS_TOPUP_USDT 时，用 USDT 经 baw market-order swap 换 BNB。
每号每天最多 GAS_MAX_PER_DAY 次（账本 $AIDBOT_DATA/gas_topup.json）。只做 USDT->BNB。需用户事先授权。
用法：python3 gas_topup.py [--dry]"""
import json, os, subprocess, sys, time, urllib.request
here = os.path.dirname(os.path.abspath(__file__))
H = os.environ.get('AIDBOT_HOME', os.path.expanduser('~/.agent-id-bot'))
DATA = os.environ.get('AIDBOT_DATA', os.path.join(H, 'data')); os.makedirs(DATA, exist_ok=True)
BAW = os.path.join(H, 'baw', 'node_modules', '.bin', 'baw'); LED = os.path.join(DATA, 'gas_topup.json')
RPC = os.environ.get('AIDBOT_RPC', 'https://bsc-rpc.publicnode.com')
USDT = '0x55d398326f99059fF775485246999027B3197955'; NATIVE = '0x' + 'e' * 40
MIN_BNB = float(os.environ.get('GAS_MIN_BNB', 0.003)); AMT = float(os.environ.get('GAS_TOPUP_USDT', 6)); MAX_DAY = int(os.environ.get('GAS_MAX_PER_DAY', 2))
DRY = '--dry' in sys.argv
sys.path.insert(0, here); from rpcpool import batch
accs = [l.split(' ', 2) for l in subprocess.run([sys.executable, os.path.join(here, 'accounts.py'), '--auto'], capture_output=True, text=True).stdout.splitlines() if l.strip()]
led = json.load(open(LED)) if os.path.exists(LED) else {}
day = time.strftime('%Y-%m-%d', time.gmtime(time.time() + 8 * 3600))
# 一次批量查完所有账号的 BNB 和 USDT，节点轮换防 429
try: R = batch([c for _, _, a in accs for c in (('eth_getBalance', [a, 'latest']), ('eth_call', [{'to': USDT, 'data': '0x70a08231' + a[2:].lower().rjust(64, '0')}, 'latest']))])
except Exception as e: R = None; print('余额查询失败（所有节点）：', e)
for i, (d, label, addr) in enumerate(accs):
    if R is None: print(label, '余额未查到'); continue
    bnb = int(R[2 * i], 16) / 1e18; usdt = int(R[2 * i + 1], 16) / 1e18
    n = len([x for x in led.get(label, []) if x['day'] == day])
    if bnb >= MIN_BNB: print(label, f'BNB {bnb:.4f} 够用'); continue
    if usdt < AMT: print(label, f'BNB {bnb:.5f} 不足且 USDT 只有 {usdt:.2f}，需用户充值'); continue
    if n >= MAX_DAY: print(label, f'BNB {bnb:.5f} 不足，今天已补 {n} 次到上限，需用户确认'); continue
    if DRY: print(label, f'[dry] 将用 {AMT}U 换 BNB'); continue
    env = dict(os.environ, HOME=os.path.join(H, 'baw', 'accounts', d))
    r = subprocess.run([BAW, 'market-order', 'swap', '--binanceChainId', '56', '--fromTokenQty', str(AMT), '--fromToken', USDT, '--toToken', NATIVE, '--json'], env=env, capture_output=True, text=True, timeout=120)
    try: oid = json.loads(r.stdout)['data']['orderId']
    except Exception: print(label, '换 BNB 失败', (r.stdout + r.stderr)[:200]); continue
    led.setdefault(label, []).append({'day': day, 'usdt': AMT, 'orderId': oid}); json.dump(led, open(LED, 'w'), ensure_ascii=False, indent=1)
    print(label, f'已用 {AMT}U 换 BNB（原 {bnb:.5f} BNB），orderId {oid}')
