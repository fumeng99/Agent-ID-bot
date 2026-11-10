#!/usr/bin/env python3
"""BSC Mint 预检（只读：不签名、不授权、不付款）。输入网址或合约地址（CA），输出 Mint 合约、函数、价格、付款币、
钱包资格、剩余额度、开放状态、风险点和六个号的 eth_call 模拟结果，并写入历史经验库。

用法：
  python3 mintcheck.py <网址|CA> [更多CA...] [--json] [--wallets 主,001] [--no-lib] [--qty N] [--preview]
  --preview：对最佳模拟结果再跑一次 baw contract-call preview（币安钱包官方模拟+风险，只预演，绝不 execute）
输出：默认中文摘要；--json 输出完整 JSON。报告同时存到 library/reports/。
判断（verdict）：
  free_ready     免费（value=0、无付款币扣款）且至少一个号模拟通过 → 可按免费规则预演后执行
  paid_confirm   要付钱（BNB 或代币），模拟/读数完成 → 交用户确认，绝不自动付款
  not_open       未开放/已暂停/已结束/售罄
  blocked        高危（未验证、可升级、项目方可提款等）或资格不符
  needs_ai       陌生合约：库里没有匹配、函数无法识别或参数需要链下数据 → 附摘要交 AI 分析
"""
import json, os, re, sys, time, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor
D = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, D)
import chain as C, mintlib as L

T0 = time.time(); TIMES = {}
def lap(k, t): TIMES[k] = round(TIMES.get(k, 0) + time.time() - t, 2)

ACC_FILE = '$AIDBOT_HOME/baw/accounts/accounts.json'
SKIP_ADDR = {C.USDT, C.WBNB, C.REGISTRY, C.FACTORY, '0x10ed43c718714eb63d5aa57b78b54704e256024e', '0x000000000000000000000000000000000000dead',
             '0x0000000000000000000000000000000000000000', '0x13f4ea83d0bd40e75c8222255bc855a974568dd4'}
MINT_RE = re.compile(r'^(mint|publicmint|freemint|claim|claimfree|claimgift|claimreward|claimsponsorreward|buy|buyshares|purchase|register|participate|join|'
                     r'deposit|stake|presale|whitelistmint|allowlistmint|safemint|mintpublic|minttrial|commit|enter)', re.I)
ADMIN_RE = re.compile(r'^(set|withdraw|rescue|sweep|emergency|upgrade|pause|unpause|transferownership|renounce|grant|revoke|ownermint|devmint|airdrop|'
                      r'adminmint|teammint|reserve|recover|skim|migrate|kill|destroy|blacklist|exclude|enabletrading|settle|finalize|init)', re.I)
DANGER_RE = re.compile(r'^(withdraw|rescue|sweep|emergencywithdraw|recover|migrate|setprice|setmintprice|setfee|settax|settreasury|upgradeto|'
                       r'ownermint|devmint|adminmint|blacklist|setblacklist|kill|destroy)', re.I)
PRICE_N = re.compile(r'(^|_)(price|mintprice|cost|unitprice|pricepershare|currentprice|getprice|getcurrentprice|fee|mintfee|ticketprice|shareprice|'
                     r'price_wei|mint_price|PRICE|MINT_PRICE|SHARE_PRICE|ROUND_PRICE|deposit|DEPOSIT)', re.I)
SUPPLY_N = re.compile(r'(maxsupply|max_supply|totalsupply|totalminted|minted|sold|soldshares|successfulclaims|round_shares|total_shares|cap$|opening_ticket|'
                      r'open_at|openat|maxmint|max_mints|maxshares|shares|remaining|available|tickets|maxtickets|counter|nexttokenid|_nextid|claimed|filled|sharessold)', re.I)
WALLET_N = re.compile(r'(perwallet|per_wallet|peraddress|per_address|perid|per_id|walletlimit|maxper|limit|quota)', re.I)
STATE_N = re.compile(r'(paused|open|active|started|start|end|deadline|live|enabled|launched|finalized|filled|failed|phase|stage|round|status|closed)', re.I)


def wallets(only=None):
    try: acc = json.load(open(ACC_FILE))
    except Exception: return []
    out = [{'label': a['label'], 'address': a['bsc'].lower()} for a in acc if a.get('bsc')]
    return [w for w in out if not only or w['label'] in only]


# ---------- ① 输入解析：网址 → 合约地址 ----------
def fetch(u, t=12, cap=1_500_000):
    r = urllib.request.urlopen(urllib.request.Request(u, headers={'user-agent': 'Mozilla/5.0'}), timeout=t)
    return r.read(cap).decode('utf8', 'ignore')


def from_url(url):
    t = time.time(); base = '{0.scheme}://{0.netloc}'.format(urllib.parse.urlparse(url if '://' in url else 'https://' + url))
    texts = {}; jobs = [url if '://' in url else 'https://' + url] + [base + p for p in ('/skill.md', '/llms.txt', '/agent.md', '/agents.md', '/.well-known/agent.json', '/api/state', '/api/stats', '/api/?action=status', '/api/status', '/api/config', '/config.json')]
    def get(u):
        try: return u, fetch(u)
        except Exception: return u, ''
    with ThreadPoolExecutor(8) as ex:
        for u, x in ex.map(get, jobs):
            if x and not (u != jobs[0] and x.lstrip().lower().startswith(('<!doctype', '<html'))): texts[u] = x
    html = texts.get(jobs[0], '')
    js = []
    for s in re.findall(r'<script[^>]+src=["\']([^"\']+)', html)[:8]:
        js.append(urllib.parse.urljoin(jobs[0], s))
    with ThreadPoolExecutor(6) as ex:
        for u, x in ex.map(get, js): texts[u] = x
    cnt = {}; where = {}
    for u, x in texts.items():
        for a in re.findall(r'0x[0-9a-fA-F]{40}(?![0-9a-fA-F])', x):
            a = a.lower(); cnt[a] = cnt.get(a, 0) + 1; where.setdefault(a, set()).add(u.replace(base, '') or '/')
    guide = {u.replace(base, ''): x[:2500] for u, x in texts.items() if u != jobs[0] and u not in js and x}
    title = (re.search(r'<title>([^<]{0,120})', html) or [None, ''])[1].strip()
    lap('1_url_fetch', t)
    cands = sorted([a for a in cnt if a not in SKIP_ADDR], key=lambda a: -cnt[a])
    return {'base': base, 'title': title, 'pages_read': len(texts), 'addresses': cands[:12],
            'seen': {a: {'count': cnt[a], 'in': sorted(where[a])[:4]} for a in cands[:12]}, 'guides': guide}


# ---------- ② 读接口：ABI（已验证）或选择器反查（未验证） ----------
def interface(info):
    t = time.time(); tgt = info.get('impl') or info['address']
    v = C.sourcify(tgt); vp = C.sourcify(info['address']) if info.get('impl') else v
    fns = []
    if v.get('verified') and v.get('abi'):
        for f in v['abi']:
            if f.get('type') != 'function': continue
            sig = C.abi_sig(f)
            fns.append({'sig': sig, 'name': f['name'], 'selector': C.sel(sig), 'inputs': f.get('inputs', []), 'outputs': f.get('outputs', []),
                        'mutability': f.get('stateMutability', 'nonpayable'), 'src': 'abi'})
    else:
        names = C.lookup_sigs(info['selectors'][:200])
        for s, n in names.items():
            if not n: continue
            nm = n.split('(')[0]; args = n[len(nm) + 1:-1]
            if nm[:1].isupper() and not nm.isupper() and '_' not in nm: continue  # PascalCase 多半是自定义错误/事件，不是函数
            fns.append({'sig': n, 'name': nm, 'selector': s, 'inputs': [{'type': x} for x in args.split(',') if x], 'outputs': [],
                        'mutability': 'unknown', 'src': 'openchain'})
    lap('2_interface', t)
    return {'verified': bool(v.get('verified')), 'proxy_verified': bool(vp.get('verified')), 'contract_name': v.get('name', ''),
            'functions': fns, 'unknown_selectors': len([s for s in info['selectors'] if s not in {f['selector'] for f in fns}])}


# ---------- ③ 状态读取：所有零参 view、按地址的 view（六个号） ----------
def read_state(addr, fns, ws):
    t = time.time()
    views0 = [f for f in fns if not f['inputs'] and f['mutability'] in ('view', 'pure', 'unknown') and not MINT_RE.match(f['name']) and not ADMIN_RE.match(f['name'])][:120]
    addr1 = [f for f in fns if len(f['inputs']) == 1 and f['inputs'][0]['type'] == 'address' and f['mutability'] in ('view', 'unknown')
             and re.search(r'(minted|claimed|balance|count|shares|of|used|quota|remaining|eligible|bought|purchased|deposit|user|info|can)', f['name'], re.I)][:12]
    uint1 = [f for f in fns if len(f['inputs']) == 1 and f['inputs'][0]['type'].startswith('uint') and f['mutability'] in ('view', 'unknown')
             and re.search(r'(quote|price|cost)', f['name'], re.I)][:4]
    calls = [('eth_call', [{'to': addr, 'data': f['selector']}, 'latest']) for f in views0]
    calls += [('eth_call', [{'to': addr, 'data': f['selector'] + C.enc_addr(w['address'])}, 'latest']) for f in addr1 for w in ws]
    calls += [('eth_call', [{'to': addr, 'data': f['selector'] + C.enc_uint(1)}, 'latest']) for f in uint1]
    calls += [('eth_call', [{'to': C.REGISTRY, 'data': '0x70a08231' + C.enc_addr(w['address'])}, 'latest']) for w in ws]
    calls += [('eth_getBalance', [w['address'], 'latest']) for w in ws]
    calls += [('eth_call', [{'to': C.USDT, 'data': '0x70a08231' + C.enc_addr(w['address'])}, 'latest']) for w in ws]
    calls += [('eth_getBalance', [addr, 'latest']), ('eth_call', [{'to': C.USDT, 'data': '0x70a08231' + C.enc_addr(addr)}, 'latest']), ('eth_blockNumber', [])]
    r = C.batch(calls); i = 0
    def val(f, x):
        if not isinstance(x, str) or x == '0x': return None
        outs = [o['type'] for o in f.get('outputs', [])]
        if outs == ['string'] or (not outs and len(x) > 130 and f['name'] in ('name', 'symbol', 'version', 'baseURI', 'tokenURI')): return C.dec_str(x)
        if outs == ['address'] or (not outs and len(x) == 66 and x[2:26] == '0' * 24 and int(x, 16) > 2 ** 100): return C.dec_addr(x)
        if outs == ['bool']: return bool(C.dec_uint(x))
        if len(x) == 66 or (outs and outs[0].startswith(('uint', 'int'))): return C.dec_uint(x)
        return x[:130]
    st = {}
    for f in views0: st[f['sig']] = val(f, r[i]); i += 1
    per = {w['label']: {} for w in ws}
    for f in addr1:
        for w in ws: per[w['label']][f['sig']] = val(f, r[i]); i += 1
    for f in uint1: st[f['sig'] + '[1]'] = val(f, r[i]); i += 1
    for w in ws: per[w['label']]['agent_id'] = (C.dec_uint(r[i]) or 0) > 0 if isinstance(r[i], str) else None; i += 1
    for w in ws: per[w['label']]['bnb'] = int(r[i], 16) / 1e18 if isinstance(r[i], str) else None; i += 1
    for w in ws: per[w['label']]['usdt'] = (C.dec_uint(r[i]) or 0) / 1e18 if isinstance(r[i], str) else None; i += 1
    cbnb = int(r[i], 16) / 1e18 if isinstance(r[i], str) else None; cusdt = (C.dec_uint(r[i + 1]) or 0) / 1e18 if isinstance(r[i + 1], str) else None
    head = int(r[i + 2], 16) if isinstance(r[i + 2], str) else None
    lap('3_state_reads', t)
    return {'views': {k: v for k, v in st.items() if v not in (None, '')}, 'per_wallet': per, 'contract_bnb': cbnb, 'contract_usdt': cusdt, 'block': head}


def classify(views):
    pick = lambda rx: {k: v for k, v in views.items() if rx.search(k.split('(')[0]) and not isinstance(v, str)}
    price = pick(PRICE_N); supply = pick(SUPPLY_N); wallet = pick(WALLET_N); state = {k: v for k, v in views.items() if STATE_N.search(k.split('(')[0]) and isinstance(v, (bool, int))}
    addrs = {k: v for k, v in views.items() if isinstance(v, str) and v.startswith('0x') and len(v) == 42}
    return {'price': price, 'supply': supply, 'per_wallet_limit': wallet, 'state': state, 'addresses': addrs}


def progress(sup):
    """从供应类读数里猜「已售/上限」：取名字像已售的数和像上限的数（上限 > 已售）。只是提示，以合约语义为准。"""
    sold = {k: v for k, v in sup.items() if re.search(r'(minted|sold|successful|claimed|totalsupply|counter|nexttokenid|sharessold|filled)', k, re.I) and isinstance(v, int) and not isinstance(v, bool)}
    cap = {k: v for k, v in sup.items() if re.search(r'(max|cap|total_shares|round_shares|opening|open_at|openat|tickets)', k, re.I) and isinstance(v, int) and not isinstance(v, bool) and not re.search(r'(wallet|per_|perwallet|peraddress)', k, re.I)}
    best = None
    for ks, s in sold.items():
        for kc, c in cap.items():
            if c and 0 <= s <= c and c < 10 ** 30:
                p = s / c
                if best is None or ('supply' in kc.lower()) < ('supply' in best[1].lower()): best = (ks, kc, s, c, round(100 * p, 2))
    return {'sold_key': best[0], 'cap_key': best[1], 'sold': best[2], 'cap': best[3], 'pct': best[4]} if best else None


# ---------- ④ Mint 函数识别 + ⑤ 模拟 ----------
VIEWISH = re.compile(r'(open|opened|active|ed|by|price|count|of|at|limit|cap|supply|fee|info|status|time|start|end|amount|s)$', re.I)


def mint_candidates(fns, method_hint=None):
    out = [f for f in fns if MINT_RE.match(f['name']) and f['mutability'] in ('payable', 'nonpayable', 'unknown') and not ADMIN_RE.match(f['name'])
           and not (f['mutability'] == 'unknown' and VIEWISH.search(f['name']) and not f['name'].lower().endswith(('mints',)) and f['name'].lower() not in ('mint', 'claim', 'buy'))]
    if method_hint:
        out.sort(key=lambda f: 0 if f['sig'] == method_hint else 1)
    else:
        out.sort(key=lambda f: (0 if f['mutability'] == 'payable' else 1, len(f['inputs']), f['name']))
    return out[:8]


def build_args(f, me, qty):
    """常见参数形状自动填；需要证明/签名/结构体的返回 None（交 AI 或项目官网）。"""
    parts = []
    for x in f['inputs']:
        ty = x['type']; nm = (x.get('name') or '').lower()
        if ty.endswith(']') or ty in ('bytes', 'string') or ty.startswith('tuple') or ty.startswith('('): return None
        if ty.startswith(('uint', 'int')):
            if re.search(r'(max|limit|slip|deadline)', nm): parts.append(C.enc_uint(2 ** 255 if 'deadline' not in nm else int(time.time()) + 600) if 'max' in nm or 'deadline' in nm else C.enc_uint(0))
            elif re.search(r'(id|token|index|round|batch|height)', nm) and nm not in ('amount', 'qty'): parts.append(C.enc_uint(0))
            else: parts.append(C.enc_uint(qty))
        elif ty == 'address': parts.append(C.enc_addr(me if re.search(r'(to|recipient|receiver|user|account|minter)', nm) or not nm else '0x' + '0' * 40))
        elif ty == 'bool': parts.append(C.enc_uint(0))
        elif ty == 'bytes32' and not re.search(r'(proof|sig|root|hash)', nm): parts.append('0' * 64)
        else: return None
    return f['selector'] + ''.join(parts)


ALLOW_SLOT = {C.USDT: 2}  # BSC USDT _allowances 在 slot 2（stateOverride 实测）


def allow_override(token, owner, spender):
    i = ALLOW_SLOT.get(token)
    if i is None: return None
    inner = C.kec(bytes.fromhex(C.enc_addr(owner) + C.enc_uint(i)))
    slot = '0x' + C.kec(bytes.fromhex(C.enc_addr(spender) + inner))
    return {token: {'stateDiff': {slot: '0x' + 'f' * 64}}}


def simulate(addr, cands, ws, price_wei, qty, pay_token=None):
    t = time.time(); rows = []
    jobs = []
    for f in cands:
        for w in ws:
            data = build_args(f, w['address'], qty)
            if data is None: rows.append({'fn': f['sig'], 'wallet': w['label'], 'ok': None, 'why': '参数含证明/签名/结构体，需链下数据'}); continue
            vals = [0] + ([price_wei * qty] if price_wei and f['mutability'] in ('payable', 'unknown') else [])
            for v in vals: jobs.append((f, w, data, v))
    if pay_token in ALLOW_SLOT:  # 代币付款：再模拟一遍「假设已精确授权」（stateOverride，不真授权）
        jobs += [(f, w, data, -1) for f, w, data, v in list(jobs) if v == 0]
    calls = []
    for f, w, data, v in jobs:
        tx = {'from': w['address'], 'to': addr, 'data': data}
        if v > 0: tx['value'] = hex(v)
        calls.append(('eth_call', [tx, 'latest', allow_override(pay_token, w['address'], addr)] if v == -1 else [tx, 'latest']))
    res = C.batch(calls) if calls else []
    gas_calls = []
    for (f, w, data, v), r in zip(jobs, res):
        if isinstance(r, dict) and 'error' in r:
            e = r['error'] or {}
            if re.search(r'(all rpc failed|too many|limit|timeout)', str(e).lower()):
                rows.append({'fn': f['sig'], 'wallet': w['label'], 'value_wei': max(v, 0), 'assume_approved': v == -1, 'ok': None, 'why': 'RPC 失败（未判定）', 'calldata': data}); continue
            why = C.decode_revert((e.get('data') if isinstance(e, dict) else None) or (e.get('message') if isinstance(e, dict) else str(e)))
            rows.append({'fn': f['sig'], 'wallet': w['label'], 'value_wei': max(v, 0), 'assume_approved': v == -1, 'ok': False, 'why': why, 'calldata': data})
        else:
            rows.append({'fn': f['sig'], 'wallet': w['label'], 'value_wei': max(v, 0), 'assume_approved': v == -1, 'ok': True, 'ret': (r or '')[:66], 'calldata': data})
            tx = {'from': w['address'], 'to': addr, 'data': data}
            if v > 0: tx['value'] = hex(v)
            if v != -1: gas_calls.append((len(rows) - 1, ('eth_estimateGas', [tx])))
    if gas_calls:
        g = C.batch([c for _, c in gas_calls[:12]])
        for (idx, _), x in zip(gas_calls[:12], g):
            if isinstance(x, str): rows[idx]['gas'] = int(x, 16)
    lap('5_simulate', t)
    return rows


# ---------- 兜底：常见选择器 + 价格阶梯 ----------
PAT = C._load(os.path.join(C.LIB, 'patterns_preflight.json'), {})
LADDER = [0, 10 ** 14, 22 * 10 ** 13, 3 * 10 ** 14, 5 * 10 ** 14, 10 ** 15, 2 * 10 ** 15, 5 * 10 ** 15]


def ladder_probe(addr, info, cands, w, price_wei):
    t = time.time(); code = info['effective_code'].lower(); probes = []
    for c in cands[:3]:
        if build_args(c, w['address'], 1): probes.append((c['sig'], build_args(c, w['address'], 1)))
    for pc in PAT.get('mint_candidates', []):
        if pc['selector'][2:] in code and pc['name'] not in [p[0] for p in probes]:
            d = pc.get('calldata') or pc['calldata_template'].replace('{uint256}', C.enc_uint(1)).replace('{address}', C.enc_addr(w['address']))
            probes.append((pc['name'], d))
    vals = ([price_wei] if price_wei else []) + LADDER
    calls = [(sig, d, v) for sig, d in probes[:6] for v in vals[:9]]
    res = C.batch([('eth_call', [dict({'from': w['address'], 'to': addr, 'data': d}, **({'value': hex(v)} if v else {})), 'latest']) for _, d, v in calls]) if calls else []
    rows = []; done = set()
    for (sig, d, v), r in zip(calls, res):
        if sig in done: continue
        if isinstance(r, str):
            rows.append({'fn': sig, 'wallet': w['label'], 'value_wei': v, 'ok': True, 'ladder': True, 'calldata': d}); done.add(sig)
    lap('5b_ladder_probe', t)
    return rows


# ---------- baw preview（币安钱包官方模拟 + 风险，只预演不执行） ----------
BAW = '$AIDBOT_HOME/baw/node_modules/.bin/baw'


def baw_preview(to, data, value, label):
    import subprocess
    acc = {a['label']: a for a in json.load(open(ACC_FILE))}.get(label)
    if not acc or not os.path.exists(BAW): return {'ok': False, 'why': '没有 baw 或账号'}
    env = dict(os.environ, HOME=acc['home'], npm_config_cache='/tmp/npmcache')
    t = time.time()
    try:
        r = subprocess.run([BAW, 'contract-call', 'preview', '--binanceChainId', '56', '--from', acc['bsc'], '--to', to, '--value', str(value), '--inputData', data, '--json'],
                           env=env, capture_output=True, text=True, timeout=90)
        p = json.loads(r.stdout)
    except Exception as e: return {'ok': False, 'why': 'preview 调用失败 ' + str(e)[:80]}
    finally: lap('8_baw_preview', t)
    if not p.get('success'): return {'ok': False, 'why': str(p)[:200]}
    sr = p['data'].get('simulationResult', {}); rk = p['data'].get('risks', {})
    issues = []
    if sr.get('simulationCode') != '000000000': issues.append(f"模拟失败 {sr.get('simulationCode')} {str(sr.get('simulationMsg', ''))[:100]}")
    if rk.get('riskDetails') or rk.get('riskBehaviors') or any((rk.get('addresses') or {}).values()): issues.append('风险 ' + json.dumps(rk, ensure_ascii=False)[:200])
    if sr.get('authorityChanges'): issues.append('权限变更')
    if sr.get('allowanceChanges'): issues.append('授权变更 ' + json.dumps(sr['allowanceChanges'])[:150])
    return {'ok': not issues, 'wallet': label, 'issues': issues, 'balance_changes': str(sr.get('balanceChanges') or sr.get('assetChanges') or '')[:300]}


# ---------- 风险检查 ----------
def risks(info, itf, views, cls, fns, sim):
    t = time.time(); out = []; lvl = 0
    def add(level, msg):
        nonlocal lvl; out.append({'level': level, 'msg': msg}); lvl = max(lvl, {'info': 0, 'warn': 1, 'high': 2}[level])
    if not itf['verified']: add('high', '源码未验证（Sourcify 无匹配；如字节码指纹与已验证合约一致可放行，见 library_match）')
    if info['proxy'] in ('eip1967', 'beacon'):
        add('high' if info.get('admin') or info['proxy'] == 'beacon' else 'warn', f"可升级代理（{info['proxy']}），实现 {info['impl']}，admin {info.get('admin')}")
    elif info['proxy'] == 'eip1167': add('info', f"最小代理，实现 {info['impl']}（不可升级）")
    owner = views.get('owner()')
    if owner and isinstance(owner, str) and int(owner, 16) != 0: add('info', f'有 owner {owner}')
    elif 'owner()' in views: add('info', 'owner 已放弃（0 地址）')
    dang = sorted({f['name'] for f in fns if DANGER_RE.match(f['name'])})
    if dang:
        add('high' if any(re.match(r'(withdraw|rescue|sweep|emergency|recover|migrate|upgradeto)', d, re.I) for d in dang) and owner and int(owner, 16) != 0 else 'warn',
            '项目方函数：' + '、'.join(dang[:10]) + '（需看源码确认能否提走募集资金）')
    adm = sorted({f['name'] for f in fns if ADMIN_RE.match(f['name'])} - set(dang))
    if adm: add('info', '其他管理函数：' + '、'.join(adm[:12]))
    tax = {k: v for k, v in views.items() if re.search(r'(tax|fee)', k, re.I) and isinstance(v, int) and v < 10 ** 6}
    if tax: add('info', '费率类读数：' + json.dumps(tax, ensure_ascii=False))
    tre = {k: v for k, v in views.items() if re.search(r'(treasury|(?<!re)fund(?!ed)|dev|team|marketing)', k, re.I) and not re.search(r'(delay|owed|refund)', k, re.I)}
    if tre: add('warn', '国库/项目方地址：' + json.dumps(tre, ensure_ascii=False) + '（需核对每笔进国库比例 ≤30%）')
    if C.USDT[2:] in info['effective_code'].lower(): add('info', '合约码写死 USDT 地址：可能用 USDT 付款（需精确授权，本工具不授权）')
    if info['has_registry']: add('info', '合约码含 Agent ID 登记合约：需持 .ai 才能铸')
    lap('6_risk', t)
    return {'level': ['low', 'medium', 'high'][lvl], 'items': out}


def token_pool(views):
    """找代币地址并查 Pancake V2 是否已开池。"""
    t = time.time(); addrs = sorted({v for k, v in views.items() if isinstance(v, str) and v.startswith('0x') and len(v) == 42 and v not in SKIP_ADDR and not re.search(r'(owner|fund|treasury|router|pair|registry|dev|team)', k, re.I)})
    dec = C.batch([('eth_call', [{'to': a, 'data': '0x313ce567'}, 'latest']) for a in addrs]) if addrs else []
    toks = [a for a, d in zip(addrs, dec) if isinstance(d, str) and len(d) == 66 and 0 < (C.dec_uint(d) or 0) <= 36]
    out = []
    for tk in toks[:3]:
        r = C.batch([('eth_call', [{'to': C.FACTORY, 'data': '0xe6a43905' + C.enc_addr(tk) + C.enc_addr(q)}, 'latest']) for q in (C.WBNB, C.USDT)]
                    + [('eth_call', [{'to': tk, 'data': '0x95d89b41'}, 'latest']), ('eth_call', [{'to': tk, 'data': '0x18160ddd'}, 'latest'])])
        pairs = {q: C.dec_addr(x) for q, x in zip(('WBNB', 'USDT'), r[:2]) if isinstance(x, str) and C.dec_addr(x) and int(C.dec_addr(x), 16)}
        out.append({'token': tk, 'symbol': C.dec_str(r[2]) if isinstance(r[2], str) else '', 'supply': (C.dec_uint(r[3]) or 0) / 1e18 if isinstance(r[3], str) else None,
                    'pairs': pairs, 'pool_open': bool(pairs)})
    lap('7_token_pool', t)
    return out


# ---------- 汇总 ----------
def check_contract(addr, ws, qty, use_lib=True):
    rep = {'address': addr.lower(), 'bscscan': f'https://bscscan.com/address/{addr.lower()}'}
    if addr.lower() in SKIP_ADDR:
        rep.update(verdict='not_mint', reason='已知基础合约（USDT/WBNB/路由/登记合约等），不是 Mint 合约'); return rep, None
    t = time.time(); info = C.resolve(addr); lap('2a_code_proxy', t)
    if not info['is_contract']:
        rep.update(verdict='blocked', reason='不是合约（EOA 或未部署）'); return rep, info
    t = time.time(); m = L.match(info) if use_lib else {'exact': [], 'similar': [], 'template': None}; lap('2b_library_match', t)
    itf = interface(info)
    method_hint = None
    for r in (m['exact'] + m['similar']):
        if r.get('mint_fn'): method_hint = r['mint_fn']; break
    vfp = C._load('$AIDBOT_DATA/verified_fp.json', {})  # 1000+ 个已验证合约的指纹（用户 11-01 20:36 规则：指纹一致视为已验证）
    fp_verified = (bool(m['exact']) and any(r.get('verified') for r in m['exact'])) or (info['fingerprint'] in set(vfp.values()) and not C.sourcify(addr).get('verified'))
    rep['fingerprint_verified'] = fp_verified
    st = read_state(addr, itf['functions'], ws)
    cls = classify(st['views']); prog = progress(cls['supply'])
    cands = mint_candidates(itf['functions'], method_hint)
    price_wei = None
    for k, v in cls['price'].items():
        if isinstance(v, int) and 0 < v < 10 ** 21 and not re.search(r'(fee_bps|bps|tax)', k, re.I): price_wei = v; break
    usdt_pay = C.USDT[2:] in info['effective_code'].lower()
    sim = simulate(addr, cands, ws, price_wei, qty, C.USDT if usdt_pay else None)
    ladder = []
    if not any(x.get('ok') for x in sim) and ws:
        ladder = ladder_probe(addr, info, cands, ws[0], price_wei)
        sim += ladder
    subs = [v for v in cls['addresses'].values() if v not in SKIP_ADDR][:8]
    if not info['has_registry'] and subs:  # 资格判断放在子合约里（如 eligibility()）
        cs = C.batch([('eth_getCode', [a, 'latest']) for a in subs])
        if any(isinstance(c, str) and C.REGISTRY[2:] in c.lower() for c in cs): info['has_registry'] = True
    rk = risks(info, itf, st['views'], cls, itf['functions'], sim)
    pools = token_pool(st['views'])
    oks = [s for s in sim if s.get('ok')]
    free_ok = [s for s in oks if not s.get('value_wei') and not s.get('assume_approved') and re.search(r'(mint|register|claimgift|claimfree|freeclaim|claimsponsor)', s['fn'].split('(')[0], re.I)]
    eligible = [w for w, p in st['per_wallet'].items() if p.get('agent_id')]
    reasons = sorted({s['why'] for s in sim if s.get('ok') is False})[:6]
    unfillable = sorted({s['fn'] for s in sim if s.get('ok') is None and '链下' in (s.get('why') or '')})
    needs_ai = (not cands) or (not m['exact'] and not oks and (not itf['verified'] or any(s.get('ok') is None for s in sim)))
    closed = any(re.search(r'(paused)', k, re.I) and v is True for k, v in cls['state'].items()) or \
        any(re.search(r'(sold ?out|not ?(started|open|active|live)|ended|closed|paused|max ?supply|too ?early|too ?late|expired|deadline)', r, re.I) for r in reasons) and not oks
    allowance_hit = any(re.search(r'allowance', s.get('why') or '', re.I) for s in sim) or any(s.get('ok') and s.get('assume_approved') for s in sim)
    pay = 'USDT' if allowance_hit else ('BNB' if any(s.get('ok') and s.get('value_wei') for s in sim) or (price_wei and any(f['mutability'] == 'payable' for f in cands)) else ('免费' if free_ok else '未知'))
    if rk['level'] == 'high' and not fp_verified: verdict = 'blocked'
    elif free_ok: verdict = 'free_ready'
    elif closed: verdict = 'not_open'
    elif oks or pay in ('USDT', 'BNB'): verdict = 'paid_confirm'
    else: verdict = 'needs_ai'
    best = next((x for x in sim if x.get('ok') and not x.get('assume_approved') and not x.get('value_wei')), None) or next((x for x in sim if x.get('ok')), None)
    if best:
        rep['tx_draft'] = {'to': addr.lower(), 'value_wei': best.get('value_wei', 0), 'value_bnb': (best.get('value_wei') or 0) / 1e18, 'inputData': best['calldata'],
                           'fn': best['fn'], 'from_example': best['wallet'], 'needs_usdt_approve': bool(best.get('assume_approved')), 'found_by_ladder': bool(best.get('ladder'))}
        if PREVIEW and not best.get('assume_approved'):
            rep['baw_preview'] = baw_preview(addr.lower(), best['calldata'], best.get('value_wei', 0), best['wallet'])
    if any(x.get('ladder') and x.get('ok') for x in sim) and verdict in ('needs_ai',):
        verdict = 'free_ready' if any(x.get('ladder') and x.get('ok') and not x['value_wei'] for x in sim) else 'paid_confirm'
    if needs_ai and verdict not in ('blocked',): rep['needs_ai'] = True
    rep.update({
        'name': st['views'].get('name()') or itf['contract_name'], 'symbol': st['views'].get('symbol()'),
        'verdict': verdict, 'verified': itf['verified'], 'contract_name': itf['contract_name'],
        'proxy': info['proxy'], 'impl': info['impl'], 'fingerprint': info['fingerprint'], 'agent_id_gate': info['has_registry'],
        'pay_token': pay,
        'price_reads': cls['price'], 'price_wei_guess': price_wei, 'supply_reads': cls['supply'], 'progress': prog,
        'per_wallet_limit_reads': cls['per_wallet_limit'], 'state_reads': cls['state'], 'address_reads': cls['addresses'],
        'contract_balance': {'bnb': st['contract_bnb'], 'usdt': st['contract_usdt']}, 'block': st['block'],
        'mint_functions': [{'sig': f['sig'], 'selector': f['selector'], 'payable': f['mutability']} for f in cands],
        'wallets': st['per_wallet'], 'eligible_wallets': eligible,
        'simulation': [{k: v for k, v in s.items() if k != 'calldata'} for s in sim], 'sim_ok_calldata': {s['wallet'] + ':' + s['fn']: s['calldata'] for s in oks},
        'revert_reasons': reasons, 'needs_params': unfillable, 'risk': rk, 'token_pool': pools,
        'history': [(r.get('at_cst'), r.get('result'), r.get('reason')) for r in L.summarize(addr)][-5:],
        'library_match': {'exact': [(r['address'], r.get('name'), r.get('result'), r.get('reason')) for r in m['exact']],
                          'similar': [(r['address'], r.get('name'), r.get('_similarity'), r.get('result')) for r in m['similar']],
                          'template': m['template'], 'method_reused': method_hint},
        'unknown_selectors': itf['unknown_selectors'],
    })
    if rep.get('needs_ai'):
        rep['ai_brief'] = {'why': '库中无匹配，且函数参数无法自动填或模拟全部失败', 'functions': [f['sig'] for f in itf['functions'] if not ADMIN_RE.match(f['name'])][:40],
                           'reverts': reasons, 'source': rep['bscscan'] + '#code' if itf['verified'] else 'https://repo.sourcify.dev/56/' + addr.lower()}
    return rep, info


PREVIEW = '--preview' in sys.argv


def main():
    argv = sys.argv[1:]
    if not argv: sys.exit(__doc__)
    def opt(k, d=None): return argv[argv.index(k) + 1] if k in argv else d
    only = opt('--wallets'); ws = wallets(only.split(',') if only else None); qty = int(opt('--qty', '1'))
    targets = [x for i, x in enumerate(argv) if not x.startswith('--') and (i == 0 or argv[i - 1] not in ('--wallets', '--qty'))]
    site = None; addrs = []
    for x in targets:
        if re.fullmatch(r'0x[0-9a-fA-F]{40}', x): addrs.append(x.lower())
        else: site = from_url(x); addrs += site['addresses']
    # 网址里可能有很多地址：先并行读代码，只留合约，按「含 mint 类选择器 / Agent ID」排序，最多深查 4 个
    t = time.time()
    codes = C.batch([('eth_getCode', [a, 'latest']) for a in addrs]) if addrs else []
    mintsel = {C.sel(s) for s in ('mint()', 'mint(uint256)', 'freeMint()', 'claim()', 'publicMint(uint256)', 'buy(uint256)', 'register()', 'claimFree()', 'claimGift()', 'mint(address,uint256)')}
    scored = []
    for a, c in zip(addrs, codes):
        if not isinstance(c, str) or len(c) < 6: continue
        s = C.push4(c); sc = len(set(s) & mintsel) * 3 + (2 if C.REGISTRY[2:] in c else 0) + (1 if len(c) > 2000 else 0) + (site['seen'][a]['count'] / 10 if site and a in site['seen'] else 0)
        if not site: sc += 100  # 直接给的 CA 一定查
        scored.append((sc, a))
    if site and scored:  # 网址模式：排除交易对（token0()+getReserves() 都能读）
        pr = C.batch([(('eth_call', [{'to': a, 'data': d}, 'latest'])) for _, a in scored for d in ('0x0dfe1681', '0x0902f1ac')])
        scored = [x for i, x in enumerate(scored) if not (isinstance(pr[2 * i], str) and len(pr[2 * i]) == 66 and isinstance(pr[2 * i + 1], str) and len(pr[2 * i + 1]) >= 194)]
    scored.sort(reverse=True); deep = [a for _, a in scored[:4]] if site else [a for _, a in scored]
    lap('1b_pick_contracts', t)
    reports = []
    if not deep: print(json.dumps({'input': targets, 'error': '没有找到合约（地址无代码或读取失败）', 'timing_s': TIMES}, ensure_ascii=False)); return
    def one(a):
        try: return check_contract(a, ws, qty, '--no-lib' not in argv)
        except Exception as e: return {'address': a, 'verdict': 'needs_ai', 'error': str(e)[:300]}, None
    with ThreadPoolExecutor(min(4, max(1, len(deep)))) as ex:
        res = list(ex.map(one, deep))
    for rep, info in res:
        reports.append(rep)
        if info and '--no-lib' not in argv:  # ② 写经验库（预检记录；成交/失败由执行脚本或人工 record 补）
            L.add({'address': rep['address'], 'name': rep.get('name'), 'fingerprint': info['fingerprint'], 'selectors': info['selectors'],
                   'proxy': info['proxy'], 'impl': info['impl'], 'verified': rep.get('verified'), 'result': 'precheck:' + rep['verdict'],
                   'reason': '; '.join(rep.get('revert_reasons') or [])[:300], 'mint_fn': (rep.get('mint_functions') or [{}])[0].get('sig'),
                   'pay_token': rep.get('pay_token'), 'price_wei': rep.get('price_wei_guess'), 'progress': rep.get('progress'),
                   'sim_ok': [k for k in rep.get('sim_ok_calldata', {})][:6], 'source': 'mintcheck', 'site': site and site['base']})
    TIMES['total'] = round(time.time() - T0, 2)
    out = {'input': targets, 'site': {k: v for k, v in (site or {}).items() if k != 'guides'} if site else None,
           'site_guides': list((site or {}).get('guides', {}).keys()), 'reports': reports, 'timing_s': TIMES, 'rpc': C.STATS,
           'signed_or_sent': False}
    os.makedirs(os.path.join(C.LIB, 'reports'), exist_ok=True)
    fn = os.path.join(C.LIB, 'reports', time.strftime('%Y%m%d-%H%M%S') + '-' + (deep[0][:10] if deep else 'none') + '.json')
    json.dump(out, open(fn, 'w'), ensure_ascii=False, indent=1, default=str); out['report_file'] = fn
    if '--json' in argv: print(json.dumps(out, ensure_ascii=False, indent=1, default=str)); return
    print(render(out))


def human(d):
    o = {}
    for k, v in d.items():
        if isinstance(v, int) and not isinstance(v, bool) and 1.6e9 < v < 2.2e9: o[k] = time.strftime('%m-%d %H:%M CST', time.gmtime(v + 8 * 3600))
        elif isinstance(v, int) and not isinstance(v, bool) and v >= 10 ** 15: o[k] = f'{v / 1e18:g}e18'
        else: o[k] = v
    return json.dumps(o, ensure_ascii=False)


VERD = {'not_mint': '— 不是 Mint 合约', 'free_ready': '✅ 免费可铸（模拟通过）', 'paid_confirm': '💰 付费，需你确认', 'not_open': '⏸ 未开放/已结束/售罄', 'blocked': '⛔ 高危或不符合', 'needs_ai': '🤖 陌生合约，需 AI 分析'}


def render(out):
    L_ = []
    s = out.get('site')
    if s: L_.append(f"网址 {s['base']}｜{s.get('title','')}｜读了 {s['pages_read']} 个页面，发现 {len(s['addresses'])} 个地址；Agent 指南：{', '.join(out['site_guides']) or '无'}")
    side = [r for r in out['reports'] if s and not r.get('mint_functions') and not r.get('error')]
    for r in out['reports']:
        if r in side: continue
        L_.append(f"\n■ {r.get('name') or r.get('contract_name') or '未命名'} {r['address']}  → {VERD.get(r['verdict'], r['verdict'])}")
        if r.get('error'): L_.append(f"  错误：{r['error']}"); continue
        if r.get('reason') and r['verdict'] in ('not_mint', 'blocked') and 'risk' not in r: L_.append('  ' + r['reason']); continue
        L_.append(f"  源码验证：{'是' if r.get('verified') else ('否（字节码指纹与已验证合约一致，视为已验证）' if r.get('fingerprint_verified') else '否')}｜代理：{r.get('proxy') or '无'}｜Agent ID 门槛：{'是' if r.get('agent_id_gate') else '否'}｜付款：{r.get('pay_token')}")
        if r.get('mint_functions'): L_.append('  Mint 函数：' + '、'.join(f"{m['sig']}[{m['payable']}]" for m in r['mint_functions'][:5]))
        if r.get('price_reads'): L_.append('  价格读数：' + human(r['price_reads'])[:300])
        if r.get('progress'): p = r['progress']; sc = 1e18 if p['cap'] >= 10 ** 15 else 1; L_.append(f"  进度：{p['sold']/sc:g}/{p['cap']/sc:g}（{p['pct']}%，{p['sold_key']} / {p['cap_key']}）")
        elif r.get('supply_reads'): L_.append('  供应读数：' + human(r['supply_reads'])[:300])
        if r.get('per_wallet_limit_reads'): L_.append('  每号上限：' + human(r['per_wallet_limit_reads'])[:200])
        if r.get('state_reads'): L_.append('  开放状态：' + human(r['state_reads'])[:300])
        L_.append(f"  持 Agent ID：{'、'.join(r.get('eligible_wallets') or []) or '无'}｜合约余额 BNB {r['contract_balance']['bnb']} / USDT {r['contract_balance']['usdt']}")
        if r.get('history'): L_.append('  本合约历史：' + '；'.join(f"{h[0]} {h[1]} {h[2] or ''}"[:80] for h in r['history'][-3:]))
        ok = [x for x in r.get('simulation', []) if x.get('ok')]; bad = [x for x in r.get('simulation', []) if x.get('ok') is False]
        if ok: L_.append('  模拟通过的号：' + '、'.join(sorted({x['wallet'] for x in ok})))
        L_.append(f"  模拟：通过 {len(ok)} 条，失败 {len(bad)} 条" + (f"；通过示例 {ok[0]['wallet']} {ok[0]['fn']} value={ok[0].get('value_wei',0)} gas={ok[0].get('gas')}" + ('（假设已精确授权 USDT）' if ok[0].get('assume_approved') else '') if ok else ''))
        td = r.get('tx_draft')
        if td: L_.append(f"  待确认交易：to {td['to']}｜value {td['value_bnb']:g} BNB｜inputData {td['inputData'][:74]}{'…' if len(td['inputData']) > 74 else ''}｜{td['fn']}" + ('｜需先精确授权 USDT' if td['needs_usdt_approve'] else '') + ('｜价格阶梯探测得出' if td['found_by_ladder'] else ''))
        bp = r.get('baw_preview')
        if bp: L_.append(f"  baw preview（{bp.get('wallet','')}）：" + ('干净' if bp['ok'] else '有问题：' + '；'.join(bp.get('issues') or [bp.get('why', '')])))
        if r.get('revert_reasons'): L_.append('  失败原因：' + '；'.join(r['revert_reasons'][:4]))
        if r.get('needs_params'): L_.append('  需链下参数（证明/签名/列表）：' + '、'.join(r['needs_params'][:4]))
        for it in r['risk']['items']: L_.append(f"  [{it['level']}] {it['msg']}"[:260])
        for tp in r.get('token_pool') or []: L_.append(f"  代币 {tp['symbol']} {tp['token']}｜{'已开池 ' + json.dumps(tp['pairs']) if tp['pool_open'] else '未开池'}")
        lm = r['library_match']
        if lm['exact'] or lm['similar']:
            L_.append('  经验库：' + ('同指纹 ' + '、'.join(f"{a[1] or a[0][:10]}({a[2]})" for a in lm['exact'][:3]) if lm['exact'] else '') + (' 相似 ' + '、'.join(f"{a[1] or a[0][:10]}({a[2]})" for a in lm['similar'][:3]) if lm['similar'] else '') + (f"｜复用方法 {lm['method_reused']}" if lm['method_reused'] else ''))
        else: L_.append('  经验库：无匹配（新模板）')
        if r.get('needs_ai'): L_.append('  需要 AI：' + r['ai_brief']['why'])
    if side: L_.append('\n其他合约（无 Mint 函数）：' + '；'.join(f"{r.get('name') or r.get('contract_name') or ''} {r['address']}" for r in side))
    L_.append(f"\n耗时：{json.dumps(out['timing_s'], ensure_ascii=False)}｜RPC {out['rpc']['rpc_calls']} 次（失败 {out['rpc']['rpc_errors']}）｜未签名、未授权、未付款")
    return '\n'.join(L_)


if __name__ == '__main__': main()
