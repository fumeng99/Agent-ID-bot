#!/usr/bin/env python3
import sys as _s; _s.path.append('$AIDBOT_HOME/pylib')  # 沙箱重置会清掉 pip 包，pycryptodome 装在持久目录
"""Jager 类 Agent ID 分档 fair mint（付费）发现器。只读，不交易。
原理：
 ① 铸造事件：Jager/AGI 克隆合约共用事件 Minted(address,uint256,uint256,uint256,uint256) topic0=0x19f5f791…，
    按 topic0 全链 getLogs（不限地址），新出现的合约读 costOf/opened/totalMinted 等。
 ② 已部署未开铸预警（2026-11-01，币安人漏报教训）：克隆盘的代币在构造函数里就建 PancakeV2 池子（PairCreated），
    fair mint 合约由同一部署者紧接着部署（币安人：nonce0=代币，nonce1=fair mint，相隔 17 块，开铸在 13 小时后）。
    每轮扫 PairCreated → 部署交易是合约创建的记下部署者 → 按 CREATE(部署者, nonce) 算出它前后部署的合约地址 →
    合约码（代理追到底）含 Agent ID 登记合约且含 Minted 事件/costOf 即加入观察（premint），每轮读进度，开铸立即报。
    部署者记 48 小时，之后再部署的新合约也能抓到；found.json 里已知 fair mint 的部署者同样盯（老 DEV 新盘）。
 ③ 未开池项目每轮全部批量刷新（学  v2.4.1 每分钟进度扫描：低进度暴涨不漏）；
    快到门槛（50–70%）或刚开铸/本轮暴涨的，本轮剩余时间内每 20 秒重读（学 v2.4.3），总时限 FM_DEADLINE。
 ④ 读不到开池份数时自动从合约里找「总量」函数（学 v2.5.1），结果标 open_pct_guess，只用于提醒，autofill 不据此自动买。
 ⑤ has_refund：合约（含代理实现）有无退款函数（学 v2.5.1，仅供参考，不作为买入条件）。
输出 fairmint/found.json（累积）并打印本轮结果。
用法：python3 fairmint.py [--minutes 30] [--hours 48] [--no-recheck]
"""
import calendar, json, os, sys, time, urllib.request
_s.path.insert(0, '$AIDBOT_DATA/vendor')
D = os.path.dirname(os.path.abspath(__file__)); _s.path.insert(0, os.path.dirname(D))
import codeutil as CU
T0 = time.time(); DEADLINE = T0 + float(os.environ.get('FM_DEADLINE', '100'))  # discover_all 外层 timeout 110 秒
SD = os.environ.get('FM_STATE_DIR', D)  # 测试时指向副本目录，避免吃掉巡逻的「新发现」
ST = f'{SD}/state.json'; OUT = f'{SD}/found.json'
TOPICS = ['0x19f5f791ee407773427bf7b970bbbc3375065c32edd1ab142e23a84f94b0719b']  # Minted(address,uint256,uint256,uint256,uint256)
OPENED = '0x1235c671a6b39ea1918628853eceb400b8413f8cdb8ca6110e250065affa20d3'
PAIR_CREATED = '0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9'
V2_FACTORY = '0xca143ce32fe78f1f7019d7d551a6402fc5350c73'
REG = CU.REG
QN = 'https://fluent-lively-diagram.bsc.quiknode.pro/b791d0a1526852ab53f99b98dc50390986bda4b7'  # agentai.meme 前端公开节点，允许无地址 getLogs、支持批量和历史状态
RPCS = [QN, 'https://bsc.blockrazor.xyz', 'https://bsc-rpc.publicnode.com', 'https://bsc-dataseed.bnbchain.org', 'https://bsc-dataseed1.defibit.io',
        'https://bsc-dataseed2.ninicoin.io', 'https://1rpc.io/bnb', 'https://bsc.drpc.org']
RANGE = int(os.environ.get('FM_RANGE', '3000'))
BAD = {}  # 节点冷却（学 v2.4.9 多节点轮替）：报错/限流的节点 120 秒内排到最后


def _post(u, body, to=15):
    return json.load(urllib.request.urlopen(urllib.request.Request(u, json.dumps(body).encode(), {'content-type': 'application/json', 'User-Agent': 'Mozilla/5.0'}), timeout=to))


def _nodes():
    now = time.time(); ok = [u for u in RPCS if BAD.get(u, 0) < now]
    return ok + [u for u in RPCS if u not in ok]


def rpc(m, p):
    err = None
    for i in range(3):
        for r in _nodes():
            try:
                d = _post(r, {"jsonrpc": "2.0", "id": 1, "method": m, "params": p})
                if 'result' in d: return d['result']
                err = d.get('error')
                if m == 'eth_call': return None  # revert：函数不存在，不重试
                BAD[r] = time.time() + 120
            except Exception as e: err = e; BAD[r] = time.time() + 120
        time.sleep(1.5 * (i + 1))
    raise RuntimeError(err)


def bat(calls, chunk=60):
    """批量 JSON-RPC：[(method, params)] -> [result|None]，单项 revert 返回 None。整批失败换节点。"""
    out = []
    for k in range(0, len(calls), chunk):
        part = calls[k:k + chunk]; body = [{'jsonrpc': '2.0', 'id': i, 'method': m, 'params': p} for i, (m, p) in enumerate(part)]
        res = None
        for u in _nodes():
            try:
                r = _post(u, body, 20)
                if not isinstance(r, list): raise ValueError(str(r)[:100])
                r = {x.get('id'): x for x in r}
                if any(i not in r for i in range(len(part))): raise ValueError('partial')
                if any('limit' in str(x.get('error', '')).lower() or 'rate' in str(x.get('error', '')).lower() for x in r.values()): raise ValueError('rate limited')
                res = [r[i].get('result') for i in range(len(part))]; break
            except Exception: BAD[u] = time.time() + 120
        if res is None:  # 批量全失败：逐个查（慢，但不丢）
            res = []
            for m, p in part:
                try: res.append(rpc(m, p))
                except Exception: res.append(None)
        out += res
    return out


def sel(s): return '0x' + CU.sel(s)
def call(a, data):
    try: return rpc('eth_call', [{'to': a, 'data': data}, 'latest'])
    except Exception: return None
def u(a, sig, arg=''):
    r = call(a, sel(sig) + arg)
    return int(r[:66], 16) if r and len(r) >= 66 else None
def addr(a, sig):
    r = call(a, sel(sig)); return '0x' + r[26:66] if r and len(r) >= 66 else None


ONE = '%064x' % 1
UFIELDS = [('total_minted', 'totalMinted()'), ('total_shares', 'TOTAL_SHARES()'), ('total_shares2', 'totalShares()'),
           ('total_paid_wei', 'totalPaid()'), ('open_at_shares', 'OPEN_AT()'), ('open_bnb_wei', 'OPEN_BNB()'),
           ('max_per_tx', 'MAX_PER_TX()'), ('paused', 'paused()'), ('renounced', 'renounced()')]


def _int(r): return int(r[:66], 16) if isinstance(r, str) and len(r) >= 66 else None
def _str(r):
    try: return bytes.fromhex(r[130:130 + 2 * int(r[66:130], 16)]).decode(errors='ignore') if r and len(r) > 130 else None
    except Exception: return None


def info_many(addrs, prev=None, code_cache=None):
    """批量读多个合约的 fair mint 参数（一个合约约 15 个 eth_call，合并成一次批量请求）。"""
    prev = prev or {}; code_cache = code_cache if code_cache is not None else {}
    addrs = list(dict.fromkeys(addrs)); calls = []
    for a in addrs:
        calls += [('eth_call', [{'to': a, 'data': sel('bnbQuote(uint256)') + ONE}, 'latest']), ('eth_call', [{'to': a, 'data': sel('costOf(uint256)') + ONE}, 'latest']),
                  ('eth_call', [{'to': a, 'data': sel('opened()')}, 'latest'])]
        calls += [('eth_call', [{'to': a, 'data': sel(s)}, 'latest']) for _, s in UFIELDS]
        calls += [('eth_call', [{'to': a, 'data': sel('token()')}, 'latest']), ('eth_call', [{'to': a, 'data': sel('pair()')}, 'latest'])]
        if a not in code_cache and not prev.get(a, {}).get('_code_checked'): calls.append(('eth_getCode', [a, 'latest']))
    R = bat(calls); k = 0; out = {}
    for a in addrs:
        q, c, o = _int(R[k]), _int(R[k + 1]), _int(R[k + 2]); k += 3
        i = {'contract': a, 'cost_1share_bnb': (q or c) / 1e18 if (q or c) else None, 'opened': bool(o) if o is not None else None}  # bnbQuote 优先；有些克隆用 USD1/FIST 等代币计价，costOf 是代币数量
        if q and c and c != q: i['cost_1share_token_units'] = c / 1e18
        for key, _ in UFIELDS:
            v = _int(R[k]); k += 1
            if v is not None: i[key] = v
        tk, pr = R[k], R[k + 1]; k += 2
        i['token'] = '0x' + tk[26:66] if tk and len(tk) >= 66 else None
        i['pair'] = '0x' + pr[26:66] if pr and len(pr) >= 66 else None
        if a not in code_cache and not prev.get(a, {}).get('_code_checked'):
            code_cache[a] = (R[k] or '0x').lower(); k += 1
        out[a] = i
    # 代币名称
    toks = [(a, out[a]['token']) for a in addrs if out[a].get('token') and int(out[a]['token'], 16) and not prev.get(a, {}).get('name')]
    if toks:
        R2 = bat([c for _, t in toks for c in (('eth_call', [{'to': t, 'data': sel('name()')}, 'latest']), ('eth_call', [{'to': t, 'data': sel('symbol()')}, 'latest']))])
        for j, (a, _) in enumerate(toks): out[a]['name'], out[a]['symbol'] = _str(R2[2 * j]), _str(R2[2 * j + 1])
    for a in addrs:
        i = out[a]; p = prev.get(a, {})
        if a in code_cache:  # 首次：代理追到底判断 Agent ID 专属 + 退款函数（结果缓存在 found.json，之后不再拉合约码）
            fc = CU.full_code(rpc, call, a, code_cache[a])
            i['agentid_registry_in_code'] = REG in fc; i.update(CU.refund_info(fc)); i['_code_checked'] = True
            i['_has_minted_evt'] = TOPICS[0][2:] in fc
        tm, ts = i.get('total_minted'), i.get('total_shares') or i.get('total_shares2')
        if tm is not None and ts: i['progress_pct'] = round(tm * 100 / ts, 2)
        if i.get('open_at_shares') and tm is not None: i['open_progress_pct'] = round(min(tm * 100 / i['open_at_shares'], 999), 2)
        if i.get('open_bnb_wei') and i.get('total_paid_wei'): i['open_bnb_progress_pct'] = round(i['total_paid_wei'] * 100 / i['open_bnb_wei'], 2)
        for f in ('total_paid_wei', 'open_bnb_wei'):
            if f in i: i[f.replace('_wei', '_bnb')] = i.pop(f) / 1e18
    return out


def info(a):
    return info_many([a])[a]


def guess_cap(a, v):
    """读不到 OPEN_AT/OPEN_BNB 时自动找总量函数（v2.5.1）。结果缓存 cap_sel；只作提醒参考。"""
    sold = v.get('total_minted')
    if sold is None or v.get('open_at_shares') or v.get('open_bnb_bnb'): return
    if v.get('cost_1share_bnb') is None and not v.get('_has_minted_evt'): return  # 不像 fair mint（读不到单价、码里没有 Minted 事件）不猜
    if 'cap_sel' not in v:
        if time.time() > DEADLINE - 25: return
        code = (rpc('eth_getCode', [a, 'latest']) or '0x').lower()
        sels = CU.push4_selectors(code)[:160]
        R = bat([('eth_call', [{'to': a, 'data': '0x' + s}, 'latest']) for s in sels]) if sels else []
        cand = [(s, int(r, 16)) for s, r in zip(sels, R) if isinstance(r, str) and len(r) == 66]
        cand = [(s, x) for s, x in cand if sold < x <= 10 ** 7 and x % 100 == 0]
        cand.sort(key=lambda z: (z[1] != 10000, z[1]))
        v['cap_sel'] = '0x' + cand[0][0] if cand else ''
    if v.get('cap_sel'):
        cap = _int(call(a, v['cap_sel']))
        if cap and cap > 0 and cap >= sold:
            v['cap_guess'] = cap; v['open_pct_guess'] = round(sold * 100 / cap, 2)


def open_pct(v):
    op = max(v.get('open_progress_pct') or 0, v.get('open_bnb_progress_pct') or 0)
    if op: return op, False
    return (v.get('open_pct_guess') or 0), bool(v.get('open_pct_guess'))


# ---------- ② 已部署未开铸 ----------
def predeploy(st, found, start, head):
    """返回 (新加入观察的 fair mint 列表, 其他新部署的 Agent ID 合约列表)。"""
    devs = st.setdefault('pre_devs', {}); now = time.time()
    for d in [d for d, x in devs.items() if now - x.get('at', 0) > 48 * 3600]: devs.pop(d, None)
    # 已知 fair mint 的部署者也盯（老 DEV 新盘）
    for a, v in found.items():
        dep = v.get('deployer')
        if dep and dep not in devs and not v.get('opened'): devs[dep] = {'at': now, 'next': v.get('deploy_nonce', 0) + 1, 'from_known': a}
    # PairCreated（V2 工厂，按地址过滤，公共节点也允许）
    pf = st['pre_last'] + 1 if 'pre_last' in st else head - 48000  # 首次启用回看约 6 小时，补抓已部署未开铸的盘
    pf = max(pf, head - 48000); hashes = {}
    f = pf
    while f <= head and time.time() < DEADLINE - 40:
        t = min(f + RANGE - 1, head)
        try:
            for l in rpc('eth_getLogs', [{'address': V2_FACTORY, 'topics': [PAIR_CREATED], 'fromBlock': hex(f), 'toBlock': hex(t)}]) or []:
                hashes[l['transactionHash']] = (int(l['blockNumber'], 16), '0x' + l['topics'][1][-40:], '0x' + l['topics'][2][-40:])
            f = t + 1
        except Exception: break
    st['pre_last'] = f - 1
    hs = list(hashes)
    for h, tx in zip(hs, bat([('eth_getTransactionByHash', [h]) for h in hs]) if hs else []):
        if not tx or tx.get('to'): continue  # 只看「部署交易里顺手建池」的（克隆盘代币构造函数建池）
        dep = tx['from'].lower(); n = int(tx['nonce'], 16)
        if dep not in devs: devs[dep] = {'at': now, 'next': max(0, n - 3), 'pair_tx': h, 'token': CU.create_addr(dep, n), 'block': hashes[h][0]}
    if len(devs) > 400:
        for d in sorted(devs, key=lambda d: devs[d].get('at', 0))[:len(devs) - 400]: devs.pop(d, None)
    # 部署者当前 nonce → 新部署的合约地址
    dl = list(devs); cur = bat([('eth_getTransactionCount', [d, 'latest']) for d in dl]) if dl else []
    cand = []
    for d, c in zip(dl, cur):
        if not c: continue
        c = int(c, 16); x = devs[d]; lo = x.get('next', 0)
        for n in range(lo, min(c, lo + 12)): cand.append((d, n, CU.create_addr(d, n)))
        x['next'] = min(c, lo + 12)
    cand = [z for z in cand if z[2] not in found]
    codes = bat([('eth_getCode', [z[2], 'latest']) for z in cand]) if cand else []
    new_fm, other = [], []
    for (d, n, a), code in zip(cand, codes):
        code = (code or '0x').lower()
        if len(code) <= 2: continue
        if time.time() > DEADLINE - 15: break
        fc = CU.full_code(rpc, call, a, code)
        if REG not in fc: continue
        is_fm = TOPICS[0][2:] in fc or ('63' + CU.sel('costOf(uint256)')) in fc
        row = {'contract': a, 'deployer': d, 'deploy_nonce': n, 'bscscan': f'https://bscscan.com/address/{a}'}
        if is_fm:
            i = info_many([a], code_cache={a: code})[a]
            i.update(row); i.update({'premint': True, 'first_seen_cst': time.strftime('%Y-%m-%d %H:%M', time.gmtime(time.time() + 8 * 3600)),
                                     'first_block_seen': head, 'mints_in_scan': 0, 'source': 'predeploy'})
            found[a] = i; new_fm.append(i)
        else:
            nm = _str(call(a, sel('name()')))
            row.update({'name': nm, 'free_selectors_in_code': [s for s in ('1249c58b', '5b70ea9f', '4e71d92d', 'a0712d68', '26092b83', 'f366afc9', '1be3587a', '9f92d0dd') if ('63' + s) in fc]})
            seen_o = st.setdefault('pre_other', {})
            if a not in seen_o: seen_o[a] = time.strftime('%Y-%m-%d %H:%M', time.gmtime(time.time() + 8 * 3600)); other.append(row)
    return new_fm, other


def backfill_deployers(found, head, budget=3):
    """found.json 里缺部署者的未开池合约：二分查创建块 → 找出部署交易，记 deployer/deploy_nonce（每轮最多 budget 个，节点支持历史状态才做）。"""
    todo = [a for a, v in found.items() if not v.get('opened') and 'deployer' not in v][:budget]
    for a in todo:
        if time.time() > DEADLINE - 30: break
        try:
            lo, hi = head - 600000, head
            c0 = rpc('eth_getCode', [a, hex(lo)])
            if c0 and len(c0) > 2: found[a]['deployer'] = None; continue  # 太老，不追
            while hi - lo > 1:
                m = (lo + hi) // 2
                if len(rpc('eth_getCode', [a, hex(m)]) or '0x') > 2: hi = m
                else: lo = m
            b = rpc('eth_getBlockByNumber', [hex(hi), True]); dep = None
            for tx in b['transactions']:
                if not tx.get('to') and CU.create_addr(tx['from'].lower(), int(tx['nonce'], 16)) == a:
                    dep = tx['from'].lower(); found[a]['deploy_nonce'] = int(tx['nonce'], 16); break
            found[a]['deployer'] = dep; found[a]['deploy_block'] = hi
        except Exception:
            pass


def main():
    mins = 30; hours = 0
    if '--minutes' in sys.argv: mins = int(sys.argv[sys.argv.index('--minutes') + 1])
    if '--hours' in sys.argv: hours = float(sys.argv[sys.argv.index('--hours') + 1])
    st = json.load(open(ST)) if os.path.exists(ST) else {}
    found = json.load(open(OUT)) if os.path.exists(OUT) else {}
    head = int(rpc('eth_blockNumber', []), 16)
    bt = 0.45
    try:
        b1 = rpc('eth_getBlockByNumber', [hex(head), False]); b0 = rpc('eth_getBlockByNumber', [hex(head - 2000), False])
        bt = max(0.2, (int(b1['timestamp'], 16) - int(b0['timestamp'], 16)) / 2000)
    except Exception: pass
    back = int((hours * 3600 if hours else mins * 60) / bt)
    start = head - back if hours else max(st.get('last', 0) + 1, head - back)
    seen_now, errors = {}, 0
    f = start
    while f <= head:
        t = min(f + RANGE - 1, head)
        try:
            logs = rpc('eth_getLogs', [{'fromBlock': hex(f), 'toBlock': hex(t), 'topics': [TOPICS + [OPENED]]}])
            for l in logs:
                a = l['address'].lower(); e = seen_now.setdefault(a, {'mints': 0, 'opened_evt': False, 'first_block': int(l['blockNumber'], 16)})
                if l['topics'][0] == OPENED: e['opened_evt'] = True
                else: e['mints'] += 1
            f = t + 1; sys.stderr.write(f'logs {t} n={len(seen_now)}\n')
        except Exception:
            errors += 1
            if errors > 8: break
            f = t + 1
    prev_open = {a: v.get('opened') for a, v in found.items()}
    prev_minted = {a: v.get('total_minted') for a, v in found.items()}
    # ② 已部署未开铸
    pre_new, pre_other, pre_err = [], [], None
    try: pre_new, pre_other = predeploy(st, found, start, head)
    except Exception as e: pre_err = str(e)[:160]
    # ①③ 本轮有事件的 + 全部未开池（3 天内发现的）一次批量刷新
    now = time.time()
    def fresh(v):  # first_seen_cst 是北京时间
        try: return now - (calendar.timegm(time.strptime(v.get('first_seen_cst'), '%Y-%m-%d %H:%M')) - 8 * 3600) < 3 * 86400
        except Exception: return True
    unopened = [a for a, v in found.items() if not v.get('opened') and a not in seen_now and (v.get('premint') or fresh(v))]
    new = []
    targets = list(seen_now) + unopened
    code_cache = {}
    infos = info_many(targets, prev=found, code_cache=code_cache)
    for a in targets:
        i = infos[a]
        if a not in found:
            i.update({'first_seen_cst': time.strftime('%Y-%m-%d %H:%M', time.gmtime(time.time() + 8 * 3600)),
                      'first_block_seen': seen_now[a]['first_block'], 'bscscan': f'https://bscscan.com/address/{a}', 'mints_in_scan': seen_now[a]['mints']})
            found[a] = i; new.append(i)
        else:
            found[a].update({k: v for k, v in i.items() if v is not None})
            if a in seen_now: found[a]['mints_in_scan'] = seen_now[a]['mints']
    # ④ 读不到开池份数的：自动找总量函数（只提醒）
    for a in targets:
        try: guess_cap(a, found[a])
        except Exception: pass
    backfill_deployers(found, head)

    near_guess = []
    def build_rows():
        watch, near, just_opened, started = [], [], [], []; near_guess.clear()
        for a in set(targets):
            v = found[a]
            op, guessed = open_pct(v)
            last = v.get('last_open_pct'); d = round(op - last, 2) if last is not None else 0
            row = {'contract': a, 'name': v.get('name'), 'cost_1share_bnb': v.get('cost_1share_bnb'), 'open_pct': op, 'delta_pct': d,
                   'total_minted': v.get('total_minted'), 'open_at_shares': v.get('open_at_shares') or v.get('cap_guess'),
                   'has_refund': v.get('has_refund')}
            if guessed: row['open_pct_guess'] = True  # 总量是猜的：只提醒，autofill 不自动买
            if v.get('premint'): row['premint'] = True
            if v.get('deployer'): row['dev_contracts'] = sum(1 for x in found.values() if x.get('deployer') == v['deployer'])  # 同一部署者的 fair mint 数（≥3 = 重复 DEV）
            if v.get('opened'):
                if prev_open.get(a) is False or (prev_open.get(a) is None and a in seen_now and last is not None): just_opened.append(row)
                continue
            if v.get('premint') and not prev_minted.get(a) and (v.get('total_minted') or 0) > 0: started.append(row)
            watch.append(row)
            if guessed:
                if (op >= 60 and (last or 0) < 60) or d >= 15: near_guess.append(row)  # 总量是猜的：单独列，巡逻核实后再按 60% 规则处理
            elif op >= 60 or d >= 15: near.append(row)  # 60% 按开池份数(如前 1 万份)算，用户 20:06 确认
        return watch, near, just_opened, started
    watch, near, just_opened, started = build_rows()
    # ③ 本轮快速重读（v2.4.3）：50–70% 区间、刚开铸、或本轮涨 ≥10 个点的，每 20 秒重读，直到总时限
    rechecks = 0
    hot = lambda: [r['contract'] for r in watch if not r.get('open_pct_guess') and (50 <= r['open_pct'] < 70 or r['delta_pct'] >= 10 or r.get('premint') and (r['total_minted'] or 0) > 0)]
    if '--no-recheck' not in sys.argv:
        while hot() and time.time() + 25 < DEADLINE and rechecks < 4:
            time.sleep(20); rechecks += 1
            hs = hot(); ii = info_many(hs, prev=found)
            for a in hs: found[a].update({k: v for k, v in ii[a].items() if v is not None})
            for a in hs:
                try: guess_cap(a, found[a])
                except Exception: pass
            watch, near, just_opened2, started2 = build_rows()
            just_opened += [r for r in just_opened2 if r['contract'] not in {x['contract'] for x in just_opened}]
            started += [r for r in started2 if r['contract'] not in {x['contract'] for x in started}]
            if any(r['open_pct'] >= 70 for r in watch if r['contract'] in hs): break  # 过 70% 立刻交给巡逻处理
    for a in set(targets): found[a]['last_open_pct'] = open_pct(found[a])[0]
    st['last'] = head
    json.dump(st, open(ST + '.tmp', 'w')); os.replace(ST + '.tmp', ST)
    json.dump(found, open(OUT + '.tmp', 'w'), ensure_ascii=False, indent=1); os.replace(OUT + '.tmp', OUT)
    print(json.dumps({'head': head, 'blocks': head - start + 1, 'log_errors': errors, 'active_contracts': len(seen_now), 'elapsed_s': round(time.time() - T0, 1), 'rechecks': rechecks,
                      'new': new,
                      'premint_new': pre_new,          # 已部署未开铸的 fair mint（新加入观察）→ 消息里单独一行「已部署未开铸」
                      'mint_started': started,         # 观察中的未开铸盘本轮开铸 → 第一行提醒
                      'agentid_deployed': pre_other,   # 其他新部署的 Agent ID 专属合约（可能是免费 mint）→ 交给 precheck/人工核验
                      'premint_watch': [r for r in watch if r.get('premint')], 'predeploy_error': pre_err, 'watched_devs': len(st.get('pre_devs', {})),
                      'watch_unopened': sorted(watch, key=lambda r: -r['open_pct']), 'near_open': near, 'near_open_guess': near_guess, 'just_opened': just_opened,
                      'active': {a: {k: found[a].get(k) for k in ('cost_1share_bnb', 'opened', 'progress_pct', 'open_progress_pct', 'open_bnb_progress_pct', 'mints_in_scan')} for a in seen_now}},
                     ensure_ascii=False, indent=1))


if __name__ == '__main__': main()
