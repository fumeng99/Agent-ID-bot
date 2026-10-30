#!/usr/bin/env python3
"""链上发现 Agent ID 专属铸造（借鉴 agentid-sniper）。
每次运行：扫描自上次以来（最多最近 35 分钟）的 BSC 区块里所有 Transfer(from=0x0) 铸造事件，
维护 30 分钟滚动窗口；同一代币/NFT 被铸给 >=6 个不同钱包即为候选，再判断：
  - 代币/目标合约（含 EIP-1967/Beacon 代理实现）合约码里含 Agent ID 登记合约 => agentid=code
  - 或抽样铸造者 >=60% 持有 .ai（登记合约 balanceOf>0）=> agentid=holders
  - 抽样铸造交易的 value（0 = 免费候选）、调用的目标合约和函数选择器
输出 candidates.json（只含新候选）；不签名、不交易。
用法: python3 scan.py [--minutes 35]
"""
import json, os, sys, time, urllib.request, hashlib
D = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(D, 'state.json'); OUT = os.path.join(D, 'candidates.json')
RPCS = ['https://bsc.blockrazor.xyz', 'https://bsc-dataseed.bnbchain.org', 'https://bsc-dataseed1.defibit.io']
LOG_RANGE = 25
REGISTRY = '60b9262456de011f7d15a54cdd8c48baaa8b022f'
T_TRANSFER = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'
ZERO32 = '0x' + '0' * 64
WINDOW = 30 * 60; MIN_WALLETS = 6
IMPL_SLOT = '0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc'
BEACON_SLOT = '0xa3f0ad74e5423aebfd80d3ef4346578335a9a72aeaee59ff6cb3582b35133d50'
SKIP = {'0x60b9262456de011f7d15a54cdd8c48baaa8b022f', '0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c', '0x55d398326f99059ff775485246999027b3197955',
        '0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d', '0xe9e7cea3dedca5984780bafc599bd69add087d56'}
FREE_SELECTORS = {'0x1249c58b': 'mint()', '0x5b70ea9f': 'freeMint()', '0x4e71d92d': 'claim()', '0xa0712d68': 'mint(uint256)',
  '0x26092b83': 'publicMint()', '0x74d04cfc': 'commitFreeMint()', '0x9f92d0dd': 'registerForRewards()', '0x7c928fe9': 'freeMint(uint256)',
  '0x379607f5': 'claim(uint256)', '0x8ab53447': 'mintFree()', '0xf366afc9': 'claimFree()', '0x1be3587a': 'agentMint()'}

def rpc(m, p, tries=2):
    last = None
    for i in range(tries):
        for u in RPCS:
            try:
                r = urllib.request.Request(u, json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': m, 'params': p}).encode(),
                                           {'content-type': 'application/json', 'user-agent': 'Mozilla/5.0'})
                j = json.load(urllib.request.urlopen(r, timeout=8))
                if 'result' in j: return j['result']
                last = j.get('error')
            except Exception as e: last = str(e)
        time.sleep(0.3)
    raise RuntimeError(f'{m} failed: {last}')
call = lambda to, data: rpc('eth_call', [{'to': to, 'data': data}, 'latest'])
def code(a):
    try: return (rpc('eth_getCode', [a, 'latest']) or '0x').lower()
    except Exception: return '0x'
def impl_of(a):
    for slot in (IMPL_SLOT,):
        try:
            v = rpc('eth_getStorageAt', [a, slot, 'latest'])
            if v and int(v, 16): return '0x' + v[-40:]
        except Exception: pass
    try:
        v = rpc('eth_getStorageAt', [a, BEACON_SLOT, 'latest'])
        if v and int(v, 16):
            r = call('0x' + v[-40:], '0x5c60da1b'); return '0x' + r[-40:]
    except Exception: pass
    return None
def has_registry(a):
    c = code(a)
    if REGISTRY in c: return True
    i = impl_of(a)
    return bool(i and REGISTRY in code(i))
def holds_ai(w):
    try: return int(call('0x' + REGISTRY, '0x70a08231' + w[2:].rjust(64, '0'))[:66], 16) > 0
    except Exception: return False
def tstr(a, sel):
    try:
        r = call(a, sel)[2:]
        if len(r) >= 192: n = int(r[64:128], 16); return bytes.fromhex(r[128:128 + 2 * n]).decode('utf8', 'ignore')
        return bytes.fromhex(r[:64]).rstrip(b'\0').decode('utf8', 'ignore')
    except Exception: return ''

WBNB = '0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c'; USDT = '0x55d398326f99059ff775485246999027b3197955'
FACTORY = '0xca143ce32fe78f1f7019d7d551a6402fc5350c73'
WINNERS = ['0x8e6442663a4f8c50b6132c68d42a30e7dfe4ae15', '0x04fd77c2f97d044b3c719282fd0a0568f4254afa', '0x415aac3e46264c046d103c994a1da71f05c547f9', '0x171f264f938d55d968afc5d6b133c4157a61f05f']
BAD_SEL = {'0xa9059cbb','0x095ea7b3','0x23b872dd','0x42842e0e','0xb88d4fde','0xa22cb465','0x39509351','0xd505accf','0xf242432a','0x2eb2c2d6'}
SMART_FILE = os.path.join(D, 'smart.json')
def code_at(a, b):
    try: return (rpc('eth_getCode', [a, hex(b)]) or '0x')
    except Exception: return None
def creation_block(a, head, back=400000):
    lo, hi = max(1, head - back), head
    c = code_at(a, lo)
    if c and len(c) > 2: return None  # 早于约 2 天
    for _ in range(22):
        if hi - lo <= 1: break
        m = (lo + hi) // 2; c = code_at(a, m)
        if c is None: return 0
        if len(c) > 2: hi = m
        else: lo = m
    return hi
def deployer_of(a, blk):
    try:
        b = rpc('eth_getBlockByNumber', [hex(blk), True])
        for tx in b['transactions']:
            if not tx.get('to'):
                r = rpc('eth_getTransactionReceipt', [tx['hash']])
                if (r.get('contractAddress') or '').lower() == a: return tx['from'].lower()
        for tx in b['transactions']:
            if len(tx.get('input') or '') > 2000:
                r = rpc('eth_getTransactionReceipt', [tx['hash']])
                if any(l['address'].lower() == a for l in r.get('logs', [])) or a[2:] in json.dumps(r).lower(): return tx['from'].lower()
    except Exception: pass
    return ''
def pool_open(a):
    for q in (WBNB, USDT):
        try:
            r = call(FACTORY, '0xe6a43905' + a[2:].rjust(64, '0') + q[2:].rjust(64, '0'))
            if r and int(r, 16): return True
        except Exception: pass
    return False
def new_wallet(dep, blk, bt):
    try: return int(rpc('eth_getTransactionCount', [dep, hex(max(1, blk - int(8 * 3600 / bt)))]), 16) == 0
    except Exception: return False
def load_smart(head, bt):
    sm = json.load(open(SMART_FILE)) if os.path.exists(SMART_FILE) else {}
    if sm.get('built') and time.time() - sm['built'] < 86400: return set(sm['wallets'])
    cnt = {}
    for t in WINNERS:
        cb = creation_block(t, head, back=6000000)
        if not cb: continue
        seen = []
        for f in range(cb, cb + 3000, LOG_RANGE):
            try: logs = rpc('eth_getLogs', [{'address': t, 'fromBlock': hex(f), 'toBlock': hex(f + LOG_RANGE - 1), 'topics': [T_TRANSFER, ZERO32]}])
            except Exception: continue
            for l in logs:
                w = '0x' + l['topics'][2][-40:]
                if w not in seen: seen.append(w)
            if len(seen) >= 60: break
        for w in seen[:60]: cnt[w] = cnt.get(w, 0) + 1
    ws = [w for w, c in cnt.items() if c >= 2]
    json.dump({'built': time.time(), 'wallets': ws}, open(SMART_FILE, 'w'))
    return set(ws)

def main():
    mins = 35
    if '--minutes' in sys.argv: mins = int(sys.argv[sys.argv.index('--minutes') + 1])
    st = json.load(open(STATE)) if os.path.exists(STATE) else {}
    head = int(rpc('eth_blockNumber', []), 16)
    hb = rpc('eth_getBlockByNumber', [hex(head), False]); pb = rpc('eth_getBlockByNumber', [hex(head - 1000), False])
    bt = max(0.2, (int(hb['timestamp'], 16) - int(pb['timestamp'], 16)) / 1000); now = int(hb['timestamp'], 16)
    start = max(st.get('last', 0) + 1, head - int(mins * 60 / bt))
    toks = st.get('toks', {})  # token -> {wallet: [ts, tx]}
    errors = 0
    sys.stderr.write(f'scan {start}->{head} bt={bt}\n'); tl = time.time()
    for f in range(start, head + 1, LOG_RANGE):
        t = min(f + LOG_RANGE - 1, head)
        try: logs = rpc('eth_getLogs', [{'fromBlock': hex(f), 'toBlock': hex(t), 'topics': [T_TRANSFER, ZERO32]}])
        except Exception: errors += 1; continue
        ts = now - int((head - t) * bt)
        for l in logs:
            a = l['address'].lower()
            if a in SKIP or len(l['topics']) < 3: continue
            w = '0x' + l['topics'][2][-40:]
            if w == '0x' + '0' * 40: continue
            toks.setdefault(a, {}).setdefault(w, [ts, l['transactionHash']])
    sys.stderr.write(f'logs done {time.time()-tl:.1f}s toks={len(toks)} err={errors}\n')
    for a in list(toks):  # prune window
        toks[a] = {w: v for w, v in toks[a].items() if v[0] >= now - WINDOW}
        if not toks[a]: del toks[a]
    try: smart = load_smart(head, bt)
    except Exception: smart = set()
    done = st.get('checked', {})
    devs = st.get('devs', {}); hashes = st.get('hashes', {}); black = st.get('black', {})
    cands = []; t0 = time.time(); budget = float(os.environ.get('SCAN_BUDGET', '70')); pending = 0
    for a, ws in sorted(toks.items(), key=lambda x: -len(x[1])):
        smart_n = len(smart & set(ws))
        if (len(ws) < MIN_WALLETS and smart_n < 2) or a in done: continue
        if time.time() - t0 > budget: pending += 1; continue
        try:
            if call(a, '0x0dfe1681') not in ('0x', None) and len(call(a, '0x0dfe1681')) >= 66: done[a] = 'lp'; continue  # LP 代币
        except Exception: pass
        txs = list(dict.fromkeys(v[1] for v in ws.values()))[:4]
        targets, values, sels = {}, [], {}
        for h in txs:
            try:
                tx = rpc('eth_getTransactionByHash', [h])
                to = (tx.get('to') or '').lower(); targets[to] = targets.get(to, 0) + 1
                values.append(int(tx['value'], 16)); s = tx['input'][:10]; sels[s] = sels.get(s, 0) + 1
            except Exception: pass
        target = max(targets, key=targets.get) if targets else ''
        sample = list(ws)[:8]; ai_n = sum(holds_ai(w) for w in sample)
        reg = has_registry(a) or (target and target != a and has_registry(target))
        agentid = 'code' if reg else ('holders' if sample and ai_n / len(sample) >= 0.6 else '')
        done[a] = 'agentid' if agentid else 'no'; sys.stderr.write(f'chk {a} {len(ws)} {agentid} {time.time()-t0:.1f}s\n')
        if not agentid: continue
        sel = max(sels, key=sels.get) if sels else ''
        flags = []
        cb = creation_block(a if not target or target == a else target, head)
        tgt = target or a
        if cb is None: flags.append('老合约(>2天)')
        dep = deployer_of(tgt, cb) if cb else ''
        if pool_open(a): flags.append('已开池')
        if dep:
            nm = tstr(a, '0x06fdde03')
            lst = [x for x in devs.get(dep, []) if x[1] >= now - 86400]
            if not any(x[0] == a for x in lst): lst.append([a, now, nm])
            devs[dep] = lst
            if len({x[2] for x in lst}) >= 3: flags.append(f'重复DEV({len(lst)}个/24h)')
            if dep in black: flags.append('部署者黑名单:' + black[dep])
            if cb and new_wallet(dep, cb, bt): flags.append('新钱包部署(<8h)')
        h = hashlib.sha256(code(tgt).encode()).hexdigest()[:16]
        hl = [x for x in hashes.get(h, []) if x[1] >= now - 86400]
        if not any(x[0] == tgt for x in hl): hl.append([tgt, now])
        hashes[h] = hl
        if len(hl) >= 3: flags.append(f'同码批量部署({len(hl)}次/24h)')
        if sel in BAD_SEL: flags.append('选择器是转账/授权类，不可照抄')
        cands.append({'token': a, 'name': tstr(a, '0x06fdde03'), 'symbol': tstr(a, '0x95d89b41'), 'wallets_30m': len(ws),
                      'agentid': agentid, 'ai_holders': f'{ai_n}/{len(sample)}', 'mint_target': target, 'selector': sel,
                      'selector_name': FREE_SELECTORS.get(sel, ''), 'tx_values_wei': values, 'free_like': bool(values) and max(values) == 0,
                      'sample_txs': txs[:3], 'smart_wallets': smart_n, 'deployer': dep, 'created_block': cb, 'risk_flags': flags, 'auto_ok': not flags, 'bscscan': f'https://bscscan.com/token/{a}',
                      'found_at_cst': time.strftime('%Y-%m-%d %H:%M', time.gmtime(now + 8 * 3600))})
    hashes = {k: v for k, v in hashes.items() if any(x[1] >= now - 86400 for x in v)}
    devs = {k: v for k, v in devs.items() if any(x[1] >= now - 86400 for x in v)}
    st = {'last': head, 'toks': toks, 'checked': dict(list(done.items())[-3000:]), 'devs': devs, 'hashes': hashes, 'black': black}
    json.dump(st, open(STATE, 'w'))
    old = json.load(open(OUT)) if os.path.exists(OUT) else []
    have = {c['token'] for c in old}; merged = old + [c for c in cands if c['token'] not in have]
    json.dump(merged[-200:], open(OUT, 'w'), ensure_ascii=False, indent=1)  # 巡逻处理后自行删除已处理项
    print(json.dumps({'head': head, 'blocks': head - start + 1, 'block_time': round(bt, 3), 'log_errors': errors,
                      'tokens_in_window': len(toks), 'unchecked_left': pending, 'new_candidates': cands}, ensure_ascii=False, indent=1))
if __name__ == "__main__": main()
