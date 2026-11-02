#!/usr/bin/env python3
"""通用付费份额买入执行器（2026-11-03 03:21 用户「是都要等60%才对 全部改统一规则 适配全部项目」）。
给没有专用执行器的模板用（jagent 系 BabyMint/JAGENTMint/PigMint/FairMint/DaBiaoGeMint、boss 等，以及 newtpl 识别的新模板）。
只由 autofill.py（唯一付费自动买入口，全部闸门在那里查）或用户确认后调用；本脚本自己再查：入口形状、单价、每号剩余额度、余额、preview。

安全 calldata 只在入口形状明确时生成，否则 supported=false 并写原因（autofill 跳过）：
  · 入口 = ABI 里唯一的 mint/buy/purchase/contribute/participate，参数只能是 (uint256 份数) 或 (uint256 份数, address 邀请人)；
  · 付款：payable → BNB；nonpayable 且入口源码里 transferFrom(msg.sender, …) 从 usdt()/USDT 扣款 → USDT（只认 BSC USDT 0x55d3…7955，其他币不做）；
  · 价格：quote(address,uint256) / quote(uint256) / costOf(uint256) / bnbQuote(uint256) / PRICE()×份数；
  · 每号上限：paidRemainingFor(address) / remainingFor(address) / allowanceOf(address) / PAID_PER_WALLET−paidBy / SHARES_PER_WALLET−mintedBy / MAX_PER_AGENT−mintsOf / PER_WALLET−mintedBy；读不出 → 只按 U 预算算份数（合约自己的上限仍会拦）。
每号：份数 = min(合约每号剩余, floor(预算U×0.97/单价U))（预算 = autofill 给的 min(100U 日剩余, 30U 项目剩余)），≥1 才买；
  BNB 盘：余额 − value ≥ 0.0035 BNB（留 0.003 + gas）；USDT 盘：USDT ≥ 成本、BNB ≥ 0.0035；
  USDT 盘只 approve(合约, 精确成本)，mint 成交后读 allowance，>0 就 approve(合约, 0) 归零（同样 preview 干净才执行）；
  每笔 baw preview：simulationCode 000000000、risks 全空、无 authority、allowance 只允许本合约、唯一转出 = 付款币且金额 = 成本（BNB 允许 +0.002 gas），才 execute；
  回执 status=1 → confirm.py 核对代币到账；查不到回执 = unknown，绝不重发。
usage:
  python3 generic_buy.py <合约> --probe                         → JSON：supported/原因、入口、币种、单价、进度、国库、储备、每号上限
  python3 generic_buy.py <合约> --budget '{"主":29.1,...}' [--dry]   → 每号一行 JSON + 最后一行 summary
"""
import sys, os, json, re, time, threading
D = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, D); sys.path.append('$AIDBOT_HOME/pylib')
args = sys.argv[1:]
PROBE = '--probe' in args; DRYF = '--dry' in args
BUDGET = json.loads(args[args.index('--budget') + 1]) if '--budget' in args else {}
M = [x for x in args if x.startswith('0x')][0].lower()
# 复用 fair_buy.py 的工具（sel/u/ad/call/ci/bal/handle/baw/clean/go/A/DRY…），参数位用占位
sys.argv = ['fair_buy.py', M, M, '1'] + (['--dry'] if DRYF else [])
exec(open(os.path.join(D, 'fair_buy.py')).read().split('from concurrent.futures import ThreadPoolExecutor')[0])
USDT = '0x55d398326f99059ff775485246999027b3197955'; NATIVE = '0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee'
MAIN_ADDR = next((a['bsc'] for a in A if a['label'] == '主'), None)
CACHE = os.path.join(D, 'discover_out', 'generic_probe_cache.json')
ENTRY_NAME = re.compile(r'^(mint|buy|purchase|contribute|participate|mintShares|buyShares|mintPaid)$', re.I)


def tryc(data):
    try:
        r = call(M, data); return r if isinstance(r, str) and len(r) >= 66 else None
    except Exception: return None


def tint(sig, arg=''):
    r = tryc('0x' + sel(sig) + arg); return int(r[:66], 16) if r else None


def bnb_u():
    R = '0x10ed43c718714eb63d5aa57b78b54704e256024e'; WB = '0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c'
    r = rpc('eth_call', [{'to': R, 'data': '0xd06ca61f' + u(10 ** 18) + u(0x40) + u(2) + ad(WB) + ad(USDT)}, 'latest'])
    return int(r[-64:], 16) / 1e18


def static_probe():
    """源码/ABI 分析（缓存 6 小时）：入口、币种、函数清单、newtpl 结论。"""
    try: cache = json.load(open(CACHE))
    except Exception: cache = {}
    c = cache.get(M)
    if c and time.time() - c.get('t', 0) < 6 * 3600: return c
    import newtpl as NT
    os.chdir(D)
    v = NT.verify(M)
    if v.get('verified') is None: return {'supported': None, 'why': '源码验证查询失败（unknown）'}
    if v.get('verified') is not True: return {'supported': False, 'why': '源码未验证'}
    srcs, abi, cname = NT.fetch_source(v.get('proxy_impl') or (v['source'].split(':', 1)[1] if str(v.get('source', '')).startswith('bytecode-match:') else M))
    if not abi: return {'supported': None, 'why': '取不到 ABI/源码（unknown）'}
    try: an = NT.analyze(M, v)
    except Exception as ex: an = {'status': 'analyze_err', 'why': str(ex)[:120]}
    fn = {f['name'] + '(' + ','.join(i['type'] for i in f.get('inputs', [])) + ')': f for f in abi if f.get('type') == 'function'}
    entries = [(k, f) for k, f in fn.items() if ENTRY_NAME.match(f['name']) and f.get('stateMutability') in ('payable', 'nonpayable')]
    out = {'t': time.time(), 'name': (an.get('name') if isinstance(an, dict) else None) or cname, 'functions': sorted(fn),
           'newtpl': {k: an.get(k) for k in ('status', 'category', 'conclusion', 'risk_flags', 'safe_flags', 'why')} if isinstance(an, dict) else None,
           'treasury_bps': ((an.get('params') or {}).get('treasury_bps') if isinstance(an, dict) else None),
           'treasury_src': ((an.get('params') or {}).get('treasury_src') if isinstance(an, dict) else None),
           'progress_fns': ((an.get('params') or {}).get('progress') if isinstance(an, dict) else None),
           'verify_source': v.get('source')}
    if len(entries) != 1:
        out.update(supported=False, why=f'买入入口不唯一或没有（{[k for k, _ in entries]}），不生成 calldata')
    else:
        sig, f = entries[0]; ins = [i['type'] for i in f.get('inputs', [])]
        names = [i.get('name', '') for i in f.get('inputs', [])]
        if ins == ['uint256']: shape = 'qty'
        elif ins == ['uint256', 'address'] and re.search(r'ref|invit|sponsor|upline', names[1], re.I): shape = 'qty_ref'
        else: shape = None
        if not shape:
            out.update(supported=False, why=f'入口 {sig} 参数形状不认识（只支持 (uint256 份数) 或 (uint256, address 邀请人)），不生成 calldata')
        else:
            cur = None
            if f.get('stateMutability') == 'payable': cur = 'BNB'
            else:
                code = '\n'.join(srcs.values()) if srcs else ''
                body = NT.body_with_helpers(NT.parse_functions(NT.strip_comments(code)), f['name'])
                ua = ('0x' + tryc('0x' + sel('usdt()'))[26:66]).lower() if tryc('0x' + sel('usdt()')) else None
                if re.search(r'transferFrom\(\s*msg\.sender', body, re.I) and ua == USDT: cur = 'USDT'
                elif re.search(r'transferFrom\(\s*msg\.sender', body, re.I): out['why_cur'] = f'付款币 {ua} 不是 BSC USDT'
            if not cur: out.update(supported=False, why=out.get('why_cur') or f'入口 {sig} 既不是 payable 也看不出从 USDT 扣款，不生成 calldata')
            else: out.update(supported=True, entry=sig, shape=shape, currency=cur)
    cache[M] = out
    try: json.dump(cache, open(CACHE + '.tmp', 'w'), ensure_ascii=False); os.replace(CACHE + '.tmp', CACHE)
    except Exception: pass
    return out


def has(fs, sig): return sig in fs


def price_of(fs, n, me):
    """n 份的成本（wei/最小单位）。"""
    if has(fs, 'quote(address,uint256)'): return tint('quote(address,uint256)', ad(me) + u(n))
    if has(fs, 'quote(uint256)'): return tint('quote(uint256)', u(n))
    if has(fs, 'costOf(uint256)'): return tint('costOf(uint256)', u(n))
    if has(fs, 'bnbQuote(uint256)'): return tint('bnbQuote(uint256)', u(n))
    for p in ('PRICE()', 'price()', 'MINT_PRICE()', 'mintPrice()', 'pricePerShare()'):
        if has(fs, p):
            x = tint(p); return x * n if x is not None else None
    return None


def remaining_of(fs, me):
    """(每号剩余份数 或 None, 来源)。"""
    for s in ('paidRemainingFor(address)', 'remainingFor(address)', 'allowanceOf(address)'):
        if has(fs, s):
            x = tint(s, ad(me))
            if x is not None: return x, s
    for cap, used in (('PAID_PER_WALLET()', 'paidBy(address)'), ('SHARES_PER_WALLET()', 'mintedBy(address)'), ('MAX_PER_AGENT()', 'mintsOf(address)'),
                      ('PER_WALLET()', 'mintedBy(address)'), ('MAX_PER_WALLET()', 'mintedBy(address)')):
        if has(fs, cap) and has(fs, used):
            c, x = tint(cap), tint(used, ad(me))
            if c is not None and x is not None: return max(0, c - x), f'{cap}-{used}'
    return None, None


def live(sp):
    """进度、开池、国库、储备（每次实时读）。"""
    fs = set(sp.get('functions') or []); o = {}
    pg = sp.get('progress_fns') or {}
    if pg.get('sold_fn') and pg.get('cap_fn'):
        s_, c_ = tint(pg['sold_fn']), tint(pg['cap_fn'])
        if s_ is not None and c_: o.update(sold=s_, cap=c_, open_pct=round(min(s_ * 100 / c_, 999), 2))
    for f in ('poolOpen()', 'opened()', 'finalized()', 'liquidityTargetReached()', 'soldOut()'):
        if has(fs, f):
            x = tint(f)
            if x is not None: o['opened'] = bool(x); o['open_fn'] = f; break
    if has(fs, 'mintingOpen()'):
        x = tint('mintingOpen()'); o['minting_open'] = None if x is None else bool(x)
    tb = sp.get('treasury_bps')
    if tb is None and has(fs, 'TREASURY_BPS()'): tb = tint('TREASURY_BPS()')
    o['treasury_bps'] = tb
    # 储备：合约里实际留着的付款币 / 已募资（paidShares×PRICE 或 totalRaised）
    cur = sp.get('currency'); p1 = tint('PRICE()') if has(fs, 'PRICE()') else None
    raised = None
    if has(fs, 'totalRaised()'): raised = tint('totalRaised()')
    elif has(fs, 'paidShares()') and p1: ps = tint('paidShares()'); raised = ps * p1 if ps is not None else None
    held = None
    try: held = bal(USDT, M) if cur == 'USDT' else int(rpc('eth_getBalance', [M, 'latest']), 16) if cur == 'BNB' else None
    except Exception: held = None
    o['raised'] = raised / 1e18 if raised is not None else None; o['held'] = held / 1e18 if held is not None else None
    o['reserve_ratio'] = round(held / raised, 4) if raised and held is not None else (1.0 if raised == 0 and held is not None else None)
    o['has_refund'] = any(re.match(r'(refund|claimRefund|withdrawRefund)\(', f) for f in fs)
    return o


def probe():
    sp = static_probe()
    out = {k: v for k, v in sp.items() if k not in ('functions', 't')}
    if sp.get('supported'): out.update(live(sp))
    return out


if PROBE:
    try: print(json.dumps(probe(), ensure_ascii=False))
    except Exception as ex: print(json.dumps({'supported': None, 'why': f'probe 异常 {str(ex)[:200]}'}, ensure_ascii=False))
    sys.exit()

import confirm as CF
SP = static_probe()
if not SP.get('supported'):
    print(json.dumps({'summary': True, 'supported': SP.get('supported'), 'why': SP.get('why')}, ensure_ascii=False)); sys.exit()
FS = set(SP['functions']); CUR = SP['currency']; ENTRY = SP['entry']; SHAPE = SP['shape']
BU = bnb_u()
TOKEN = None
r_ = tryc('0x' + sel('token()'))
if r_ and int(r_, 16): TOKEN = ('0x' + r_[26:66]).lower()
res, lk = [], threading.Lock()


def acct_lock(label):
    import fcntl
    f = open(f'/tmp/acct_{label}.lock', 'w'); fcntl.flock(f, fcntl.LOCK_EX); return f


def run(a):
    lab = a['label']; me = a['bsc']; o = {'label': lab}
    bud = float(BUDGET.get(lab, 0))
    if bud <= 0: o['state'] = 'no_budget'; return o
    L_ = acct_lock(lab)
    try:
        rem, rsrc = remaining_of(FS, me); o['remaining_src'] = rsrc
        one = price_of(FS, 1, me)
        if not one: o['state'] = 'price_unknown'; return o
        unit_u = one / 1e18 * (BU if CUR == 'BNB' else 1.0)
        n = int(bud * 0.97 // unit_u) if unit_u > 0 else 0
        if rem is not None: n = min(n, rem)
        o['wallet_remaining'] = rem
        if n < 1: o['state'] = 'quota_full' if rem == 0 else 'budget_lt_1share'; o['unit_u'] = round(unit_u, 4); return o
        cost = price_of(FS, n, me)
        while n > 0 and cost is not None and cost / 1e18 * (BU if CUR == 'BNB' else 1.0) > bud * 0.97:  # 报价含阶梯/免费份时再收紧
            n -= 1; cost = price_of(FS, n, me) if n else None
        if not n or cost is None: o['state'] = 'budget_lt_1share'; return o
        cost_u = cost / 1e18 * (BU if CUR == 'BNB' else 1.0)
        o.update(shares=n, cost=cost / 1e18, currency=CUR, cost_u=round(cost_u, 4))
        bnb = int(rpc('eth_getBalance', [me, 'latest']), 16)
        args_ = u(n) if SHAPE == 'qty' else u(n) + (ad(MAIN_ADDR) if MAIN_ADDR and me.lower() != MAIN_ADDR.lower() else u(0))
        data = '0x' + sel(ENTRY) + args_
        t0 = bal(TOKEN, me) if TOKEN else None
        if CUR == 'BNB':
            if bnb - cost < int(0.0035e18): o['state'] = 'insufficient_bnb_keep_0.003'; o['bnb'] = bnb / 1e18; return o
            def chk(sr):
                bc = sr.get('balanceChanges') or []; neg = [x for x in bc if float(x.get('change', 0)) < 0]
                if len(neg) != 1 or str(neg[0].get('contractAddress', '')).lower() != NATIVE: return 'unexpected_outflow ' + json.dumps(bc)[:240]
                out_ = abs(float(neg[0]['change']))
                if out_ < cost * 0.999 or out_ > cost + 0.002e18: return 'bnb_out_mismatch ' + json.dumps(bc)[:240]
                return True
            st = go(a, M, data, value=str(cost), check=chk)
        else:
            usd = bal(USDT, me)
            if usd < cost: o['state'] = 'insufficient_usdt'; o['usdt'] = usd / 1e18; return o
            if bnb < int(0.0035e18): o['state'] = 'low_bnb_keep_0.003'; o['bnb'] = bnb / 1e18; return o
            alw = ci(USDT, '0xdd62ed3e' + ad(me) + ad(M))
            if alw < cost:
                def achk(sr):
                    ac = sr.get('allowanceChanges') or []
                    if not ac: return True
                    if all(str(x.get('spender', '')).lower() == M and int(x.get('postAmount', -1)) == cost for x in ac): return True
                    return 'approve_not_exact ' + json.dumps(ac)[:200]
                sa = go(a, USDT, '0x095ea7b3' + ad(M) + u(cost), allow_allow=True, check=achk)
                o['approve'] = sa[0]
                if sa[0] == 'dry_ok': o['state'] = 'dry_ok_approve_only'; o['detail'] = 'USDT 授权预演干净；mint 预演需先授权，dry 不执行授权'; return o
                if sa[0] != 'ok': o['state'] = 'approve_' + sa[0]; o['detail'] = str(sa[1])[:200]; return o
            def chk(sr):
                bc = sr.get('balanceChanges') or []; neg = [x for x in bc if float(x.get('change', 0)) < 0]
                if len(neg) != 1 or str(neg[0].get('contractAddress', '')).lower() != USDT: return 'unexpected_outflow ' + json.dumps(bc)[:240]
                if abs(abs(float(neg[0]['change'])) - cost) > max(1, cost * 1e-9): return 'usdt_out_mismatch ' + json.dumps(bc)[:240]
                return True
            st = go(a, M, data, check=chk)
        o['state'] = st[0]; o['detail'] = str(st[1])[:240]
        if st[0] in ('ok', 'reverted', 'unknown'): o['tx'] = st[1]; o['tx_url'] = f'https://bscscan.com/tx/{st[1]}'
        if st[0] == 'ok' and TOKEN:
            o['confirm'] = CF.confirm_tx(st[1], TOKEN, me, t0, timeout=30)
            t1 = bal(TOKEN, me); o['token_delta'] = (t1 - t0) / 1e18 if t0 is not None else None
        if CUR == 'USDT' and st[0] in ('ok', 'reverted', 'preview_fail', 'unclean', 'execute_fail') and not DRY:
            left = ci(USDT, '0xdd62ed3e' + ad(me) + ad(M))
            if left > 0:
                z = go(a, USDT, '0x095ea7b3' + ad(M) + u(0), allow_allow=True)
                o['approve_reset'] = z[0]
            else: o['approve_reset'] = 'already_0'
        return o
    except Exception as ex:
        o['state'] = 'error'; o['detail'] = str(ex)[:200]; return o
    finally:
        L_.close()


acc = [a for a in A if a['label'] in BUDGET]
ths = []
def _w(a):
    r = run(a)
    with lk: res.append(r)
    print(json.dumps(r, ensure_ascii=False), flush=True)
for i, a in enumerate(acc):
    th = threading.Thread(target=_w, args=(a,)); th.start(); ths.append(th); time.sleep(0.2)
for th in ths: th.join()
print(json.dumps({'summary': True, 'supported': True, 'entry': ENTRY, 'currency': CUR, 'bnb_u': round(BU, 1), 'token': TOKEN, 'results': res}, ensure_ascii=False))
