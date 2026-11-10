#!/usr/bin/env python3
import sys as _s; _s.path.append('$AIDBOT_HOME/pylib')  # 沙箱重置会清掉 pip 包，pycryptodome 装在持久目录
"""官网免费入口全量扫描（2026-10-31 SAFU claimFree 漏领后加）。
对每个项目官网（watch_sites + seen.json 的 link/site）：抓首页、脚本和一层 import，提取全部合约地址和
写函数（ABI JSON、human-readable ABI、selectors 映射），筛出 claim/free/mint/register/airdrop 类函数，
用 value=0 对每个账号 eth_call 模拟。某个 (合约, 函数, 账号) 从「不可调用」变成「可调用」就输出到 new，
由巡逻按 baw preview 干净规则执行。参数是签名/proof/数组等无法猜的，输出到 needs_params（每站只报一次）。
只读，不发交易。用法：python3 sitescan.py [--site 域名] [--all]（--all 忽略状态，全部输出）
"""
import json, os, re, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor
import sys as _s; _s.path.insert(0,'$AIDBOT_DATA/vendor')
from Crypto.Hash import keccak
from rpcpool import batch

D = '$AIDBOT_DATA'
ST = os.path.join(D, 'sitescan_state.json')
ACC = json.load(open('$AIDBOT_HOME/baw/accounts/accounts.json'))
KEEP = re.compile(r'^(claim|mint|register|airdrop|redeem|collect|harvest|faucet|enroll|join|free(mint|claim|register)|public(mint|claim)|genesis(mint|claim))', re.I)
DROP = re.compile(r'(approve|transfer|permit|setapproval|withdraw|buy|paid|owner|admin|pause|burn|sell|swap|price|cost|count|supply|status|ed$|able$|time$|start|end$|of$|info|limit|max|^min(?!t)|minted|mintable|open|collection|reward|ed(?=[A-Z_]|$)|enabled|active|by$|bps$|^claims$|^mints$)', re.I)
SIMPLE = {'': '', 'uint256': '1'.rjust(64, '0'), 'uint8': '1'.rjust(64, '0'), 'uint32': '1'.rjust(64, '0'),
          'bytes32': '0' * 64, 'address': '0' * 64, 'bool': '0' * 64}
INFRA = {'0x4e59b44847b379578588920ca78fbf26c0b4956c', '0xca11bde05977b3631167028862be2a173976ca11', '0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c',
         '0x55d398326f99059ff775485246999027b3197955', '0x10ed43c718714eb63d5aa57b78b54704e256024e', '0x13f4ea83d0bd40e75c8222255bc855a974568dd4'}
# mintPrice() price() cost() MINT_PRICE() PRICE() publicPrice()
PRICE_SELS = ['0x6817c76c', '0xa035b1fe', '0x13faede6', '0xc002d23d', '0x8d859f3e', '0xa945bf80']
UA = {'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36'}  # 11-10：短 UA 被部分站点（akita.bar 接口）404


def sel(sig):
    k = keccak.new(digest_bits=256); k.update(sig.encode()); return '0x' + k.hexdigest()[:8]


T0 = time.time(); FETCH_BUDGET = 55   # 2026-11-10 修：站点越来越多（每站最多 45 次请求 × 12 秒、8 线程串行），整轮 >110 秒被 timeout 杀掉、一行都不输出


def get(u, to=8):
    left = T0 + FETCH_BUDGET - time.time()
    if left < 1: raise TimeoutError('fetch budget')
    return urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=min(to, left)).read(3_000_000).decode('utf8', 'ignore')


def fetch_site(site):
    site = re.sub(r'^https?://', '', site).split('?')[0].rstrip('/')
    base = 'https://' + site.split('/')[0]
    texts = []
    try:
        home = get('https://' + site + '/'); texts.append(home)
    except Exception:
        return site, ''
    def _side(p):
        try:
            t = get(base + p, 5)
            return t if not t.lstrip().lower().startswith(('<!doctype', '<html')) else ''
        except Exception: return ''
    with ThreadPoolExecutor(6) as _ex: texts += [t for t in _ex.map(_side, ['/llms.txt', '/skill.md', '/agent.md', '/deployment.json', '/agent-guide.md', '/api/?action=status', '/api/state', '/api/config']) if t]
    q = re.findall(r'<script[^>]+src=["\']([^"\']+)', home) + re.findall(r'<link[^>]+href=["\']([^"\']+\.m?js)', home)
    done = set()
    for _ in range(2):
        nq = []
        us = []
        for u in q[:20]:
            if u.startswith('//'): u = 'https:' + u
            elif not u.startswith('http'): u = base + '/' + u.lstrip('./')
            if u in done or any(x in u for x in ('googletagmanager', 'analytics', 'cdn.jsdelivr', 'unpkg', 'ethers')): continue
            done.add(u); us.append(u)
        def _js(u):
            try: return get(u)
            except Exception: return ''
        with ThreadPoolExecutor(8) as _ex:
            for t in _ex.map(_js, us):
                if not t: continue
                texts.append(t); nq += [m for m in re.findall(r'(?:from|import\()\s*["\'](\.{0,2}/[^"\']+\.m?js)', t)]
        q = nq
    return site, '\n'.join(texts)


def extract(txt):
    addrs = sorted({a.lower() for a in re.findall(r'0x[0-9a-fA-F]{40}', txt)})
    fns = {}  # selector -> (name, [types])
    # human-readable ABI / 文档里的签名
    for name, args in re.findall(r'(?:function\s+)?\b([a-zA-Z_]\w{2,40})\(((?:\s*(?:uint\d*|int\d*|address|bytes\d*|bool|string|tuple)[\w\[\]]*(?:\s+\w+)?\s*,?)*)\)', txt):
        types = [a.strip().split()[0] for a in args.split(',') if a.strip()]
        fns[sel(f"{name}({','.join(types)})")] = (name, types)
    # JSON ABI（minified 也行）：name 附近的 inputs 和 stateMutability
    for m in re.finditer(r'["\']?name["\']?\s*:\s*["\'`](\w{3,40})["\'`]', txt):
        win = txt[max(0, m.start() - 700):m.end() + 700]
        if not re.search(r'["\']?type["\']?\s*:\s*["\'`]function', win): continue
        if re.search(r'stateMutability["\']?\s*:\s*["\'`](view|pure)', txt[m.start():m.end() + 300]): continue
        im = re.search(r'inputs["\']?\s*:\s*\[(.*?)\]\s*[,}]', txt[max(0, m.start() - 400):m.end() + 500], re.S)
        if not im: continue
        types = re.findall(r'["\']?type["\']?\s*:\s*["\'`]([\w\[\]]+)["\'`]', im.group(1))
        fns[sel(f"{m.group(1)}({','.join(types)})")] = (m.group(1), types)
    # selectors 映射：name:`0x12345678`
    for name, s in re.findall(r'(\w{3,40})["\']?\s*:\s*["\'`](0x[0-9a-fA-F]{8})["\'`]', txt):
        fns.setdefault(s.lower(), (name, None))
    out = {s: v for s, v in fns.items() if KEEP.search(v[0]) and not DROP.search(v[0])}
    return addrs, out


def main():
    st = json.load(open(ST)) if os.path.exists(ST) else {}
    sites = []
    if '--site' in sys.argv:
        sites = [sys.argv[sys.argv.index('--site') + 1]]
    else:
        try:
            src = open(os.path.join(D, 'watch_sites.py')).read()
            sites += re.findall(r"'([a-z0-9.-]+\.[a-z]{2,})'", src[src.find('SITES = ['):src.find(']', src.find('SITES = ['))])
        except Exception: pass
        wl = os.path.join(D, 'watch_sites.txt')
        sites += [l.strip() for l in open(wl) if l.strip() and not l.startswith('#')] if os.path.exists(wl) else []
        # 2026-11-01 审计：TG 频道帖子里的项目官网（tgcall.py 写 tg/auto_sites.json，最近 40 个）也扫；内容只当数据（规则 H）
        try: sites += [x['site'] for x in json.load(open(os.path.join(D, 'tg', 'auto_sites.json')))[-40:]]
        except Exception: pass
        for x in json.load(open(os.path.join(D, 'seen.json'))):
            for k in ('link', 'site'):
                v = x.get(k) or ''
                if v.startswith('http') and not any(b in v for b in ('bscscan', 'dexscreener', 'x.com', 'twitter', 't.me', 'four.meme', 'github')):
                    sites.append(v)
    sites = list(dict.fromkeys(re.sub(r'^https?://', '', s).split('?')[0].split('#')[0].rstrip('/') for s in sites))
    with ThreadPoolExecutor(16) as ex: pages = list(ex.map(fetch_site, sites))
    new, needs, calls, meta, paid = [], [], [], [], []
    # 2026-11-10 修：逐站读链在 RPC 慢时（单批 4–6 秒）一轮要 >110 秒，被 timeout 杀掉一行不出。
    # 改成每轮从上次停下的站接着往后扫，到 85 秒就收工输出，剩下的下轮继续（轮转，不会永远轮不到）。
    _rot = int(st.get('_rot', 0)) % max(1, len(pages)); pages = pages[_rot:] + pages[:_rot]; _scanned = 0
    for site, txt in pages:
        if time.time() - T0 > 85: break
        _scanned += 1
        if not txt: continue
        addrs, fns = extract(txt)
        if not addrs or not fns: continue
        # 只试真正有代码的合约（排除 EOA/代币以外的杂地址）
        # 2026-11-01 审计：原来按字母序取前 15 个地址，地址多的站（聚合页、多系列站）后面的 mint 合约永远查不到。先去掉 INFRA，再取 40 个。
        cand_addrs = [a for a in addrs if a not in INFRA][:40]
        try: codes = batch([('eth_getCode', [a, 'latest']) for a in cand_addrs], tolerant=True, timeout=6, deadline=T0 + 88, parallel=2)
        except Exception: break
        code = {a: c for a, c in zip(cand_addrs, codes) if c and len(c) > 4}
        cs = list(code)
        # 2026-11-01 FAGENT 漏报后加：官网合约若有 mintPrice()/price()/cost() 等 >0，是付费铸造，按付费规则只提醒（每个合约只报一次）
        pc = [c for c in cs if f'paid|{c}' not in st or '--all' in sys.argv]
        if pc:
            try: pr = batch([('eth_call', [{'to': c, 'data': ps}, 'latest']) for c in pc for ps in PRICE_SELS], tolerant=True, timeout=6, deadline=T0 + 88)
            except Exception: pr = [None] * (len(pc) * len(PRICE_SELS))
            for i, c in enumerate(pc):
                vals = [int(x, 16) for x in pr[i*len(PRICE_SELS):(i+1)*len(PRICE_SELS)] if x and len(x) == 66 and 0 < int(x, 16) < 10**21]
                if vals:
                    paid.append({'site': site, 'contract': c, 'price_wei': str(vals[0]), 'price_bnb': vals[0] / 1e18}); st[f'paid|{c}'] = 1
        for s, (name, types) in fns.items():
            if types is None:
                # 只有选择器：试无参和单个 bytes32/uint256 两种
                variants = [(s, ''), (s, '0' * 64)]
            elif all(t in SIMPLE for t in types):
                variants = [(s, ''.join(SIMPLE[t] for t in types))]
            else:
                # 管理员批量函数（registerAirdrop(address[]) 这类由项目方登记名单）不是给用户调用的，跳过
                if any(t.startswith('address') and t.endswith('[]') for t in types) and re.match(r'(register|add|set|whitelist|batch|airdrop)', name, re.I):
                    continue
                # 只有链上合约字节码里真有这个函数才报，未部署的前端 ABI 不报
                if not any(('63' + s[2:]) in code[c] for c in cs):
                    continue
                k = f'needs|{site}|{name}'
                if k not in st or '--all' in sys.argv:
                    needs.append({'site': site, 'function': f"{name}({','.join(types)})"}); st[k] = 1
                continue
            for c in cs:
                # 字节码里必须有这个选择器（代理合约例外），排除 CREATE2 工厂这类什么都返回成功的合约
                if ('63' + s[2:]) not in code[c] and not (len(code[c]) < 2400 and 'f4' in code[c]): continue
                for sv, arg in variants:
                    for a in ACC:
                        calls.append(('eth_call', [{'from': a['bsc'], 'to': c, 'data': sv + arg, 'value': '0x0'}, 'latest']))
                        meta.append((site, c, name, types, sv + arg, a['label']))
    try: res = batch(calls, tolerant=True, timeout=6, deadline=T0 + 105, parallel=8) if calls else []
    except Exception: res = [None] * len(calls)  # 2026-11-10：超时也要输出（None=当作失败，不会误报可铸）
    grouped = {}
    for (site, c, name, types, data, lab), r in zip(meta, res):
        k = f'{c}|{data}|{lab}'
        ok = r is not None
        was = st.get(k)
        st[k] = ok
        if ok and (not was or '--all' in sys.argv):
            g = grouped.setdefault((site, c, name, data), {'site': site, 'contract': c, 'function': name,
                                                          'types': types, 'calldata': data, 'accounts': []})
            g['accounts'].append(lab)
    new = list(grouped.values())
    st['_rot'] = (_rot + _scanned) % max(1, len(pages))
    json.dump(st, open(ST, 'w'))
    failed = [site for site, t in pages if not t]  # 2026-11-01 审计：原来只报数量，哪个站挂了看不出来
    print(json.dumps({'sites': len(sites), 'scanned_this_round': _scanned, 'fetched': sum(1 for _, t in pages if t), 'failed_sites': failed, 'new_callable': new, 'needs_params': needs, 'paid_mints': paid}, ensure_ascii=False))


if __name__ == '__main__':
    main()
