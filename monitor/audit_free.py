# 漏打审计：对近期 Agent ID 相关合约，用五个号逐一 eth_call 模拟各种免费铸造写法（含带自己 Agent ID 编号），列出能免费铸、我们却还没铸的。只读。
import json, sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rpcpool import batch, rpc
from guard import agent_id
A = json.load(open('$AIDBOT_HOME/baw/accounts/accounts.json'))
st = json.load(open('chainscan/state.json')); f = json.load(open('fairmint/found.json'))
toks = []  # 旧方法 getLogs 公共节点常拒，改用 scan.py 记录的 targets
for t, tg in st.get('targets', {}).items(): pass
head = int(rpc('eth_blockNumber', []), 16)
T0 = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'
# 找铸造入口：最近 6000 块内从 0 地址转出的那笔交易的 to
logs = batch([('eth_getLogs', [{'address': t, 'fromBlock': hex(head - 6000), 'toBlock': 'latest', 'topics': [T0, '0x' + '0' * 64]}]) for t in toks], tolerant=True, chunk=10)
txh = {}
for t, L in zip(toks, logs):
    if L: txh[t] = L[-1]['transactionHash']
txs = batch([('eth_getTransactionByHash', [h]) for h in txh.values()], tolerant=True)
targets = {}
for t, tx in zip(txh, txs):
    if tx and tx.get('to'): targets.setdefault(tx['to'].lower(), set()).add(t)
for c in f: targets.setdefault(c.lower(), set())
for t, tg in st.get('targets', {}).items(): targets.setdefault(tg.lower(), set()).add(t)
print('recent active tokens', len(txh), 'targets', len(targets), file=sys.stderr)
MAX = 'f' * 64; w = lambda x: hex(x)[2:].rjust(64, '0')
def forms(a):
    aid = agent_id(a['agentId']); me = a['bsc'][2:].lower().rjust(64, '0')
    return [('mint()', '0x1249c58b'), ('mint(1)', '0xa0712d68' + w(1)), ('claim()', '0x4e71d92d'), ('freeMint()', '0x5b70ea9f'),
            ('mint(1,aid,ref)', '0x02acc94b' + w(1) + aid + MAX), ('mint(aid)', '0xa0712d68' + aid), ('claim(aid)', '0x379607f5' + aid),
            ('mint(me)', '0x6a627842' + me), ('agentMint()', '0x1be3587a'), ('publicMint()', '0x26092b83')]
codes = dict(zip(targets, batch([('eth_getCode', [c, 'latest']) for c in targets], tolerant=True)))
calls, meta = [], []
for c in targets:
    code = codes.get(c) or ''
    if len(code) < 10: continue
    for a in A:
        for n, d in forms(a):
            if d[2:10] not in code: continue
            calls.append(('eth_call', [{'from': a['bsc'], 'to': c, 'data': d, 'value': '0x0'}, 'latest'])); meta.append((c, a['label'], n, d))
res = batch(calls, tolerant=True, chunk=30)
hits = {}
for (c, l, n, d), r in zip(meta, res):
    if r is not None: hits.setdefault(c, {}).setdefault(l, []).append((n, d))
# 已持有就算打过：看 targets 对应 token 或合约自身 balanceOf
out = []
for c, by in hits.items():
    tk = list(targets[c]) or [c]
    for l, fl in by.items():
        a = next(x for x in A if x['label'] == l)
        bals = batch([('eth_call', [{'to': t, 'data': '0x70a08231' + a['bsc'][2:].lower().rjust(64, '0')}, 'latest']) for t in tk + [c]], tolerant=True)
        held = any(b and len(b) >= 66 and int(b[:66], 16) > 0 for b in bals)
        out.append({'contract': c, 'tokens': tk, 'acct': l, 'held': held, 'forms': [x[0] for x in fl], 'data': fl[0][1]})
print(json.dumps(out, ensure_ascii=False))

if '--mint' in sys.argv:
    import subprocess
    from concurrent.futures import ThreadPoolExecutor
    todo = [x for x in out if not x['held']]
    vs = subprocess.run(['python3', 'verify_source.py'] + sorted({x['contract'] for x in todo}), capture_output=True, text=True, timeout=200).stdout.splitlines()
    ok = {json.loads(l)['addr'].lower() for l in vs if l.startswith('{') and json.loads(l).get('verified') is True and json.loads(l).get('impl_verified', True) is not False}
    for x in todo:
        if x['contract'] not in ok: print('跳过(源码未验证，规则B)', x['contract'], x['acct'])
    todo = [x for x in todo if x['contract'] in ok]  # 规则 B：源码已验证才铸
    def go(x): return subprocess.run(['python3', 'mint_many.py', x['contract'], x['data'], x['acct']], capture_output=True, text=True, timeout=200).stdout.strip()
    with ThreadPoolExecutor(5) as ex:
        for r in ex.map(go, todo): print(r)

# 2026-11-02 用户「前面免费 后面付费的也不打」：混合盘免费份（discover_out/mixedfree.json 候选）一并领取，闸门同 mint_many（规则 B + preview 干净）
if '--mint' in sys.argv:
    print(subprocess.run(['python3', 'mixedfree.py', '--from-out', '--mint'], capture_output=True, text=True, timeout=115, env=dict(os.environ, MF_MINT_STOP='45')).stdout[-3000:])
