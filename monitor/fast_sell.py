# 一键并发卖出：各号同时下市价单把某币卖成 USDT。只在用户明确说卖（或已授权的止损规则触发）时运行。
# usage: python3 fast_sell.py <token> [pct=100] [--slip 8] [--gas HIGH] [--chunk N] [--dry] [labels...]
# 2026-11-24：加自适应分片。AGENTMINT 实测单笔 1,000,000 枚被服务端以 slippage 不足拒单，
#   旧逻辑每号只下一笔 -> 永远卖不掉。现在：单笔失败就按 1/2、1/4、1/8 降档，直到余额清零。
import sys, os, json, subprocess, time
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rpcpool import batch
U = '0x55d398326f99059fF775485246999027B3197955'; B = '$AIDBOT_HOME/baw/node_modules/.bin/baw'
A = json.load(open('$AIDBOT_HOME/baw/accounts/accounts.json'))
args = sys.argv[1:]; DRY = '--dry' in args
def opt(k, d):
    return args[args.index(k) + 1] if k in args else d
SLIP = str(min(float(opt('--slip', '8')), 15))  # D：滑点上限 15%
GAS = opt('--gas', 'HIGH')
CHUNK = int(float(opt('--chunk', '0') or 0))  # 0 = 用余额一次试，失败自动降档
PX = float(opt('--px', '0') or 0)
pos = [x for i, x in enumerate(args) if not x.startswith('--') and (i == 0 or args[i-1] not in ('--slip', '--gas', '--px', '--chunk'))]
LABELS = {a['label'] for a in A}
T = pos[0]
PCT = float(pos[1]) if len(pos) > 1 and pos[1] not in LABELS and pos[1].replace('.', '').isdigit() else 100.0
only = [x for x in pos[1:] if x in LABELS]
bad = [x for x in pos[1:] if x not in LABELS and not (x == pos[1] and x.replace('.', '').isdigit())]
if bad: print(json.dumps({'error': f'未知账号标签 {bad}，不下单'}, ensure_ascii=False)); sys.exit(2)
acc = [a for a in A if not only or a['label'] in only]
t0 = time.time()
dec = int(batch([('eth_call', [{'to': T, 'data': '0x313ce567'}, 'latest'])])[0], 16)
bals = batch([('eth_call', [{'to': T, 'data': '0x70a08231' + a['bsc'][2:].lower().rjust(64, '0')}, 'latest']) for a in acc])


def fmt(wei):
    whole, frac = divmod(int(wei), 10**dec)
    return str(whole) if frac == 0 else ('%d.%s' % (whole, str(frac).rjust(dec, '0').rstrip('0')))


def bal_of(addr):
    r = batch([('eth_call', [{'to': T, 'data': '0x70a08231' + addr[2:].lower().rjust(64, '0')}, 'latest'])])[0]
    return int(r, 16) if r else 0


def quote_ok(env, qty):
    qr = subprocess.run([B, 'market-order', 'quote', '--binanceChainId', '56', '--fromTokenQty', qty, '--fromToken', T, '--toToken', U, '--slippage', SLIP, '--json'], env=env, capture_output=True, text=True, timeout=60)
    try:
        d = json.loads(qr.stdout).get('data') or {}
        return bool(d), d
    except Exception:
        return False, {'raw': (qr.stdout + qr.stderr)[:160]}


def single(env, qty):
    """下一笔。返回 (ok, info)。"""
    ok_q, qd = quote_ok(env, qty) if not DRY else (True, {})
    if not ok_q:
        return False, {'qty': qty, 'error': 'quote 预演失败：' + json.dumps(qd, ensure_ascii=False)[:140], 'kind': 'quote'}
    if PX and qd:
        try:
            got = float(qd.get('toCoinAmount') or 0); qpx = got / float(qty)
            if qpx < PX * 0.5:
                return False, {'qty': qty, 'error': f'quote_mismatch 报价 {got:.4g}U 仅为估算 {qpx / PX * 100:.1f}%', 'kind': 'px'}
        except Exception: pass
    cmd = [B, 'market-order', 'quote' if DRY else 'swap', '--binanceChainId', '56', '--fromTokenQty', qty, '--fromToken', T, '--toToken', U, '--slippage', SLIP, '--json']
    if not DRY: cmd[-1:-1] = ['--gasLevel', GAS]
    r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=150)
    try:
        d = json.loads(r.stdout)['data']
    except Exception:
        txt = (r.stdout + r.stderr)
        kind = 'size' if ('slippage is not enough' in txt.lower() or '30003001' in txt) else 'other'
        return False, {'qty': qty, 'error': txt[:200], 'kind': kind}
    if DRY: return True, {'qty': qty, 'quote': str(d)[:160]}
    oid = d['orderId']
    for _ in range(15):
        time.sleep(4)
        try:
            L2 = json.loads(subprocess.run([B, 'market-order', 'list', '--json'], env=env, capture_output=True, text=True, timeout=60).stdout)['data']
            L2 = L2.get('list', L2) if isinstance(L2, dict) else L2
            o = next((x for x in L2 if x.get('orderId') == oid), None)
        except Exception:
            o = None
        if o and o.get('status') == 'FINISHED':
            return True, {'qty': qty, 'orderId': oid, 'got_u': o.get('toTokenActualQty'), 'tx': o.get('txHash')}
        if o and o.get('status') in ('FAILED', 'CANCELLED', 'EXPIRED'):
            return False, {'qty': qty, 'orderId': oid, 'error': f"订单 {oid} {o['status']}", 'kind': 'order'}
    return False, {'qty': qty, 'orderId': oid, 'error': f'订单 {oid} 60 秒未确认，不重发', 'kind': 'order'}


def one(a, b):
    """按余额循环卖；单笔失败自动降档（1/2、1/4、1/8…），直到清零或降无可降。"""
    env = dict(os.environ, HOME=a['home'], npm_config_cache='/tmp/npmcache')
    raw = int(b, 16) if b else 0
    target = raw * PCT / 100 if PCT < 100 else raw
    left = int(target)
    if left <= 0: return a['label'], 'no_balance'
    chunk = CHUNK * 10**dec if CHUNK else left
    chunk = min(chunk, left)
    got_u = 0.0; orders = []; fails = 0
    while left > 0 and fails < 8:
        ok, info = single(env, fmt(chunk))
        if ok:
            orders.append(info)
            got_u += float(info.get('got_u') or 0)
            time.sleep(6)
            nb = bal_of(a['bsc'])
            sold = max(0, left - int(nb * PCT / 100 if PCT < 100 else nb))
            left = int(nb * PCT / 100 if PCT < 100 else nb)
            if left <= 0: break
            chunk = min(chunk, left)
            continue
        orders.append(info)
        if info.get('kind') == 'size' and chunk > 1:
            chunk = max(1, chunk // 2)   # 单笔太大 -> 降档重试
            continue
        fails += 1
        time.sleep(4)
    if orders and any('orderId' in o or 'quote' in o for o in orders):
        out = orders[-1] if len(orders) == 1 else {'orders': orders, 'got_u_total': round(got_u, 6), 'left': left}
        return a['label'], out
    return a['label'], (orders[-1] if orders else {'error': '未下单'})


with ThreadPoolExecutor(len(acc)) as ex:
    res = dict(ex.map(lambda ab: one(*ab), zip(acc, bals)))
print(json.dumps({'token': T, 'pct': PCT, 'dry': DRY, 'secs': round(time.time() - t0, 1), 'orders': res}, ensure_ascii=False))
if not DRY and PCT >= 100 and all(isinstance(v, (dict, str)) and (v == 'no_balance' or 'orderId' in v or 'orders' in v) for v in res.values()):
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