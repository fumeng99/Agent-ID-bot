#!/usr/bin/env python3
# 2026-11-06 用户「彻底解决卡单」：兜底扫尾。每轮读六号 AGC 链上余额，价值达标且当前没有卖单在跑 → 起 sell_retry 补卖。
# 2026-11-23 两处修正：
#   1) 卖前过 sellguard（聚合器报价判死写缓存），命中直接跳过，不再白烧 gas；
#   2) 阈值判据改用 market-order quote 的税后到账量（旧的 V2 getAmountsOut 是税前报价，
#      对带 2.25% 税 / 多档池的 AGC 会高估；V2 报价仅在接口失败时作兜底）。
import json, os, subprocess, sys, time, fcntl
D = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, D)
from rpcpool import batch
from sellguard import can_sell, unsellable

AIDBOT_HOME = os.environ.get('AIDBOT_HOME') or os.path.expanduser('~/.agent-id-bot')
BAW = os.path.join(AIDBOT_HOME, 'baw/node_modules/.bin/baw')
T = '0x63dea5acdbe1f27c968907d08610a5f0a0284ba6'
USDT = '0x55d398326f99059ff775485246999027b3197955'
MIN_USD = float(os.environ.get('AGC_SWEEP_MIN_USD', '0.3'))
A = json.load(open(AIDBOT_HOME + '/baw/accounts/accounts.json'))

lk = open(f'/tmp/sell_{T[2:10]}.lock', 'w')
try: fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError: sys.exit(0)  # 有卖单在跑，下轮再看
fcntl.flock(lk, fcntl.LOCK_UN); lk.close()

R = '0x10ED43C718714eb63d5aA57B78B54704E256024E'


def amt_out_v2(a):  # 兜底：V2 路径报价（税前，仅用于接口失败时）
    p = ['63dea5acdbe1f27c968907d08610a5f0a0284ba6', 'bb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c', '55d398326f99059ff775485246999027b3197955']
    data = '0xd06ca61f' + hex(a)[2:].rjust(64, '0') + hex(64)[2:].rjust(64, '0') + hex(3)[2:].rjust(64, '0') + ''.join(x.rjust(64, '0') for x in p)
    try:
        o = batch([('eth_call', [{'to': R, 'data': data}, 'latest'])])[0]
        return int(o[-64:], 16) / 1e18, 'v2_quote'
    except Exception:
        return None, 'v2_quote_failed'


def amt_out_net(account, balance_wei, dec):
    """税后到账量（聚合器报价），失败回落到 V2 报价。"""
    whole, frac = divmod(balance_wei, 10 ** dec)
    qty = str(whole) if frac == 0 else ('%d.%s' % (whole, str(frac).rjust(dec, '0').rstrip('0')))
    env = dict(os.environ, HOME=account.get('home') or '', AIDBOT_HOME=AIDBOT_HOME)
    try:
        p = subprocess.run([BAW, 'market-order', 'quote', '--binanceChainId', '56', '--fromTokenQty', qty,
                            '--fromToken', T, '--toToken', USDT, '--slippage', 'auto', '--json'],
                           capture_output=True, text=True, env=env, timeout=90)
        d = (json.loads((p.stdout or '') + (p.stderr or '')).get('data') or {})
        return float(d.get('toCoinAmount') or 0), 'quote_net'
    except Exception:
        return amt_out_v2(balance_wei)


bals = batch([('eth_call', [{'to': T, 'data': '0x70a08231' + a['bsc'][2:].lower().rjust(64, '0')}, 'latest']) for a in A], tolerant=True)
dec_raw = batch([('eth_call', [{'to': T, 'data': '0x313ce567'}, 'latest'])])[0]
dec = int(dec_raw, 16) if dec_raw else 18

why = unsellable(T)
todo, skipped = [], []
for a, b in zip(A, bals):
    if not b: continue
    v = int(b, 16)
    if v <= 0: continue
    if why:
        skipped.append((a['label'], 'unsellable_cached')); continue
    usd, src = amt_out_net(a, v, dec)
    if usd is None or usd < MIN_USD:
        skipped.append((a['label'], 'below_min_%.2f(%s)' % (MIN_USD, src))); continue
    ok, reason = can_sell(T, v, a)
    if not ok:
        skipped.append((a['label'], str(reason)[:80])); continue
    todo.append(a['label'])

if todo or skipped:
    lf = open(os.path.join(D, 'discover_out/agc_sell.log'), 'a')
    for L in skipped:
        lf.write(time.strftime('%m-%d %H:%M:%S ', time.gmtime(time.time() + 8 * 3600)) + f'skip {L[0]} {L[1]}\n')
    for L in todo:
        lf.write(time.strftime('%m-%d %H:%M:%S ', time.gmtime(time.time() + 8 * 3600)) + f'sweep 残余 {L} -> sell {T}\n'); lf.flush()
        subprocess.Popen(['python3', 'sell_retry.py', T, '100', '--slip', '8', L], stdout=lf, stderr=lf, cwd=D, start_new_session=True)
print(json.dumps({'sweep': todo, 'skipped': skipped}, ensure_ascii=False))