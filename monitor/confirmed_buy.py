#!/usr/bin/env python3
"""用户「确认买入 XX 金额 + 账号」后的通用并发下单（2026-11-01 20:47 用户采纳第 8 项）。
只在用户明确确认买入（金额、项目、账号都由用户给出）后运行；不改金额、不加备选、不重发。

流程（每个账号一个线程，同时进行；同一账号用 /tmp/acct_<label>.lock 串行，避免 already in progress）：
  1. 规则 B：目标合约（代理含实现）verify_source.py 必须 verified=true；用户当场破例才可加 --unverified-ok "<用户原话>"。
  2. 余额：BNB −（value + gas 预留 0.0005）≥ 0.003（规则 G，按 0.0035 留余量）；USDT 付款的 USDT 余额 ≥ 金额。不够 → 该号跳过（insufficient_*），不换币。
  3. （USDT 付款）精确授权：allowance < 金额时 approve(spender=合约, 金额) → preview 干净才 execute → 按 tx 核对 status=1。
  4. 买入（balanceChanges 的 change 是最小单位 wei）：baw contract-call preview；simulationCode 000000000、risks 全空、无 authorityChanges、allowance 只允许被本合约消耗、
     BNB 转出 ≤ value + 0.002（gas）、USDT 转出 ≤ 金额，才 execute。Flap 合约一律拦截（guard.flap_guard）。
  5. 核对：eth_getTransactionReceipt status=0x1 才算成交；查不到回执 = unknown（不确定，绝不重发，只按 txHash 再核对）。
     成交后读代币余额增量；USDT 付款的残余授权 >0 时 approve(合约, 0) 归零（同样 preview 干净才执行）。
用法：
  python3 confirmed_buy.py --to <合约> --data <calldata> [--value-bnb 0.01 | --usdt 5] [--token <代币>] [--dry] [--unverified-ok "原话"] 账号label...
  calldata 里可用占位符：{ME}=本号地址（32 字节补零），{REF}=邀请人（主账号地址，主账号自己填 0）
  --dry：每号只做 preview（USDT 付款且需要授权时只预演授权），绝不 execute。
输出：每号一行 JSON（label、state、tx、tx_url、cost_bnb/cost_u、token_delta、detail），最后一行 summary。
"""
import sys, os, json, subprocess, time, fcntl
D = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, D); sys.path.append('$AIDBOT_HOME/pylib')
from concurrent.futures import ThreadPoolExecutor
from rpcpool import rpc
from guard import flap_guard

argv = sys.argv[1:]
def opt(k, d=None):
    return argv[argv.index(k) + 1] if k in argv else d
FLAGS_WITH_ARG = ('--to', '--data', '--value-bnb', '--usdt', '--token', '--unverified-ok')
labels = [x for i, x in enumerate(argv) if not x.startswith('--') and (i == 0 or argv[i - 1] not in FLAGS_WITH_ARG)]
TO = (opt('--to') or '').lower(); DATA = opt('--data') or ''; TOKEN = (opt('--token') or '').lower() or None
VAL = int(round(float(opt('--value-bnb', '0')) * 1e18)); USD = float(opt('--usdt', '0')); DRY = '--dry' in argv; UNV = opt('--unverified-ok')
if not TO.startswith('0x') or not DATA.startswith('0x') or not labels or (VAL and USD):
    sys.exit(__doc__)
USDT = '0x55d398326f99059ff775485246999027b3197955'; ETH = '0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee'
MAIN = '<MAIN_ADDR>'; B = '$AIDBOT_HOME/baw/node_modules/.bin/baw'
USD_W = int(round(USD * 1e18)); FLOOR = int(0.0035e18); GAS = int(0.0005e18)
A = [a for a in json.load(open('$AIDBOT_HOME/baw/accounts/accounts.json')) if a['label'] in labels]
missing = [l for l in labels if l not in [a['label'] for a in A]]


def ad(x): return x.lower()[2:].rjust(64, '0')
def u(x): return hex(int(x))[2:].rjust(64, '0')
def bal(tok, a): return int(rpc('eth_call', [{'to': tok, 'data': '0x70a08231' + ad(a)}, 'latest']), 16)
def allowance(o, s): return int(rpc('eth_call', [{'to': USDT, 'data': '0xdd62ed3e' + ad(o) + ad(s)}, 'latest']), 16)


def baw(a, args, t=150):
    env = dict(os.environ, HOME=a['home'], npm_config_cache='/tmp/npmcache')
    r = subprocess.run([B] + args + ['--json'], env=env, capture_output=True, text=True, timeout=t)
    try: return json.loads(r.stdout)
    except Exception: return {'success': False, 'raw': (r.stdout + r.stderr)[:300]}


def receipt(tx):
    for _ in range(40):
        try:
            r = rpc('eth_getTransactionReceipt', [tx])
            if r: return r
        except Exception: pass
        time.sleep(3)
    return None


def clean(sr, rk, to, max_bnb_out, max_usdt_out, approve_ok=False):
    if sr.get('simulationCode') != '000000000': return 'sim ' + str(sr.get('simulationCode')) + ' ' + str(sr.get('simulationMsg', ''))[:150]
    if rk.get('riskDetails') or rk.get('riskBehaviors') or any((rk.get('addresses') or {}).values()): return 'risk ' + json.dumps(rk, ensure_ascii=False)[:200]
    if sr.get('authorityChanges'): return 'authority ' + json.dumps(sr['authorityChanges'])[:200]
    for x in sr.get('allowanceChanges') or []:
        sp = str(x.get('spender', '')).lower(); post = int(x.get('postAmount', 0) or 0); pre = int(x.get('preAmount', 0) or 0)
        if sp != TO: return 'allowance_other_spender ' + json.dumps(x)[:200]
        if not approve_ok and post > pre: return 'allowance_increase ' + json.dumps(x)[:200]
        if approve_ok and post > USD_W: return 'allowance_above_amount ' + json.dumps(x)[:200]
    for x in sr.get('balanceChanges') or []:
        ch = float(x.get('change', 0) or 0); ca = str(x.get('contractAddress', '') or ETH).lower()
        if ch >= 0: continue
        if ca in (ETH, '', 'native', '0x0000000000000000000000000000000000000000'):
            if abs(ch) > max_bnb_out + int(0.002e18): return f'bnb_out_too_high {abs(ch)}'
        elif ca == USDT:
            if abs(ch) > max_usdt_out * (1 + 1e-9): return f'usdt_out_too_high {abs(ch)}'
        else: return 'other_token_out ' + json.dumps(x)[:200]
    return None


def send(a, to, data, value, max_bnb_out, max_usdt_out, approve_ok=False):
    p = baw(a, ['contract-call', 'preview', '--binanceChainId', '56', '--from', a['bsc'], '--to', to, '--value', str(value), '--inputData', data])
    if not p.get('success'): return {'state': 'preview_fail', 'detail': str(p)[:300]}
    d = p['data']; sr = d.get('simulationResult') or {}
    fg = flap_guard(to, data, sr)
    if fg: return {'state': 'flap_block', 'detail': str(fg)[:200]}
    bad = clean(sr, d.get('risks') or {}, to, max_bnb_out, max_usdt_out, approve_ok)
    if bad: return {'state': 'unclean', 'detail': bad}
    if DRY: return {'state': 'dry_ok', 'detail': json.dumps(sr.get('balanceChanges'), ensure_ascii=False)[:300]}
    e = baw(a, ['contract-call', 'execute', '--requestId', d['requestId']])
    tx = (e.get('data') or {}).get('txHash') if isinstance(e, dict) else None
    if not tx: return {'state': 'execute_fail', 'detail': json.dumps(e, ensure_ascii=False)[:300]}
    r = receipt(tx)
    if not r: return {'state': 'unknown', 'tx': tx, 'detail': '未拿到回执，结果不确定，绝不重发，只按 txHash 核对'}
    return {'state': 'ok' if r.get('status') == '0x1' else 'reverted', 'tx': tx}


def run(a):
    me = a['bsc']; out = {'label': a['label'], 'address': me}
    lk = open(f"/tmp/acct_{a['label']}.lock", 'w'); fcntl.flock(lk, fcntl.LOCK_EX)
    try:
        data = DATA.replace('{ME}', ad(me)).replace('{REF}', ad('0x' + '0' * 40) if me.lower() == MAIN.lower() else ad(MAIN))
        bnb = int(rpc('eth_getBalance', [me, 'latest']), 16)
        if bnb - VAL - GAS < FLOOR: return dict(out, state='insufficient_bnb', detail=f'BNB {bnb/1e18:.5f}，付款 {VAL/1e18:.5f} + gas 后低于 0.003')
        t0 = bal(TOKEN, me) if TOKEN else None
        if USD_W:
            ub = bal(USDT, me)
            if ub < USD_W: return dict(out, state='insufficient_usdt', detail=f'USDT {ub/1e18:.2f} < {USD:.2f}')
            if allowance(me, TO) < USD_W:
                ap = send(a, USDT, '0x095ea7b3' + ad(TO) + u(USD_W), 0, 0, 0, approve_ok=True)
                out['approve'] = ap
                if ap['state'] not in ('ok', 'dry_ok'): return dict(out, state='approve_' + ap['state'], detail=ap.get('detail'))
                if DRY: return dict(out, state='dry_ok_approve_only', detail='需先精确授权；授权预演通过，买入预演要等授权上链后才能做')
        r = send(a, TO, data, VAL, VAL, USD_W); out.update(r)
        if r.get('tx'): out['tx_url'] = 'https://bscscan.com/tx/' + r['tx']
        if r['state'] == 'ok':
            out['cost_bnb'] = VAL / 1e18 if VAL else None; out['cost_u'] = USD if USD_W else None
            if TOKEN:
                try: out['token_delta'] = (bal(TOKEN, me) - t0) / 1e18
                except Exception: out['token_delta'] = None
            if USD_W and allowance(me, TO) > 0:
                out['reset_allowance'] = send(a, USDT, '0x095ea7b3' + ad(TO) + u(0), 0, 0, 0, approve_ok=True)
        return out
    except Exception as e:
        return dict(out, state='error', detail=str(e)[:200])
    finally:
        lk.close()


if __name__ == '__main__':
    if not UNV:
        try: v = json.loads(subprocess.run(['python3', f'{D}/verify_source.py', TO], capture_output=True, text=True, timeout=90).stdout.splitlines()[0])
        except Exception as e: v = {'verified': None, 'source': f'查询失败 {e}'}
        if v.get('verified') is not True:
            print(json.dumps({'state': 'source_unverified', 'contract': TO, 'source': v.get('source')}, ensure_ascii=False)); sys.exit(3)
    t = time.time(); res = []
    with ThreadPoolExecutor(max(1, len(A))) as ex:
        for r in ex.map(run, A): res.append(r); print(json.dumps(r, ensure_ascii=False))
    print(json.dumps({'summary': True, 'dry': DRY, 'accounts': len(A), 'missing_labels': missing, 'ok': sum(r['state'] == 'ok' for r in res),
                      'states': {r['label']: r['state'] for r in res}, 'unverified_ok': UNV, 'secs': round(time.time() - t, 1)}, ensure_ascii=False))
