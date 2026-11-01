# 一键并发卖出：五个号同时下市价单把某币卖成 USDT。只在用户明确说卖（或已授权的止损规则触发）时运行。
# usage: python3 fast_sell.py <token> [pct=100] [--slip 8] [--gas HIGH] [--dry] [labels...]
import sys, os, json, subprocess, time
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rpcpool import batch
U = '0x55d398326f99059fF775485246999027B3197955'; B = '$AIDBOT_HOME/baw/node_modules/.bin/baw'
A = json.load(open('$AIDBOT_HOME/baw/accounts/accounts.json'))
args = sys.argv[1:]; DRY = '--dry' in args
def opt(k, d):
    return args[args.index(k) + 1] if k in args else d
SLIP = opt('--slip', '8'); GAS = opt('--gas', 'HIGH')
pos = [x for i, x in enumerate(args) if not x.startswith('--') and (i == 0 or args[i-1] not in ('--slip', '--gas'))]
T = pos[0]; PCT = float(pos[1]) if len(pos) > 1 and pos[1].replace('.', '').isdigit() else 100.0
only = [x for x in pos[1:] if not x.replace('.', '').isdigit()]
acc = [a for a in A if not only or a['label'] in only]
t0 = time.time()
dec = int(batch([('eth_call', [{'to': T, 'data': '0x313ce567'}, 'latest'])])[0], 16)
bals = batch([('eth_call', [{'to': T, 'data': '0x70a08231' + a['bsc'][2:].lower().rjust(64, '0')}, 'latest']) for a in acc])
def one(a, b):
    q = int(b, 16) * PCT / 100 / 10**dec
    if q < 1e-6: return a['label'], 'no_balance'
    qty = ('%.6f' % q).rstrip('0').rstrip('.') if PCT < 100 else str(int(b, 16) / 10**dec)
    if PCT >= 100: qty = '%.6f' % (int(b, 16) // 10**(dec-6) / 1e6) if dec >= 6 else str(int(b, 16) / 10**dec)
    env = dict(os.environ, HOME=a['home'], npm_config_cache='/tmp/npmcache')
    cmd = [B, 'market-order', 'quote' if DRY else 'swap', '--binanceChainId', '56', '--fromTokenQty', qty, '--fromToken', T, '--toToken', U, '--slippage', SLIP, '--json']
    if not DRY: cmd[-1:-1] = ['--gasLevel', GAS]
    r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=150)
    try:
        d = json.loads(r.stdout)['data']
        return a['label'], {'qty': qty, **({'orderId': d['orderId']} if not DRY else {'quote': str(d)[:160]})}
    except Exception:
        return a['label'], {'qty': qty, 'error': (r.stdout + r.stderr)[:200]}
with ThreadPoolExecutor(len(acc)) as ex:
    res = dict(ex.map(lambda ab: one(*ab), zip(acc, bals)))
print(json.dumps({'token': T, 'pct': PCT, 'dry': DRY, 'secs': round(time.time() - t0, 1), 'orders': res}, ensure_ascii=False))
# 2026-11-01 优化：全部卖出后自动核对余额，清零的就从 auto_buy.json 持仓里移除并停掉该币的 price_trigger，避免保本机制对空仓继续触发
if not DRY and PCT >= 100 and all('orderId' in v or v == 'no_balance' for v in res.values()):
    time.sleep(8)
    left = batch([('eth_call', [{'to': T, 'data': '0x70a08231' + a['bsc'][2:].lower().rjust(64, '0')}, 'latest']) for a in acc], tolerant=True)
    if all(x is not None and int(x, 16) == 0 for x in left) and not only:
        P = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'auto_buy.json')
        d = json.load(open(P)); n0 = len(d.get('positions', []))
        d['positions'] = [p for p in d.get('positions', []) if str(p.get('token', '')).lower() != T.lower()]
        if len(d['positions']) != n0: json.dump(d, open(P, 'w'), ensure_ascii=False, indent=1)
        subprocess.run(['pkill', '-f', f'price_trigger.py {T}'], capture_output=True)
        print(json.dumps({'closed': True, 'removed_positions': n0 - len(d['positions'])}, ensure_ascii=False))
    else:
        print(json.dumps({'closed': False, 'left': [a['label'] for a, x in zip(acc, left) if x is None or int(x, 16) > 0]}, ensure_ascii=False))
