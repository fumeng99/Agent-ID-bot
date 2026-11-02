#!/usr/bin/env python3
"""新模板自动识别（用户 2026-11-03 02:4x 要求，起因：AGC 押金全退挖矿 0x6288…3743 因模板不认识被漏掉）。
【只读】本脚本绝不签名、不发交易，只读链上 + 源码，输出分析。
2026-11-03 03:21 统一付费自动买：analyze 额外提取 params.progress（已售/上限/开池，读不出上限=无）和 params.treasury_bps；
每轮重读已识别合约进度，≥60% 且 pass 的付费类只「拉起 autofill.py」（买不买由 autofill 全部闸门决定，本脚本仍不签名不交易）。

discoverd 每约 120 秒拉起（flock -n /tmp/newtpl.lock）。流程：
1. 候选：fairmint/found.json 里 template 为 None/'other' 的合约 + chainscan/candidates.json（contract/mint_target）
   + salearm_plan.json 的合约 + discover_out/hotfree.json 的 alerts。已知模板（jager/jagent/boss/mao）排除。
2. 热度门槛：近 30 分钟（约 4000 块，BSC 0.45 秒/块）合约日志对应交易的 from ≥30 个不同地址（rpcpool 拉 getLogs + 交易）。
   日志读不到 → 本轮跳过热度判断（不记冷、不记失败）。冷合约 10 分钟后再看。
3. 源码：verify_source.py（含字节码指纹放行）必须 verified=True；代理合约取实现合约源码（实现未验证 → 跳过）。
   源码取 Sourcify v2，失败退 OKLink；bytecode-match 的取被匹配合约的源码。
4. 从源码/ABI 提取特征 → 归类 refund_deposit / fair_launch / paid_mint / free_mint / unknown，给 safe_flags / risk_flags 和结论 pass/review/reject。
5. 输出 discover_out/newtpl.json（全部分析结果）、discover_out/newtpl_events.jsonl（只写新识别或结论变化的）。
   状态 newtpl_state.json：已分析 6 小时内不重复；未验证 30 分钟后重查。
6. refund_deposit + pass + refundmine 闸门（已验证、非代理、合约余额 ≥ 应退押金）→ refundmine_ok=true；refundmine.py 会读 newtpl.json 把它并入候选
   （refundmine 自己还会再查 AGC 接口选择器和全部闸门，接口不同的只出分析不挖）。
usage: newtpl.py [--test 0x... [0x...]]   （--test：跳过热度/状态，直接分析并打印，不写 state/events）"""
import sys, os, re, json, time, subprocess, urllib.request
D = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, D); os.chdir(D)
from rpcpool import batch, rpc
from codeutil import sel, upgradeable, IMPL_SLOT

OUT = 'discover_out/newtpl.json'; EV = 'discover_out/newtpl_events.jsonl'; ST = 'newtpl_state.json'
LOG_NODES = ['https://rpc-bsc.48.club', 'https://0.48.club']
HEAT_MIN = 30; WIN_BLOCKS = 4000; RE_ANALYZE = 6 * 3600; RE_UNVER = 1800; RE_COLD = 900
MAX_ANALYZE = 4; DEADLINE = time.time() + 110  # discoverd 用 timeout 170 拉起；过了 DEADLINE 不再开始新分析，每个合约分析完立即落盘
KNOWN_TPL = {'jager', 'jagent', 'boss', 'mao'}
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36'}
DEAD = re.compile(r"0x0*dead\b|address\(0xdead\)|\bDEAD\b|\bdead\b|BURN_ADDRESS|address\(0\)", re.I)
SEND = re.compile(r"\.call\{\s*value\s*:|\.transfer\(|\.send\(|sendValue\(|safeTransferETH\(|_sendBnb\(|_sendETH\(|_safeTransferETH\(|_sendValue\(", re.I)
PRIV_MOD = re.compile(r"\bonly[A-Z]\w*|\bauth\b|\brequiresAuth\b", re.S)
PRIV_BODY = re.compile(r"msg\.sender\s*(==|!=)\s*(owner\(\)|_owner|owner|admin|_admin|dev|treasury|operator|pauser|manager|governance|feeTo\w*|deployer|creator|team|marketing)\b", re.I)
REFUND_NAME = re.compile(r"refund|claimfree|withdrawdeposit|claimdeposit|redeemdeposit|unstake|claimrefund|reclaim|returndeposit", re.I)
REF_ARG = re.compile(r"^_?(ref|referrer|referral|inviter|invite|invitor|upline|parent|sponsor|referer|refAddr|_ref)\w*$", re.I)
SKIP_URL = re.compile(r"github\.com|openzeppelin|eips\.ethereum|soliditylang|solidity\.readthedocs|ethereum\.org|consensys|forum\.|docs\.|wikipedia|uniswap\.org|pancakeswap|eth\.wiki|blog\.|medium\.com/@|stackexchange|hardhat|foundry|spdx|creativecommons|apache\.org|gnu\.org|etherscan|bscscan|w3\.org|ietf", re.I)


def load(p, d):
    try: return json.load(open(p))
    except Exception: return d


def dump(p, o):
    t = p + '.%d.tmp' % os.getpid(); json.dump(o, open(t, 'w'), ensure_ascii=False, indent=1); os.replace(t, p)


def now_cst(t=None): return time.strftime('%m-%d %H:%M', time.gmtime((t or time.time()) + 8 * 3600))


def get(u, t=20): return json.loads(urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=t).read())


# ---------------- 候选 ----------------
def candidates():
    c = {}
    found = load('fairmint/found.json', {})
    known = {a.lower() for a, v in found.items() if isinstance(v, dict) and v.get('template') in KNOWN_TPL}
    for a, v in found.items():
        if isinstance(v, dict) and v.get('template') in (None, 'other'): c.setdefault(a.lower(), set()).add('found')
    cs = load('chainscan/candidates.json', [])
    for x in cs if isinstance(cs, list) else []:
        if not isinstance(x, dict): continue
        for k in ('contract', 'mint_target'):
            if x.get(k): c.setdefault(x[k].lower(), set()).add('chainscan')
    for a in load('salearm_plan.json', {}): c.setdefault(a.lower(), set()).add('salearm')
    for x in (load('discover_out/hotfree.json', {}) or {}).get('alerts', []) or []:
        if isinstance(x, dict) and x.get('contract'): c.setdefault(x['contract'].lower(), set()).add('hotfree')
    return {a: sorted(s) for a, s in c.items() if re.fullmatch(r'0x[0-9a-f]{40}', a) and a not in known}


# ---------------- 热度 ----------------
def heat(addrs):
    """返回 {addr: 不同调用地址数}；日志全部读失败 → None（本轮跳过）；某组读失败 → 该组地址为 None（不记冷）。只统计到 ≥HEAT_MIN 为止。
    多地址 getLogs 只有 48.club 节点支持（dataseed 限流、publicnode 多地址 403、blockrazor 限 25 块）——同 fairmint.LOGS_RPCS。"""
    if not addrs: return {}
    from concurrent.futures import ThreadPoolExecutor
    from rpcpool import _post
    head = int(rpc('eth_blockNumber', []), 16); f0 = head - WIN_BLOCKS
    al = sorted(addrs); groups = [al[i:i + 100] for i in range(0, len(al), 100)]
    jobs = [(gi, b) for gi in range(len(groups)) for b in range(f0, head + 1, 500)]
    def one(j):
        gi, b = j; q = {'address': groups[gi], 'fromBlock': hex(b), 'toBlock': hex(min(b + 499, head))}
        for k, u in enumerate(LOG_NODES * 2):
            if time.time() > DEADLINE - 60: break
            try:
                d = _post(u, {'jsonrpc': '2.0', 'id': 1, 'method': 'eth_getLogs', 'params': [q]}, 20)
                if isinstance(d, dict) and isinstance(d.get('result'), list): return gi, d['result']
            except Exception: pass
            time.sleep(0.5)
        return gi, None
    with ThreadPoolExecutor(8) as ex: rs = list(ex.map(one, jobs))
    bad = {gi for gi, r in rs if r is None}
    if len(bad) == len(groups):
        sys.stderr.write(f'[{now_cst()}] heat getLogs 全部失败，本轮跳过\n'); return None
    res = [r for gi, r in rs if r is not None]
    txs = {}
    for r in res:
        for lg in r or []:
            txs.setdefault(lg['address'].lower(), []).append(lg['transactionHash'])
    out = {a: (None if any(a in groups[g] for g in bad) else 0) for a in addrs}
    need = {}
    for a, hs in txs.items():
        hs = list(dict.fromkeys(hs))
        if len(hs) >= HEAT_MIN: need[a] = hs[-400:]   # 交易数不足 30 的不可能有 30 个不同地址
        else: out[a] = len({h for h in hs})  # 上限估计（必然 <30）
    for a, hs in need.items():
        if time.time() > DEADLINE - 55: out[a] = None; continue
        froms = set()
        for i in range(0, len(hs), 80):
            try: rs = batch([('eth_getTransactionByHash', [h]) for h in hs[i:i + 80]], tolerant=True, chunk=40, timeout=15)
            except Exception: out[a] = None; froms = None; break
            froms |= {(t or {}).get('from', '').lower() for t in rs if t}
            if len(froms) >= HEAT_MIN: break
        if froms is not None: out[a] = len(froms - {''})
    return out


# ---------------- 源码 ----------------
def verify(a):
    try:
        r = subprocess.run(['python3', 'verify_source.py', a], capture_output=True, text=True, timeout=90)
        return json.loads(r.stdout.splitlines()[0])
    except Exception as e: return {'verified': None, 'source': 'verify_err ' + str(e)[:80]}


def fetch_source(a):
    """返回 (sources{path:content}, abi, contract_name) 或 (None, None, None)。"""
    try:
        d = get(f'https://sourcify.dev/server/v2/contract/56/{a}?fields=sources,abi,compilation', 25)
        if d.get('sources'):
            return {k: v.get('content', '') for k, v in d['sources'].items()}, d.get('abi') or [], (d.get('compilation') or {}).get('name')
    except Exception: pass
    for i in range(3):
        try:
            r = get(f'https://www.oklink.com/api/v5/explorer/contract/verify-contract-info?chainShortName=BSC&contractAddress={a}', 20)
            if r.get('code') == '50011': time.sleep(2 + 2 * i); continue
            x = (r.get('data') or [{}])[0]
            sc = x.get('sourceCode') or ''
            if not sc.strip(): return None, None, None
            srcs = {}
            try:
                j = json.loads(sc[1:-1] if sc.startswith('{{') else sc)
                for k, v in (j.get('sources') or j).items(): srcs[k] = v.get('content', '') if isinstance(v, dict) else str(v)
            except Exception: srcs = {'main.sol': sc}
            try: abi = json.loads(x.get('contractAbi') or '[]')
            except Exception: abi = []
            return srcs, abi, x.get('contractName')
        except Exception: time.sleep(2)
    return None, None, None


def strip_comments(s):
    s = re.sub(r'/\*.*?\*/', ' ', s, flags=re.S)
    return re.sub(r'(?<![:"\'])//[^\n]*', '', s)


def parse_functions(code):
    """{name: [ {params, mods, body} ]}（同名重载放列表），含 receive/fallback。"""
    fns = {}
    for m in re.finditer(r'\b(function\s+(\w+)|receive|fallback)\s*\(([^)]*)\)([^{;]*)\{', code):
        name = m.group(2) or m.group(1)
        i = m.end(); depth = 1
        while i < len(code) and depth:
            if code[i] == '{': depth += 1
            elif code[i] == '}': depth -= 1
            i += 1
        fns.setdefault(name, []).append({'params': m.group(3), 'mods': m.group(4), 'body': code[m.end():i - 1]})
    return fns


def body_with_helpers(fns, name, depth=2, seen=None):
    seen = seen if seen is not None else set()
    if name in seen or name not in fns: return ''
    seen.add(name); out = ''
    for f in fns[name]:
        out += '\n' + f['body']
        if depth > 0:
            for callee in set(re.findall(r'\b(_?\w+)\s*\(', f['body'])):
                if callee in fns and callee != name and not callee[0].isupper():
                    out += body_with_helpers(fns, callee, depth - 1, seen)
    return out


def is_priv(f):
    return bool(PRIV_MOD.search(f['mods']) or PRIV_BODY.search(f['body'][:600]))


# ---------------- 链上读 ----------------
def dec(out_type, r):
    if not r or r == '0x' or len(r) < 66: return None
    if out_type.startswith('uint') or out_type.startswith('int'): return int(r[:66], 16)
    if out_type == 'bool': return bool(int(r[:66], 16))
    if out_type == 'address': return '0x' + r[26:66]
    if out_type == 'string':
        try: return bytes.fromhex(r[130:130 + 2 * int(r[66:130], 16)]).decode(errors='ignore')
        except Exception: return None
    return None


def read_views(a, abi):
    vs = [f for f in abi if f.get('type') == 'function' and f.get('stateMutability') in ('view', 'pure') and not f.get('inputs')
          and len(f.get('outputs') or []) == 1 and (f['outputs'][0]['type'] in ('bool', 'address', 'string') or re.match(r'u?int\d*$', f['outputs'][0]['type']))][:90]
    if not vs: return {}
    try: rs = batch([('eth_call', [{'to': a, 'data': '0x' + sel(f['name'] + '()')}, 'latest']) for f in vs], tolerant=True, chunk=30)
    except Exception: return {}
    return {f['name']: dec(f['outputs'][0]['type'], r) for f, r in zip(vs, rs)}


def call_raw(to, sig, args=''):
    try: return rpc('eth_call', [{'to': to, 'data': '0x' + sel(sig) + args}, 'latest'])
    except Exception: return None


def token_check(token, minter):
    """代币增发/税/权限（复用 riskcheck.py 的选择器检查），返回 (safe, risk, info)。"""
    safe, risk, info = [], [], {'token': token}
    try:
        r = json.loads(subprocess.run(['python3', 'riskcheck.py', token], capture_output=True, text=True, timeout=60).stdout.strip().splitlines()[-1])
    except Exception as e:
        info['riskcheck_err'] = str(e)[:80]; return safe, risk, info
    info.update({k: r.get(k) for k in ('flags', 'owner', 'pair', 'upgradeable_proxy')})
    if r.get('upgradeable_proxy'): risk.append('代币可升级')
    tm = dec('address', call_raw(token, 'minter()'))
    renounced = r.get('owner') in (None, '0x' + '0' * 40)
    for fl in r.get('flags') or []:
        if '已放弃权限' in fl: continue
        if fl == '可增发':
            if tm and tm.lower() == minter.lower(): safe.append('代币增发权限只在本合约（minter=本合约）')
            elif renounced: safe.append('代币 owner 已放弃')
            else: risk.append('代币可被 owner 增发')
        elif fl == '可改税/限额':
            if not renounced: risk.append('代币 owner 可改税/限额')
        elif not renounced: risk.append('代币' + fl)
    # 税率：常见读数，>10% 记风险
    for s in ('buyTax()', 'sellTax()', 'taxFee()', '_taxFee()', 'buyFee()', 'sellFee()', 'totalFees()', 'sellTotalFees()', 'buyTotalFees()', 'TAX_BPS()', 'taxBps()'):
        v = dec('uint256', call_raw(token, s))
        if v is None: continue
        pct = v if v <= 100 else (v / 100 if v <= 10000 else None)
        if pct is None: continue
        info.setdefault('tax', {})[s[:-2]] = pct
        if pct > 10: risk.append(f'税>10%（{s[:-2]}={pct}%）')
    return safe, risk, info


# ---------------- 进度 / 国库（2026-11-03 03:21 统一付费自动买：newtpl 识别的模板也按 ≥60% 进度走 autofill） ----------------
SOLD_KEYS = ('sharesMinted', 'totalMinted', 'totalPaidShares', 'totalSold', 'soldShares', 'sold', 'minted', 'totalShares')
CAP_KEYS = ('MAX_SHARES', 'MAX_MINTS', 'LIQUIDITY_TARGET_SHARES', 'OPEN_AT', 'OPEN_UNITS', 'TOTAL_SHARES', 'maxShares', 'MAX_SUPPLY_SHARES', 'cap', 'CAP')
OPEN_KEYS = ('poolOpen', 'opened', 'finalized', 'liquidityTargetReached', 'launched', 'tradingOpen', 'soldOut')


def extract_progress(views):
    """从无参 view 读数取 已售/上限/是否开池。读不出上限 → None（autofill 不自动买，只简报）。"""
    sold_k = next((k for k in SOLD_KEYS if isinstance(views.get(k), int) and not isinstance(views.get(k), bool)), None)
    cap_k = next((k for k in CAP_KEYS if isinstance(views.get(k), int) and not isinstance(views.get(k), bool) and views.get(k)), None)
    if sold_k == 'totalShares' and cap_k is None: return None
    if not sold_k or not cap_k: return None
    sold, cap = views[sold_k], views[cap_k]
    if cap > 10 ** 9: return None  # 像代币总量（wei），不是份数
    op = next((bool(views[k]) for k in OPEN_KEYS if isinstance(views.get(k), bool)), None)
    if views.get('mintingOpen') is False and op is None: op = True
    return {'sold_fn': sold_k + '()', 'cap_fn': cap_k + '()', 'open_fn': next((k + '()' for k in OPEN_KEYS if isinstance(views.get(k), bool)), None),
            'sold': sold, 'cap': cap, 'pct': round(min(sold * 100 / cap, 999), 2), 'opened': op, 'at': int(time.time())}


def extract_treasury_bps(views, code):
    """国库抽成 bps：TREASURY_BPS 类 view / 10000-LP_BPS / 源码常量 / 源码 lp = x * 70 / 100。读不出 → None。"""
    for k, v in views.items():
        if isinstance(v, int) and not isinstance(v, bool) and re.fullmatch(r'(TREASURY|TEAM|DEV|FEE)_?(BPS|RATE)', k, re.I) and v <= 10000: return v, k
    for k, v in views.items():
        if isinstance(v, int) and not isinstance(v, bool) and re.fullmatch(r'(LP|LIQ\w*|POOL)_?BPS', k, re.I) and v <= 10000: return 10000 - v, f'10000-{k}'
    m = re.search(r'constant\s+(LP|LIQ\w*|POOL)_?BPS\s*=\s*([\d_]+)', code or '', re.I)
    if m: return 10000 - int(m.group(2).replace('_', '')), f'10000-{m.group(1)}_BPS(常量)'
    m = re.search(r'constant\s+(TREASURY|TEAM|DEV)_?BPS\s*=\s*([\d_]+)', code or '', re.I)
    if m: return int(m.group(2).replace('_', '')), f'{m.group(1)}_BPS(常量)'
    pcts = {int(x.group(1)) * 10000 // int(x.group(2).replace('_', '')) for x in re.finditer(r'\b(?:lp|liq\w*|toPool|poolAmt|reserve\w*)\s*=\s*[^;]*?\*\s*(\d+)\s*/\s*(100|1000|10000|10_000)\b', code or '', re.I)}
    if len(pcts) == 1: return 10000 - pcts.pop(), '源码进池比例'
    return None, None


def refresh_progress(res):
    """每轮便宜地重读 newtpl.json 里有进度字段的合约（已售/上限/开池），返回 [(合约, 行)]。"""
    rows = [(a, r) for a, r in res.items() if (r.get('params') or {}).get('progress') and not r['params']['progress'].get('opened')]
    if not rows: return []
    calls = []
    for a, r in rows:
        pg = r['params']['progress']
        calls += [('eth_call', [{'to': a, 'data': '0x' + sel(pg['sold_fn'])}, 'latest']), ('eth_call', [{'to': a, 'data': '0x' + sel(pg['cap_fn'])}, 'latest']),
                  ('eth_call', [{'to': a, 'data': '0x' + sel(pg['open_fn'])}, 'latest']) if pg.get('open_fn') else ('eth_blockNumber', [])]
    try: R = batch(calls, tolerant=True, chunk=30)
    except Exception: return []
    out = []
    for j, (a, r) in enumerate(rows):
        pg = r['params']['progress']; s_, c_, o_ = R[3 * j], R[3 * j + 1], R[3 * j + 2]
        if not (isinstance(s_, str) and len(s_) >= 66 and isinstance(c_, str) and len(c_) >= 66): pg['stale'] = True; continue
        sold, cap = int(s_[:66], 16), int(c_[:66], 16)
        if not cap: continue
        pg.update(sold=sold, cap=cap, pct=round(min(sold * 100 / cap, 999), 2), at=int(time.time()), stale=False)
        if pg.get('open_fn') and isinstance(o_, str) and len(o_) >= 66: pg['opened'] = bool(int(o_[:66], 16))
        out.append((a, r))
    return out


# ---------------- 分析 ----------------
def analyze(a, vinfo):
    impl = vinfo.get('proxy_impl'); src_addr = impl or a
    srcref = vinfo.get('source') or ''
    if impl and str(vinfo.get('impl_verified')) != 'True': return {'status': 'unverified', 'why': '代理实现未验证'}
    fetch_from = src_addr
    if srcref.startswith('bytecode-match:') and not impl: fetch_from = srcref.split(':', 1)[1]
    srcs, abi, cname = fetch_source(fetch_from)
    if not srcs: return {'status': 'source_unavailable', 'why': f'已验证但取不到源码（{fetch_from}）'}
    own = {k: v for k, v in srcs.items() if not re.search(r'openzeppelin|@|/lib/|node_modules|forge-std|solmate|solady', k, re.I)} or srcs
    raw = '\n'.join(own.values()); code = strip_comments(raw)
    code = re.sub(r'\binterface\s+\w+[^{]*\{(?:[^{}]|\{[^{}]*\})*\}', ' ', code)  # 去掉接口声明，避免把声明当调用
    fns = parse_functions(code)
    abif = [f for f in abi if f.get('type') == 'function']
    views = read_views(a, abi)
    bal = int(rpc('eth_getBalance', [a, 'latest']), 16)
    safe, risk = [], []
    p = {'bnb_balance': bal / 1e18}
    pg = extract_progress(views)
    if pg: p['progress'] = pg
    tbps, tsrc = extract_treasury_bps(views, code)
    if tbps is not None: p['treasury_bps'] = tbps; p['treasury_src'] = tsrc

    # 名字 / 官网
    name = cname
    urls = [u for u in dict.fromkeys(re.findall(r'https?://[\w\-\.]+(?:/[\w\-\./?=&%#]*)?', raw)) if not SKIP_URL.search(u)]
    # 入口函数
    payable = [f for f in abif if f.get('stateMutability') == 'payable' and f['name'] not in ('approve', 'transferFrom', 'safeTransferFrom', 'setApprovalForAll', 'transfer', 'multicall')]
    pay_names = [f['name'] for f in payable]
    p['payable_entries'] = [f"{f['name']}({','.join(i['type'] for i in f['inputs'])})" for f in payable]
    free_entries = [f['name'] for f in abif if f.get('stateMutability') == 'nonpayable' and re.search(r'mint|claim|free|airdrop', f['name'], re.I)
                    and not re.search(r'withdraw|owner|admin|set|pause|rescue|sweep|refund|reward|lp', f['name'], re.I)
                    and not any(is_priv(x) for x in fns.get(f['name'], []))]
    # 单价
    price = {k: v for k, v in views.items() if isinstance(v, int) and not isinstance(v, bool) and re.search(r'price|cost|mint_?fee|fee_?per|per_?share', k, re.I)}
    p['price_views'] = {k: v / 1e18 if v > 10 ** 12 else v for k, v in price.items()}
    vexpr = []
    for n in pay_names:
        for f in fns.get(n, []):
            vexpr += re.findall(r'msg\.value\s*(?:!=|==|<|>=|<=|>)\s*([^;{)]+(?:\)[^;{)]*)?)', body_with_helpers(fns, n))
    p['value_check'] = list(dict.fromkeys(x.strip()[:80] for x in vexpr))[:4]
    # 上限
    p['limits'] = {k: v for k, v in views.items() if isinstance(v, int) and not isinstance(v, bool) and re.search(r'quota|max_?per|maxper|per_?wallet|per_?address|limit|max_?batch|max_?mint|cap\b|_cap|max_?tx', k, re.I)}
    p['daily_limit'] = bool(re.search(r'1\s*days|86400|/\s*1 days|daily|today', code))
    # 开池条件
    p['open_conditions'] = {k: v for k, v in views.items() if re.search(r'open|launch|target|max_?shares|max_?mints|deadline|start|end_?time|finaliz|liquidity', k, re.I) and not re.search(r'pause', k, re.I)}
    lp_calls = re.findall(r'addLiquidityETH\s*\(([^;]*)\)|addLiquidity\s*\(([^;]*)\)|\.mint\s*\(\s*(address\(this\)|[\w\.\(\)]+)\s*\)', code)
    adds_lp = bool(re.search(r'addLiquidityETH\s*\(|addLiquidity\s*\(|Pair\([^)]*\)\.mint\(|pair\.mint\(|IPancakePair\(\s*\w+\s*\)\.mint\(', code))
    p['adds_lp'] = adds_lp
    lp_to = []
    for m in re.finditer(r'addLiquidityETH\s*\{[^}]*\}\s*\(([^;]*)\)|addLiquidityETH\s*\(([^;]*)\)|addLiquidity\s*\(([^;]*)\)', code):
        args = [x.strip() for x in (m.group(1) or m.group(2) or m.group(3) or '').split(',')]
        if len(args) >= 5: lp_to.append(args[-2])
    for m in re.finditer(r'(?:Pair\([^)]*\)|pair)\.mint\(\s*([^;]+?)\s*\)\s*;', code): lp_to.append(m.group(1))
    p['lp_to'] = list(dict.fromkeys(lp_to))[:4]
    if adds_lp:
        if lp_to and all(DEAD.search(x) for x in lp_to): safe.append('LP 打入黑洞')
        elif any('this' in x for x in lp_to): p['lp_held_by_contract'] = True
        elif lp_to: risk.append('LP 打给非黑洞地址：' + ','.join(lp_to)[:60])
    # 特权函数：能否动 BNB / LP / 任意代币
    priv_fns = {n: fl for n, fl in fns.items() if any(is_priv(f) for f in fl)}
    p['privileged_functions'] = sorted(priv_fns)[:30]
    tracker = None
    for n in pay_names:
        m = re.search(r'(\w+)\s*\+=\s*msg\.value', body_with_helpers(fns, n))
        if m: tracker = m.group(1); break
    owner_take = []
    for n, fl in priv_fns.items():
        b = body_with_helpers(fns, n)
        if SEND.search(b) or re.search(r'address\(this\)\.balance', b):
            if tracker and re.search(r'balance\s*-\s*' + tracker, b): safe.append(f'{n} 只能提超出押金部分')
            else: owner_take.append(n)
        if re.search(r'\b(pair|lp\w*)\b[^;]*\.transfer\(|IERC20\(\s*\w+\s*\)\.(safe)?[tT]ransfer\(|rescue|sweep|recover', b, re.I) and n not in owner_take:
            owner_take.append(n)
    if owner_take: risk.append('owner/特权可提走资金：' + ','.join(owner_take)[:80])
    else: safe.append('无特权提币/rescue/sweep 函数')
    if not re.search(r'\bOwnable\b|\bowner\b', code): safe.append('无 owner')
    # BNB 去向（入口函数里给谁转了 BNB）
    bnb_to = []
    for n in pay_names:
        b = body_with_helpers(fns, n)
        for m in re.finditer(r'(?:payable\()?\s*([\w\.\(\)\[\]]+?)\)?\s*\.(?:call\{\s*value\s*:\s*([^}]+)\}|transfer\(([^)]*)\)|send\(([^)]*)\))', b):
            to = m.group(1); amt = (m.group(2) or m.group(3) or m.group(4) or '').strip()
            if re.search(r'IERC20|token|agc|pair|wbnb|weth', to, re.I) and m.group(3) is not None and ',' in (m.group(3) or ''): continue
            to = re.sub(r'^payable\(', '', to); bnb_to.append(f'{to} ← {amt}'[:80])
        for m in re.finditer(r'_send\w*\(\s*([^,]+),\s*([^)]+)\)', b): bnb_to.append(f'{m.group(1).strip()} ← {m.group(2).strip()}'[:80])
    p['bnb_to'] = list(dict.fromkeys(bnb_to))[:8]
    bps = {k: v for k, v in views.items() if isinstance(v, int) and not isinstance(v, bool) and re.search(r'treasury|team|dev(?!iation)|marketing|fee', k, re.I) and re.search(r'bps|rate|percent|pct|share|fee', k, re.I) and v <= 10000}
    for m in re.finditer(r'uint\d*\s+(?:public\s+|private\s+|internal\s+)*constant\s+(\w*(?:TREASURY|TEAM|DEV|MARKETING)\w*(?:BPS|RATE|PCT|SHARE|FEE)\w*)\s*=\s*([\d_]+)\s*;', code, re.I):
        if 'DEVIATION' not in m.group(1).upper(): bps.setdefault(m.group(1), int(m.group(2).replace('_', '')))
    if bps: p['treasury_bps_views'] = bps
    to_owner = [x for x in bnb_to if re.search(r'owner|treasury|dev|team|marketing|feeTo|fee_?recipient|wallet|project|payee|beneficiary|vault|fund|receiver', x, re.I)]
    if to_owner: p['bnb_to_project'] = to_owner
    if payable and (to_owner or re.search(r'\b(treasury|feeTo|feeRecipient|devWallet|teamWallet|marketingWallet|payee|beneficiary)\b', body_with_helpers(fns, pay_names[0]) if pay_names else '', re.I)):
        tb = [v for k, v in (bps or {}).items() if re.search(r'treasury|team|dev(?!iation)|marketing', k, re.I)]
        if not tb and tbps is not None: tb = [tbps]
        if tb and max(tb) <= 3000: safe.append(f'项目方抽成 {max(tb)/100:.0f}%（≤30%）')
        elif tb: risk.append(f'项目方抽成 {max(tb)/100:.0f}%（>30%）')
        else: risk.append('付款（部分或全部）直接进项目方地址，比例未能从源码读出')
    # 押金退还
    refund_fns = [f['name'] for f in abif if REFUND_NAME.search(f['name']) and f.get('stateMutability') == 'nonpayable' and not any(is_priv(x) for x in fns.get(f['name'], []))]
    refund = {'functions': refund_fns, 'tracker': tracker, 'equals_deposit': None}
    for n in refund_fns:
        b = body_with_helpers(fns, n)
        if not SEND.search(b): continue
        if tracker:
            m = re.search(re.escape(tracker) + r'\s*-=\s*(\w+)', b)
            if m:
                v = m.group(1)
                sent_same = bool(re.search(r'(_send\w*|sendValue|safeTransferETH)\(\s*[\w\.]+\s*,\s*' + v + r'\s*\)|value\s*:\s*' + v + r'\b|\.transfer\(\s*' + v + r'\s*\)', b))
                cut = bool(re.search(v + r'\s*(\*|-)\s*\w*(fee|tax|bps|rate|penalty)', b, re.I) or re.search(r'(fee|tax|penalty)\w*\s*=\s*' + v, b, re.I))
                refund['equals_deposit'] = sent_same and not cut
                refund['refund_var'] = v; refund['via'] = n
                # 押金 = 份数×单价 双向核对
                dm = re.search(v + r'\s*=\s*(\w+)\s*\*\s*(\w+)', b)
                if dm: refund['refund_expr'] = f'{dm.group(1)} * {dm.group(2)}'
                break
        if refund['equals_deposit'] is None and re.search(r'\.(deposit|amount|paid|value)\b', b) and not re.search(r'fee|tax|penalty', b, re.I):
            refund['equals_deposit'] = 'probable'; refund['via'] = n
    if refund_fns and refund.get('via'):
        for n in [refund['via']]:
            b = body_with_helpers(fns, n) + ''.join(f['mods'] for f in fns.get(n, []))
            if re.search(r'whenNotPaused|require\(\s*!\s*paused|paused\s*\(\)\s*\)|if\s*\(\s*paused', b): risk.append('暂停会卡住退款/领取')
    owed = views.get(tracker) if tracker else None
    if isinstance(owed, int):
        refund['owed'] = owed / 1e18
        if bal >= owed: safe.append(f'合约 BNB {bal/1e18:.4f} ≥ 应退押金 {owed/1e18:.4f}')
        else: risk.append(f'合约 BNB {bal/1e18:.4f} < 应退押金 {owed/1e18:.4f}')
    delay = {k: v for k, v in views.items() if isinstance(v, int) and re.search(r'delay|lock|duration|period|vest', k, re.I)}
    if delay: refund['delay_views'] = delay
    p['refund'] = refund
    # 暂停
    pause_fns = [n for n in fns if re.search(r'^(pause|setPaused|togglePause|setPause|stop|emergencyStop)', n)]
    if pause_fns:
        p['pause_functions'] = pause_fns
        if re.search(r'PAUSE_DURATION|PAUSE_COOLDOWN|pausedUntil', code): safe.append('暂停有时限/冷却（只停新铸造）' if '暂停会卡住退款/领取' not in risk else '暂停有时限')
    # 升级
    up, why = upgradeable(rpc, a)
    if up: risk.append('可升级：' + why)
    elif up is False: safe.append('不可升级')
    if impl: p['proxy_impl'] = impl
    # 邀请参数
    refs = sorted({f"{f['name']}.{i['name']}" for f in abif for i in f.get('inputs', []) if i.get('name') and REF_ARG.match(i['name'])})
    # mint 出的代币
    toks = {}
    for k, v in views.items():
        if isinstance(v, str) and re.fullmatch(r'0x[0-9a-f]{40}', v.lower() if v else '') and int(v, 16) and not re.search(r'router|wbnb|weth|pair|registr|owner|pauser|treasury|dev|usdt|factory|admin|quote|feed|oracle|charity|signer|dead|marketing|wallet|nft|agentid', k, re.I):
            sym = dec('string', call_raw(v, 'symbol()'))
            if sym: toks[k] = (v.lower(), sym)
    self_sym = dec('string', call_raw(a, 'symbol()'))
    token = None
    if self_sym and re.search(r'function\s+_mint\s*\(|\b_mint\s*\(', code): token = (a, self_sym)
    elif toks:
        pri = [k for k in toks if re.search(r'\b' + re.escape(k) + r'\s*(\(\s*\))?\s*\.\s*mint\s*\(|\(\s*' + re.escape(k) + r'\s*\)\s*\.\s*mint\s*\(', code)]
        pri += [k for k in toks if re.search(r'token|reward', k, re.I) and k not in pri]
        if pri: token = toks[pri[0]]
    mints_token = bool(re.search(r'\.mint\s*\(\s*msg\.sender|\b_(safe)?[mM]int\s*\(\s*(msg\.sender|to|_to|recipient|account|user|agent)', code)) or bool(token)
    if token:
        p['token'] = {'address': token[0], 'symbol': token[1]}
        if token[0] != a:
            s2, r2, info = token_check(token[0], a); safe += s2; risk += r2; p['token'].update({k: info.get(k) for k in ('flags', 'owner', 'pair', 'tax') if info.get(k) is not None})
        name = f'{name or ""} ({token[1]})'.strip()
    elif self_sym: name = f'{name or ""} ({self_sym})'.strip()
    token_pay = [n for n in free_entries if re.search(r'transferFrom\(\s*msg\.sender', body_with_helpers(fns, n), re.I)]
    if token_pay:
        p['pay_in_erc20'] = token_pay
        if isinstance(views.get('firstPrice'), int) and views.get('firstPrice') == 0: p['first_share_free'] = True
        if tbps is not None: (safe.append(f'项目方抽成 {tbps/100:.0f}%（≤30%）') if tbps <= 3000 else risk.append(f'项目方抽成 {tbps/100:.0f}%（>30%）'))
        if re.search(r'transferFrom\(\s*msg\.sender\s*,\s*(treasury|owner|dev|team|feeTo)', ''.join(body_with_helpers(fns, n) for n in token_pay), re.I):
            risk.append('代币付款直接进项目方 treasury')
    claim_fns = [f['name'] for f in abif if f.get('stateMutability') == 'nonpayable' and re.search(r'claim|harvest|withdraw|redeem|unlock', f['name'], re.I) and not any(is_priv(x) for x in fns.get(f['name'], []))]
    p['claim_functions'] = claim_fns

    # ---------- 归类 ----------
    cat = 'unknown'
    if payable and refund_fns and tracker and refund.get('equals_deposit') in (True, 'probable'):
        cat = 'refund_deposit'
    elif payable and adds_lp and (p['open_conditions'] or re.search(r'open|launch|target', code, re.I)):
        cat = 'fair_launch'
    elif (payable or token_pay) and mints_token:
        cat = 'paid_mint'
    elif free_entries and mints_token:
        cat = 'free_mint'
    if cat == 'refund_deposit' and refund.get('equals_deposit') is True: safe.append('退款=押金（同一变量全额退回，无扣费）')
    if cat == 'refund_deposit' and refund.get('equals_deposit') == 'probable': risk.append('退款金额无法从源码确认等于押金')
    if cat == 'refund_deposit' and refund.get('equals_deposit') is False: risk.append('退款≠押金（有扣费或另算）')
    hard = [r for r in risk if re.search(r'提走|可升级|增发|税>10|LP 打给|< 应退|退款≠|卡住退款|>30%', r)]
    if hard: concl = 'reject'
    elif cat == 'unknown' or risk: concl = 'review'
    elif cat in ('paid_mint', 'fair_launch', 'refund_deposit') and not (price or vexpr or token_pay): concl = 'review'
    else: concl = 'pass'
    rm_ok = None
    if cat == 'refund_deposit':
        code_hex = (rpc('eth_getCode', [a, 'latest']) or '').lower()
        need = [sel(s) for s in ('mintFree(uint256,address)', 'claimFree(uint256[])', 'quota(address,uint256)', 'refundOwed()', 'REFUND_DELAY()', 'PRICE_PER_SHARE()')]
        p['refundmine_interface'] = all(x in code_hex for x in need)
        rm_ok = concl == 'pass' and vinfo.get('verified') is True and not impl and isinstance(owed, int) and bal >= owed
    return {'status': 'analyzed', 'name': name, 'contract': a, 'category': cat, 'params': p, 'safe_flags': list(dict.fromkeys(safe)),
            'risk_flags': list(dict.fromkeys(risk)), 'conclusion': concl, 'referral_params': refs, 'website': urls[:3], 'refundmine_ok': rm_ok,
            'verify_source': srcref, 'source_from': fetch_from, 'bscscan': f'https://bscscan.com/address/{a}#code'}


def run_one(a, heat_n=None, srcs=None):
    v = verify(a)
    if v.get('verified') is None: return {'status': 'verify_unknown', 'why': v.get('source')}
    if v.get('verified') is not True: return {'status': 'unverified', 'why': v.get('source')}
    try: r = analyze(a, v)
    except Exception as e: r = {'status': 'analyze_err', 'why': str(e)[:160]}
    r['heat_wallets_30m'] = heat_n; r['sources'] = srcs; r['analyzed_cst'] = now_cst()
    return r


def main():
    args = sys.argv[1:]
    if '--test' in args:
        for a in [x.lower() for x in args if x.startswith('0x')]:
            print(json.dumps(run_one(a), ensure_ascii=False, indent=1))
        return
    st = load(ST, {}); outd = load(OUT, {'contracts': {}}); res = outd.setdefault('contracts', {})
    now = time.time(); cands = candidates()
    todo = []
    for a in cands:
        e = st.get(a) or {}
        s, t = e.get('status'), e.get('t', 0)
        if s == 'analyzed' and now - t < RE_ANALYZE: continue
        if s in ('unverified', 'source_unavailable') and now - t < RE_UNVER: continue
        if s == 'cold' and now - t < RE_COLD: continue
        todo.append(a)
    hm = heat(todo)
    stats = {'time_cst': now_cst(), 'candidates': len(cands), 'checked_heat': len(todo), 'hot': 0, 'analyzed': 0, 'heat_read_fail': hm is None}
    if hm is None:
        outd['last_run'] = stats; dump(OUT, outd); return
    hot = []
    for a in todo:
        n = hm.get(a)
        if n is None: continue          # 本轮读失败：不记冷，下轮再看
        if n < HEAT_MIN: st[a] = {'status': 'cold', 't': now, 'heat': n}
        else: hot.append((n, a))
    hot.sort(reverse=True); stats['hot'] = len(hot); dump(ST, st)
    for n, a in hot:
        if stats['analyzed'] >= MAX_ANALYZE or time.time() > DEADLINE: break
        r = run_one(a, n, cands.get(a)); stats['analyzed'] += 1
        prev = st.get(a) or {}
        if r['status'] == 'verify_unknown':  # 查询失败 ≠ 未验证：下轮重查
            continue
        st[a] = {'status': r['status'] if r['status'] in ('analyzed', 'unverified', 'source_unavailable') else 'unverified', 't': time.time(),
                 'category': r.get('category'), 'conclusion': r.get('conclusion'), 'heat': n}
        if r['status'] != 'analyzed':
            if r['status'] == 'analyze_err': st[a]['status'] = 'source_unavailable'
            continue
        old = res.get(a) or {}
        res[a] = r
        if old.get('conclusion') != r['conclusion'] or old.get('category') != r['category']:
            ev = {'time_cst': now_cst(), 'contract': a, 'name': r.get('name'), 'category': r['category'], 'conclusion': r['conclusion'],
                  'prev': ({'category': old.get('category'), 'conclusion': old.get('conclusion')} if old else None), 'heat_wallets_30m': n,
                  'risk_flags': r['risk_flags'], 'safe_flags': r['safe_flags'], 'referral_params': r['referral_params'], 'refundmine_ok': r.get('refundmine_ok'),
                  'website': r.get('website'), 'bscscan': r['bscscan']}
            open(EV, 'a').write(json.dumps(ev, ensure_ascii=False) + '\n'); print(json.dumps(ev, ensure_ascii=False), flush=True)
        outd['refundmine_candidates'] = sorted(x for x, r in res.items() if r.get('category') == 'refund_deposit' and r.get('refundmine_ok'))
        dump(OUT, outd); dump(ST, st)
    # 2026-11-03 03:21 统一付费自动买：每轮重读已识别合约的进度；≥60%、未开池、结论 pass、付费类 → 后台拉起 autofill.py（全部闸门在 autofill 里查；本脚本自己仍不签名不交易）
    if time.time() < DEADLINE + 40:
        fresh = refresh_progress(res); stats['progress_refreshed'] = len(fresh)
        hot60 = [a for a, r in fresh if r['params']['progress']['pct'] >= 60 and r['params']['progress']['pct'] <= 100 and r.get('category') in ('paid_mint', 'fair_launch')
                 and r.get('conclusion') == 'pass']
        trig = load('discover_out/newtpl_autofill_trig.json', {})
        hot60 = [a for a in hot60 if time.time() - trig.get(a, 0) >= 300]
        if hot60 and '--no-autofill' not in sys.argv:
            for a in hot60: trig[a] = time.time()
            dump('discover_out/newtpl_autofill_trig.json', trig); stats['autofill_triggered'] = hot60
            subprocess.Popen(['bash', '-c', f'cd {D} && timeout 900 python3 autofill.py > discover_out/autofill_newtpl.json 2>&1'], start_new_session=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    outd['last_run'] = stats
    outd['refundmine_candidates'] = sorted(a for a, r in res.items() if r.get('category') == 'refund_deposit' and r.get('refundmine_ok'))
    dump(OUT, outd); dump(ST, st)


if __name__ == '__main__':
    main()
