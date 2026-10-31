import sys as _s; _s.path.append('$AIDBOT_HOME/pylib')  # 沙箱重置会清掉 pip 包，pycryptodome 装在持久目录
# 通用 fair mint 一键买（Jager/AGI 克隆模板：quotaOf/costOf/mint/mintWithBnb），五号并发。只在用户「确认买入 XX」或已授权的自动规则下运行。
# usage: python3 fair_buy.py <mintContract> <token> <每号份数> [--max 每份最高U, 默认1] [--dry] [labels...]
import sys, os, json, subprocess, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rpcpool import batch, rpc
from Crypto.Hash import keccak
from guard import flap_guard, lock_check
_a=sys.argv[1:]; _p=[x for i,x in enumerate(_a) if not x.startswith('--') and (i==0 or _a[i-1]!='--max')]
M=_p[0].lower(); T=_p[1].lower()
U = '0x55d398326f99059ff775485246999027b3197955'; REG = '0x60b9262456de011f7d15a54cdd8c48baaa8b022f'
B = '$AIDBOT_HOME/baw/node_modules/.bin/baw'; SH = int(_p[2])
A = json.load(open('$AIDBOT_HOME/baw/accounts/accounts.json'))
DRY = '--dry' in sys.argv; MAXP = float(sys.argv[sys.argv.index('--max')+1]) if '--max' in sys.argv else 1.0; only = _p[3:]
def sel(s): k = keccak.new(digest_bits=256); k.update(s.encode()); return k.hexdigest()[:8]
def u(x): return hex(int(x))[2:].rjust(64, '0')
def ad(x): return x.lower()[2:].rjust(64, '0')
def call(to, data): return rpc('eth_call', [{'to': to, 'data': data}, 'latest'])
def ci(to, data): return int(call(to, data), 16)
def bal(tok, a): return ci(tok, '0x70a08231' + ad(a))
def handle(a):
    r = call(REG, '0x' + sel('handleOf(address)') + ad(a)); b = bytes.fromhex(r[2:])
    n = int.from_bytes(b[32:64], 'big'); return b[64:64 + n].decode()
def receipt(tx):
    for _ in range(40):
        r = rpc('eth_getTransactionReceipt', [tx])
        if r: return r
        time.sleep(3)
    return None
def baw(a, args):
    env = dict(os.environ, HOME=a['home'], npm_config_cache='/tmp/npmcache')
    r = subprocess.run([B] + args + ['--json'], env=env, capture_output=True, text=True, timeout=150)
    try: return json.loads(r.stdout)
    except Exception: return {'success': False, 'raw': (r.stdout + r.stderr)[:300]}
def clean(d, allow_allow=False):
    sr = d['simulationResult']; rk = d['risks']
    if sr.get('simulationCode') != '000000000': return False, 'sim ' + str(sr.get('simulationCode')) + ' ' + str(sr.get('simulationMsg', ''))[:150]
    if rk.get('riskDetails') or rk.get('riskBehaviors') or any((rk.get('addresses') or {}).values()): return False, 'risk ' + json.dumps(rk)[:200]
    if sr.get('authorityChanges'): return False, 'authority'
    if sr.get('allowanceChanges') and not allow_allow and not all(str(x.get('spender','')).lower()==M and int(x.get('postAmount',1))<=int(x.get('preAmount',0)) for x in sr['allowanceChanges']): return False, 'allowance ' + json.dumps(sr['allowanceChanges'])[:200]
    return True, sr
def go(a, to, data, value='0', allow_allow=False, check=None):
    p = baw(a, ['contract-call', 'preview', '--binanceChainId', '56', '--from', a['bsc'], '--to', to, '--value', value, '--inputData', data])
    if not p.get('success'): return 'preview_fail', str(p)[:300]
    fg = flap_guard(to, data, p['data'].get('simulationResult'))
    if fg: return 'unclean', fg
    ok, sr = clean(p['data'], allow_allow)
    if not ok: return 'unclean', sr
    if check:
        c = check(sr)
        if c is not True: return 'unclean', c
    if DRY: return 'dry_ok', json.dumps(sr.get('balanceChanges'))[:300]
    e = baw(a, ['contract-call', 'execute', '--requestId', p['data']['requestId']])
    tx = (e.get('data') or {}).get('txHash') if isinstance(e, dict) else None
    if not tx: return 'execute_fail', json.dumps(e)[:300]
    r = receipt(tx)
    if not r: return 'unknown', tx
    return ('ok' if r['status'] == '0x1' else 'reverted'), tx
from concurrent.futures import ThreadPoolExecutor
def run(a):
    out = []
    me = a['bsc']; h = handle(me)
    if not h: out.append((a['label'], 'no_agent_id')); return out
    aid = int(keccak.new(digest_bits=256, data=h.encode()).hexdigest(), 16)
    minted = ci(M, '0x' + sel('mintedCount(uint256)') + u(aid)); quota = ci(M, '0x' + sel('quotaOf(uint256)') + u(aid))
    n = min(SH, quota - minted)
    if n <= 0: out.append((a['label'], h, 'quota_full', minted, quota)); return out
    cost = ci(M, '0x' + sel('costOf(uint256)') + u(n))
    if cost > n * MAXP * 10**18: out.append((a['label'], 'price_above_max', cost / 1e18)); return out
    r = call(M, '0x' + sel('quotePurchasePreview(uint256)') + u(cost * 8000 // 10000)); minimum = int(r[66:130], 16)
    until = int(time.time()) + 900
    usdt = bal(U, me); bnb = int(rpc('eth_getBalance', [me, 'latest']), 16)
    t0 = bal(T, me)
    if usdt >= cost:
        alw = ci(U, '0xdd62ed3e' + ad(me) + ad(M))
        if alw < cost:
            if bnb < int(0.0035e18): out.append((a['label'], 'low_bnb', bnb)); return out
            st = go(a, U, '0x095ea7b3' + ad(M) + u(cost), allow_allow=True)
            out.append((a['label'], 'approve', cost / 1e18) + st)
            if st[0] not in ('ok', 'dry_ok'): return out
            if DRY: out.append((a['label'], 'mint_preview_skipped_until_approve')); return out
        def chk(sr):
            bc = sr.get('balanceChanges') or []
            dec = [x for x in bc if x.get('change', 0) < 0 and str(x.get('contractAddress', '')).lower() == U]
            return True if dec and abs(abs(float(dec[0]['change'])) - cost) <= cost*1e-9 and all(float(x.get('change',0))>0 or str(x.get('contractAddress','')).lower() in (U,'0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee') for x in bc) else 'usdt_change_mismatch ' + json.dumps(bc)[:200]
        data = '0x' + sel('mint(uint256,address,uint256,uint256,uint256)') + u(n) + u(0) + u(cost) + u(minimum) + u(until)
        st = go(a, M, data, check=chk)
    else:
        need = ci(M, '0x' + sel('bnbQuote(uint256)') + u(n)); val = need * 1003 // 1000
        if bnb - val < int(0.0035e18): out.append((a['label'], 'insufficient_bnb', bnb / 1e18, val / 1e18)); return out
        data = '0x' + sel('mintWithBnb(uint256,address,uint256,uint256,uint256)') + u(n) + u(0) + u(val) + u(minimum) + u(until)
        st = go(a, M, data, value=str(val))
    t1 = bal(T, me) if st[0] == 'ok' else t0
    if st[0] == 'ok': st = st + (lock_check(rpc, T, me) or 'transferable',)
    out.append((a['label'], h, f'{n}份', round(cost / 1e18, 4), 'U') + st + (f'token +{(t1 - t0) / 1e18:,.0f}',))
    return out
acc=[a for a in A if not only or a['label'] in only]
_t=time.time()
with ThreadPoolExecutor(len(acc)) as ex:
    for res in ex.map(run, acc):
        for o in res: print(o)
print('secs', round(time.time()-_t,1))
