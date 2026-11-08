#!/usr/bin/env python3
"""常驻发现进程（2026-11-02 用户 16:32「按照你推荐优化 确保不会遗漏」）。只读：只做 getLogs / eth_call / getCode / getTransaction，
不签名、不交易、不买不卖不铸。

为什么：原来「发现」只在巡逻里跑（fairmint.py 每 10 分钟一轮、每轮最多 100 秒），而且有两处会跳块：
  ① fairmint.py 主扫描 start = max(上次+1, head-30 分钟)：落后超过 30 分钟（节点故障、超时）中间的块直接跳过；
  ② predeploy 落后超过 12000 块只补最近 12000 块（同样跳过）。
  另外公共节点偶尔落后几十块，对还没同步到的块 getLogs 会返回空列表（不报错），游标照样前移 = 静默漏块。
做法（每约 60 秒一轮）：
  1. 块游标 cursor 持久化（fairmint/discoverd_state.json）。每轮从 cursor+1 扫到 安全头（各日志节点自报的块高 −2）；
     500 块一段、4 段并发、按顺序提交，遇到第一段失败就停，cursor 只推进到最后一段成功的块；任何失败都不前移，绝不跳块。
     只用自报块高 ≥ 本段末块的节点（防「节点落后返回空」）。
  2. 一次 getLogs 同时取：Jager/AGI 铸造事件 Minted、开池类事件、PairCreated（只认 PancakeV2 工厂）、
     OwnershipTransferred(0→x)（Ownable 合约部署）、Initialized(uint8/uint64)（克隆/代理初始化，如 EIP-1167 克隆）。
  3. 新地址分三路判定（合约码一律代理追到底，CU.full_code）：
     · 新 Minted 发出者 → 读模板/进度，加入 found.json（source=discoverd）；
     · PairCreated 的部署交易是合约创建（克隆盘代币构造函数建池）→ 记部署者，按 CREATE(部署者, nonce) 每轮查它新部署的合约；
     · 新部署/新初始化的合约 → 码里有 Minted 事件或 costOf 且（含 Agent ID 登记合约 或 含 Minted 事件）→ fair mint（premint）；
       只含登记合约的 → agentid_deployed（可能是免费 mint，交巡逻核验）。
  4. 发现即写 found.json（与 fairmint.py/heatscan 同一把 .fairmint.lock；锁忙就先存进状态文件 pending，下轮再写，不丢）。
     fastprogress 常驻进程每轮重读 found.json → 立刻纳入 30/60/70% 进度、自动审计与 70% 打满闸门（闸门本身一行没改）。
     同时写 discoverd_announce.json 队列，fairmint.py 下一轮把它们并进 new / premint_new / agentid_deployed / backfill 输出，巡逻照常出简报。
  5. 漏检保护：每小时后台跑一次 `discoverd.py --reconcile`（独立进程、独立锁）：过去 2 小时
     （a）换节点（优先非本轮主扫节点）重扫 Minted/PairCreated/部署事件；（b）另一条信号：全链 Transfer(from=0) 按代币聚合，
     取铸造交易的 tx.to，码含 Minted/costOf 或与已知 jager/jagent/AGI 盘字节码指纹一致 → fair mint。
     found.json 里没有的立刻补入（backfill=true），巡逻里写「补漏」。
心跳：discover_out/discoverd_live.json；事件：discover_out/discoverd_events.jsonl；对账结果：discover_out/discoverd_reconcile.json。
用法：python3 discoverd.py --daemon [--every 60]      # 常驻（fmkick.sh 拉起，文件锁保证只有一份）
      python3 discoverd.py --once                     # 跑一轮
      python3 discoverd.py --reconcile [--hours 2]    # 对账
      python3 discoverd.py --replay FROM TO [--sim-every 60]   # 历史回放（只读，不写 found.json/状态），按 60 秒一轮模拟发现时间
"""
import json, os, sys, time, fcntl, threading, subprocess
from concurrent.futures import ThreadPoolExecutor
D = os.path.dirname(os.path.abspath(__file__)); L = os.path.dirname(D)
sys.path.insert(0, D); sys.path.insert(0, L); sys.path.insert(0, os.path.join(L, 'vendor')); sys.path.append('$AIDBOT_HOME/pylib')
os.environ.setdefault('FM_DEADLINE', '55')
import fairmint as F
import codeutil as CU

argv = sys.argv
SD = F.SD
OUTD = os.environ.get('DISC_OUT', os.path.join(L, 'discover_out'))
STATE = os.path.join(SD, 'discoverd_state.json'); FOUND = os.path.join(SD, 'found.json')
ANN = os.path.join(SD, 'discoverd_announce.json')
LIVE = os.path.join(OUTD, 'discoverd_live.json'); EVENTS = os.path.join(OUTD, 'discoverd_events.jsonl')
RECON_OUT = os.path.join(OUTD, 'discoverd_reconcile.json')
EVERY = float(argv[argv.index('--every') + 1]) if '--every' in argv else 60.0
SEG = int(os.environ.get('DISC_SEG', '500')); PAR = int(os.environ.get('DISC_PAR', '4'))
MAX_SEGS = int(os.environ.get('DISC_MAX_SEGS', '16'))  # 落后时每轮最多补 16 段（8000 块 ≈ 1 小时），下轮接着补，不跳
CONF = 2  # 安全头 = 节点块高 − 2
CYCLE_BUDGET = float(os.environ.get('DISC_BUDGET', '50'))
FIRST_BACK = int(os.environ.get('DISC_FIRST_BACK', '16000'))  # 首次启动回看约 2 小时
RECON_EVERY = 3600; RECON_BLOCKS = 16000

MINTED = F.TOPICS[0]
OPENS = F.OPEN_TOPICS
PAIR = F.PAIR_CREATED; V2 = F.V2_FACTORY
OWN = '0x8be0079c531659141344cd1fd0a4f28419497f9722a3daafe3b4186f6b6457e0'  # OwnershipTransferred(address,address)
INIT = ['0x7f26b83ff96e1f2b6a682f133852f6798a09c465da95921460cefb3847402498',  # Initialized(uint8)
        '0xc7f505b2f371ae2175ee4913f4499e1f2633a7b5936321eed1cdaeb6115181d2']  # Initialized(uint64)
TRANSFER = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'
ZERO32 = '0x' + '0' * 64
ALL_TOPICS = [MINTED] + OPENS + [PAIR, OWN] + INIT
COSTOF = '63' + CU.sel('costOf(uint256)')
FM_SELS = ['63' + CU.sel(s) for s in ('costOf(uint256)', 'bnbQuote(uint256)', 'OPEN_AT()', 'OPEN_UNITS()', 'openAt()', 'sharesMinted()', 'MAX_SHARES()',
                                       'MAX_MINTS()', 'LIQUIDITY_TARGET_SHARES()')]
NODES = list(dict.fromkeys(F.LOGS_RPCS + ['https://bsc.blockrazor.xyz']))  # blockrazor 只做 25 块小段兜底
SMALL_ONLY = {'https://bsc.blockrazor.xyz'}
BAD = {}


def now_cst(t=None): return time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime((t or time.time()) + 8 * 3600))
def load(p, d):
    try: return json.load(open(p))
    except Exception: return d
def dump(p, o):
    json.dump(o, open(p + '.tmp', 'w'), ensure_ascii=False, indent=1); os.replace(p + '.tmp', p)
def log(msg): sys.stderr.write(f'[{now_cst()}] {msg}\n'); sys.stderr.flush()


def node_heads(nodes=None):
    """各日志节点自报块高（并发，4 秒超时）；拿不到的不参与本轮。"""
    nodes = nodes or NODES
    def h(u):
        try:
            d = F._post(u, {'jsonrpc': '2.0', 'id': 1, 'method': 'eth_blockNumber', 'params': []}, 4)
            return u, int(d['result'], 16)
        except Exception: return u, None
    with ThreadPoolExecutor(len(nodes)) as ex: r = dict(ex.map(h, nodes))
    return {u: b for u, b in r.items() if b}


def get_logs(f, t, topics, heads, prefer=None, avoid=None, address=None, deadline=None, small=True):
    """[f,t] 的 getLogs。只用自报块高 ≥ t 的节点；返回 (logs, 节点)。全部失败抛异常（调用方不前移游标）。"""
    q = {'fromBlock': hex(f), 'toBlock': hex(t), 'topics': topics}  # topics 为完整 topics 数组，如 [[t0a,t0b]] 或 [t0, topic1]
    if address: q['address'] = address
    now = time.time()
    cand = [u for u in NODES if u not in SMALL_ONLY and heads.get(u, 0) >= t]
    cand.sort(key=lambda u: (u == avoid, BAD.get(u, 0) > now, u != prefer))
    last = None
    for u in cand:
        if deadline and time.time() > deadline: break
        try:
            d = F._post(u, {'jsonrpc': '2.0', 'id': 1, 'method': 'eth_getLogs', 'params': [q]}, max(3, min(20, (deadline or time.time() + 20) - time.time())))
            if isinstance(d, dict) and isinstance(d.get('result'), list): return d['result'], u
            last = d.get('error') if isinstance(d, dict) else d
        except Exception as e: last = e
        BAD[u] = time.time() + 60
    # 兜底：blockrazor 25 块一段（同样要求它的块高 ≥ t）
    sm = [u for u in SMALL_ONLY if heads.get(u, 0) >= t]
    if small and sm and t - f + 1 <= 25 * 24:
        out = []
        for a in range(f, t + 1, 25):
            if deadline and time.time() > deadline: raise RuntimeError(f'getLogs {f}-{t} 主节点失败、小段兜底到时限未完成：{str(last)[:120]}')
            q2 = dict(q, fromBlock=hex(a), toBlock=hex(min(a + 24, t))); ok = False
            for k in range(3):
                try:
                    d = F._post(sm[0], {'jsonrpc': '2.0', 'id': 1, 'method': 'eth_getLogs', 'params': [q2]}, 8)
                    if isinstance(d.get('result'), list): out += d['result']; ok = True; break
                except Exception: pass
                time.sleep(0.6 * (k + 1))
            if not ok: raise RuntimeError(f'getLogs {f}-{t} 全部节点失败（blockrazor 小段 {a} 也失败）：{str(last)[:120]}')
        return out, sm[0]
    raise RuntimeError(f'getLogs {f}-{t} 全部节点失败：{str(last)[:160]}')


def scan_range(f, t, topics, heads, budget_end, prefer=None, avoid=None, address=None, max_segs=None, small=True):
    """f..t 按 SEG 分段并发取日志，按顺序提交，遇到第一段失败/超时就停。返回 (logs, scanned_to, 错误, 用到的节点)。"""
    segs = [(x, min(x + SEG - 1, t)) for x in range(f, t + 1, SEG)]
    if max_segs: segs = segs[:max_segs]
    def one(s):
        if time.time() > budget_end: return None
        try: return get_logs(s[0], s[1], topics, heads, prefer, avoid, address, deadline=budget_end + 15, small=small)
        except Exception as e: return e
    res = []
    with ThreadPoolExecutor(min(PAR, max(1, len(segs)))) as ex: res = list(ex.map(one, segs))
    logs, to, err, used = [], f - 1, None, set()
    for s, r in zip(segs, res):
        if r is None: err = err or '本轮时限到'; break
        if isinstance(r, Exception): err = str(r)[:200]; break
        logs += r[0]; used.add(r[1]); to = s[1]
    return logs, to, err, used


# 2026-11-02 17:3x：对账首轮把一批「REG + bnbQuote + totalMinted」的机器批量部署合约（Mesaphase/…lune/…core 等 90 个，无开池门槛）当成 fair mint 补进来，
# 太吵。收紧：加入 found.json 的必须是「码里有 Jager Minted 事件」或「有开池门槛函数（OPEN_AT/OPEN_UNITS/MAX_SHARES/MAX_MINTS/LIQUIDITY_TARGET_SHARES）」
# 或「字节码指纹与已知 fair mint 一致」；只含 Agent ID 登记合约的其余合约归 agentid_deployed（交巡逻核验，可能是免费/付费 mint），不进 found.json。
# 2026-11-09 00:35 DOG(0xe996，binanciansai.meme 克隆) 用小写 openAt()，原来进不了 found.json → 没有进度监控
OPEN_SELS = ['63' + CU.sel(s) for s in ('openAt()', 'openUnits()', 'OPEN_AT()', 'OPEN_UNITS()', 'MAX_SHARES()', 'MAX_MINTS()', 'LIQUIDITY_TARGET_SHARES()', 'OPEN_BNB()')]
TRIO = ['63' + CU.sel(x) for x in ('openAt()', 'costOf(uint256)', 'totalMinted()')]
def fm_trio(fc): fc = fc or ''; return all(x in fc for x in TRIO)  # 2026-11-09 DOG 类：无 REG/Minted 事件但有开池门槛+按份计价+已铸
def fm_like(fc):
    fc = fc or ''
    return F.TOPICS[0][2:] in fc or any(s in fc for s in OPEN_SELS)


def code_info(addrs, impl_cache):
    """批量 getCode + 代理追到底。返回 {a: full_code 小写 | '' (无码) | None (读失败)}。"""
    out = {}
    if not addrs: return out
    R = F.bat([('eth_getCode', [a, 'latest']) for a in addrs], chunk=40, parallel=4)
    for a, c in zip(addrs, R):
        if c is None: out[a] = None; continue
        c = c.lower()
        if len(c) <= 2: out[a] = ''; continue
        h = c[2:]
        if h.startswith('363d3d373d3d3d363d73') and len(h) >= 60:  # EIP-1167：实现合约码缓存（克隆工厂同一实现）
            impl = '0x' + h[20:60]
            if impl not in impl_cache:
                try: impl_cache[impl] = CU.full_code(F.rpc, F.call, impl)
                except Exception: out[a] = None; continue
            out[a] = c + impl_cache[impl]; continue
        if len(c) > 6000: out[a] = c; continue  # 大合约不是代理（代理/Beacon 壳都很小），直接用自身码，省掉两次 getStorageAt
        out[a] = ('PROXY', c)
    prox = [a for a, v in out.items() if isinstance(v, tuple)]
    def fc(a):
        try: return a, CU.full_code(F.rpc, F.call, a, out[a][1])
        except Exception: return a, None
    if prox:
        with ThreadPoolExecutor(min(8, len(prox))) as ex:
            for a, v in ex.map(fc, prox): out[a] = v
    return out


def make_row(a, info, extra):
    row = dict(info); row.update(extra)
    row.setdefault('bscscan', f'https://bscscan.com/address/{a}')
    return row


class Ctx:
    def __init__(self, st, found, replay=False):
        self.st = st; self.found = found; self.replay = replay
        self.adds = {}      # 新加入 found.json 的 {a: row}
        self.ann = []       # 公告队列
        self.events = []
        self.impl_cache = {}
        self.fail_block = None

    def known(self, a): return a in self.found or a in self.adds or a in self.st.get('pending_add', {})

    def fail(self, b):
        self.fail_block = b if self.fail_block is None else min(self.fail_block, b)


def add_fm(ctx, a, blk, kind, source, backfill=False, mints=0, extra=None, code=None):
    """把一个 fair mint 合约加进 found（结构与 fairmint.py/predeploy 一致）。"""
    if ctx.known(a): return None
    try: i = F.info_many([a], code_cache={a: code or '0x'} if code else None)[a]
    except Exception as e:
        ctx.fail(blk); log(f'info_many {a} 失败：{e}'); return None
    i.pop('_read_ok', None)
    row = make_row(a, i, {'first_seen_cst': now_cst()[:16], 'first_block_seen': blk, 'mints_in_scan': mints, 'source': source,
                          'discovered_by': 'discoverd' + ('_reconcile' if backfill else '')})
    if kind == 'premint': row['premint'] = True
    if backfill: row['backfill'] = True
    if extra: row.update(extra)
    ctx.adds[a] = row
    ev = {'time_cst': now_cst(), 'kind': kind, 'backfill': backfill, 'contract': a, 'name': row.get('name'), 'block': blk, 'source': source,
          'template': row.get('template'), 'open_at_shares': row.get('open_at_shares'), 'total_minted': row.get('total_minted'), 'opened': row.get('opened'),
          'bscscan': row['bscscan']}
    ctx.events.append(ev); ctx.ann.append({'kind': 'premint_new' if kind == 'premint' else 'new', 'backfill': backfill, 'contract': a, 'at': time.time()})
    log(f'发现 {kind} {a} {row.get("name")} 块 {blk} 来源 {source}{" 补漏" if backfill else ""}')
    return row


def handle_logs(ctx, logs, backfill=False, source_prefix=''):
    """处理一批日志：新 Minted 发出者、PairCreated 部署者、部署/初始化的新合约。"""
    st = ctx.st; now = time.time()
    devs = st.setdefault('devs', {}); checked = st.setdefault('checked', {})
    minted, pairs, newc = {}, {}, {}
    for l in logs:
        t0 = l['topics'][0] if l.get('topics') else None; a = l['address'].lower(); b = int(l['blockNumber'], 16)
        if t0 == MINTED:
            e = minted.setdefault(a, [b, 0]); e[1] += 1
        elif t0 == PAIR and a == V2:
            pairs[l['transactionHash']] = b
        elif t0 == OWN and len(l['topics']) >= 2 and int(l['topics'][1], 16) == 0:
            newc.setdefault(a, b)
        elif t0 in INIT:
            newc.setdefault(a, b)
    # ① 新 Minted 发出者（与 fairmint.py 主扫描同口径：任何发 Jager Minted 事件的合约都收录，模板/进度由 info_many 读）
    for a, (b, n) in sorted(minted.items(), key=lambda x: x[1][0]):
        if not ctx.known(a): add_fm(ctx, a, b, 'new', source_prefix + 'minted_event', backfill, mints=n)
    # ② PairCreated → 部署交易是合约创建的 → 记部署者
    hs = [h for h in pairs if h not in st.setdefault('pair_tx_seen', {})]
    if hs:
        try: txs = F.bat([('eth_getTransactionByHash', [h]) for h in hs], chunk=40, parallel=4)
        except Exception: txs = [None] * len(hs)
        for h, tx in zip(hs, txs):
            if tx is None: ctx.fail(pairs[h]); continue
            st['pair_tx_seen'][h] = int(now)
            if tx.get('to'): continue
            dep = tx['from'].lower(); n = int(tx['nonce'], 16)
            if dep not in devs: devs[dep] = {'at': now, 'next': max(0, n - 3), 'pair_tx': h, 'block': pairs[h], 'token': CU.create_addr(dep, n)}
    # ③ 新部署/新初始化合约
    cand = [a for a in newc if not ctx.known(a) and a not in checked]
    if cand:
        ci = code_info(cand, ctx.impl_cache)
        for a in cand:
            fc = ci.get(a)
            if fc is None: ctx.fail(newc[a]); continue
            checked[a] = int(now)
            if not fc: continue
            judge(ctx, a, fc, newc[a], backfill, source_prefix + 'deploy_event')


def judge(ctx, a, fc, blk, backfill, source, deployer=None, nonce=None):
    reg = CU.REG in fc
    if fm_like(fc) and (reg or F.TOPICS[0][2:] in fc or fm_trio(fc)):
        ex = {'agentid_registry_in_code': reg}
        if deployer: ex.update({'deployer': deployer, 'deploy_nonce': nonce})
        add_fm(ctx, a, blk, 'premint', source, backfill, extra=ex, code=fc[:2] == '0x' and fc or None)
    elif reg:
        other = ctx.st.setdefault('other', {})
        if a not in other:
            nm = F._str(F.call(a, F.sel('name()')))
            other[a] = now_cst()[:16]
            row = {'contract': a, 'name': nm, 'deployer': deployer, 'block': blk, 'source': source, 'backfill': backfill,
                   'bscscan': f'https://bscscan.com/address/{a}',
                   'free_selectors_in_code': [s for s in ('1249c58b', '5b70ea9f', '4e71d92d', 'a0712d68', '26092b83', 'f366afc9', '1be3587a', '9f92d0dd') if ('63' + s) in fc]}
            ctx.events.append(dict(row, kind='agentid_deployed', time_cst=now_cst()))
            ctx.ann.append({'kind': 'agentid_deployed', 'backfill': backfill, 'row': row, 'at': time.time()})


def poll_devs(ctx, head_blk, backfill=False):
    """部署者新 nonce → 新合约地址（与 predeploy 同法；部署者记 48 小时，最多 400 个）。found.json 里未开池盘的部署者同样盯。"""
    st = ctx.st; devs = st.setdefault('devs', {}); now = time.time()
    for d in [d for d, x in devs.items() if now - x.get('at', 0) > 48 * 3600]: devs.pop(d, None)
    for a, v in ctx.found.items():
        dep = v.get('deployer')
        if dep and dep not in devs and not v.get('opened'): devs[dep] = {'at': now, 'next': (v.get('deploy_nonce') or 0) + 1, 'from_known': a}
    if len(devs) > 400:
        for d in sorted(devs, key=lambda d: devs[d].get('at', 0))[:len(devs) - 400]: devs.pop(d, None)
    dl = list(devs)
    try: cur = F.bat([('eth_getTransactionCount', [d, 'latest']) for d in dl], chunk=40, parallel=4) if dl else []
    except Exception: return
    cand = []
    for d, c in zip(dl, cur):
        if not c: continue
        c = int(c, 16); x = devs[d]; lo = x.get('next', 0)
        for n in range(lo, min(c, lo + 12)): cand.append((d, n, CU.create_addr(d, n)))
    todo = [z for z in cand if not ctx.known(z[2])]
    ci = code_info([z[2] for z in todo], ctx.impl_cache) if todo else {}
    done_upto = {}
    for d, n, a in cand:
        fc = ci.get(a, 'known') if not ctx.known(a) else 'known'
        if fc is None: done_upto.setdefault(d, n); continue  # 读失败：该部署者从这个 nonce 起下轮重查
        if fc and fc != 'known': judge(ctx, a, fc, head_blk, backfill, ('reconcile_' if backfill else '') + 'predeploy_daemon', d, n)
    for d, x in devs.items():
        if d in done_upto: x['next'] = done_upto[d]
        else:
            got = [n for dd, n, _ in cand if dd == d]
            if got: x['next'] = max(got) + 1


def commit(ctx, live_extra, write_live=True):
    """写 found.json（锁忙 → 进 pending 下轮再写）、公告队列、事件、状态。"""
    st = ctx.st
    pend = st.setdefault('pending_add', {}); pend.update(ctx.adds)
    if pend:
        lk = open(os.path.join(SD, '.fairmint.lock'), 'w'); got = False; t_end = time.time() + 25
        while time.time() < t_end:
            try: fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB); got = True; break
            except OSError: time.sleep(1)
        if got:
            try:
                found = load(FOUND, None)
                if isinstance(found, dict):
                    for a, row in pend.items():
                        if a not in found: found[a] = row
                    dump(FOUND, found); st['pending_add'] = {}
            finally: fcntl.flock(lk, fcntl.LOCK_UN)
        else: log(f'found.json 锁忙，{len(pend)} 个新盘暂存 pending，下轮再写')
    if ctx.ann:
        lk = open(ANN + '.lock', 'w'); fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            q = load(ANN, []); q += ctx.ann; dump(ANN, q[-500:])
        finally: fcntl.flock(lk, fcntl.LOCK_UN)
    if ctx.events:
        with open(EVENTS, 'a') as fe:
            for e in ctx.events: fe.write(json.dumps(e, ensure_ascii=False) + '\n')
    # 清理：checked 保留 7 天，pair_tx_seen 保留 1 天
    now = time.time()
    st['checked'] = {a: t for a, t in st.get('checked', {}).items() if now - t < 7 * 86400}
    st['pair_tx_seen'] = {h: t for h, t in st.get('pair_tx_seen', {}).items() if now - t < 86400}
    dump(STATE, st)
    if write_live: dump(LIVE, dict(live_extra, heartbeat=int(time.time()), pid=os.getpid(), pending_add=len(st.get('pending_add', {}))))


def cycle(st):
    F.T0 = time.time(); F.DEADLINE = F.T0 + CYCLE_BUDGET + 10; F.RPC_FAIL[0] = 0
    t0 = time.time(); heads = node_heads()
    if not heads: raise RuntimeError('所有日志节点都拿不到块高')
    head = max(heads.values()); safe = head - CONF
    cur = st.get('cursor')
    if cur is None:  # 首启：从 fairmint 主扫描游标前 2000 块接上（有重叠无空档），最多回看 FIRST_BACK
        fm_last = load(os.path.join(SD, 'state.json'), {}).get('last')
        cur = max(safe - FIRST_BACK, min(fm_last or safe, safe) - 2000)
    found = load(FOUND, {})
    ctx = Ctx(st, found)
    logs, to, err, used = (scan_range(cur + 1, safe, [ALL_TOPICS], heads, t0 + CYCLE_BUDGET * 0.6, max_segs=MAX_SEGS) if safe > cur else ([], cur, None, set()))
    handle_logs(ctx, logs)
    if time.time() < t0 + CYCLE_BUDGET * 0.85: poll_devs(ctx, safe)
    new_cur = to if ctx.fail_block is None else min(to, ctx.fail_block - 1)
    new_cur = max(new_cur, cur)  # 游标只进不退
    st['cursor'] = new_cur; st['last_ok_at'] = int(time.time()) if err is None and ctx.fail_block is None else st.get('last_ok_at')
    st['err_streak'] = 0 if err is None and ctx.fail_block is None else st.get('err_streak', 0) + 1
    if err: st['last_err'] = f'{now_cst()} {err}'
    live = {'time_cst': now_cst(), 'head': head, 'cursor': new_cur, 'lag_blocks': safe - new_cur, 'scanned': max(0, to - cur),
            'logs': len(logs), 'nodes_used': sorted(used), 'node_heads': heads, 'cycle_s': round(time.time() - t0, 1), 'err': err,
            'fail_block': ctx.fail_block, 'err_streak': st['err_streak'], 'new': [a for a in ctx.adds], 'devs': len(st.get('devs', {})),
            'last_reconcile_cst': st.get('last_reconcile_cst')}
    commit(ctx, live)
    return live


def maybe_reconcile(st):
    if 'reconcile_at' not in st: st['reconcile_at'] = int(time.time() - RECON_EVERY + 600)  # 首次启动 10 分钟后做第一次对账，避开冷启动补块
    if time.time() - st.get('reconcile_at', 0) < RECON_EVERY: return
    st['reconcile_at'] = int(time.time())
    subprocess.Popen(['bash', '-c', f'cd {D} && timeout 900 python3 discoverd.py --reconcile >> {OUTD}/discoverd_reconcile.log 2>&1'],
                     start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def scan_all(f, t, topics, heads, budget_end, **kw):
    """对账/回放用：scan_range 失败就退避重试剩余区间，直到扫完或到时限。返回 (logs, scanned_to, 最后错误, 节点)。"""
    logs, to, err, used, k = [], f - 1, None, set(), 0
    while to < t and time.time() < budget_end:
        l2, to2, e2, u2 = scan_range(to + 1, t, topics, heads, budget_end, **kw)
        logs += l2; used |= u2
        if to2 > to: to = to2; k = 0
        if e2:
            err = e2; k += 1; log(f'scan_all {to + 1}-{t} 出错（第 {k} 次）：{e2[:120]}')
            time.sleep(min(30, 3 * k))
        else: err = None
    return logs, to, (err if to < t else None), used


# ---------------- 对账（每小时） ----------------
def reconcile(hours=2.0):
    lk = open(STATE + '.recon.lock', 'w')
    try: fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError: print(json.dumps({'reconcile': 'already_running'})); return
    F.T0 = time.time(); F.DEADLINE = F.T0 + 840
    t0 = time.time(); heads = node_heads(); head = max(heads.values()); safe = head - CONF
    blocks = int(hours * 3600 / 0.45); f = safe - blocks
    dst = load(STATE, {})
    upto = min(safe, dst.get('cursor') or safe)  # 只对账主扫已经扫过的块
    main_nodes = set(load(LIVE, {}).get('nodes_used') or [])
    avoid = sorted(main_nodes)[0] if len(main_nodes) == 1 else None
    # checked 沿用常驻进程已核过码的地址（对账 (a) 只为抓「主扫那一刻节点漏返回的日志」，核过的地址不重复拉码）
    found = load(FOUND, {}); st = {'devs': {}, 'checked': dict(dst.get('checked', {})), 'pair_tx_seen': dict(dst.get('pair_tx_seen', {})), 'other': dict(dst.get('other', {}))}
    ctx = Ctx(st, found)
    res = {'time_cst': now_cst(), 'from': f, 'to': upto, 'errors': []}
    all_adds, all_events = {}, []
    tpl_fps = [dst.get('tpl_fps', [])]
    def flush(stage):
        """阶段性写回（发现即补入，不等整轮对账结束）：补漏的盘进 found.json（与主进程同一把锁）；devs/other 并进主状态让常驻进程继续盯。"""
        all_adds.update(ctx.adds); all_events.extend(ctx.events)
        lk2 = open(STATE + '.lock', 'w'); fcntl.flock(lk2, fcntl.LOCK_EX)
        try:
            d2 = dict(load(STATE, {}), tpl_fps=tpl_fps[0])
            for d, x in ctx.st.get('devs', {}).items(): d2.setdefault('devs', {}).setdefault(d, x)
            d2.setdefault('other', {}).update(ctx.st.get('other', {}))
            d2['last_reconcile_cst'] = res['time_cst']; d2['last_reconcile_missed'] = len(all_adds)
            keep = ctx.st; ctx.st = d2
            commit(ctx, {}, write_live=False)
            ctx.st = keep
        finally: fcntl.flock(lk2, fcntl.LOCK_UN)
        if ctx.adds: log(f'对账{stage}：补入 {len(ctx.adds)} 个：' + '、'.join(f"{r.get('name') or '未命名'} {a}" for a, r in ctx.adds.items()))
        for a in ctx.adds: ctx.found[a] = ctx.adds[a]
        ctx.adds = {}; ctx.ann = []; ctx.events = []
    # (a) 换节点重扫同一组事件
    logs, to, err, used = scan_all(f, upto, [ALL_TOPICS], heads, t0 + 300, avoid=avoid)
    res['a_logs'] = len(logs); res['a_scanned_to'] = to; res['a_nodes'] = sorted(used); log(f'对账 {f}-{upto}：(a) 取日志 {len(logs)} 条，扫到 {to}，错误 {err}')
    if err: res['errors'].append('a: ' + err)
    handle_logs(ctx, logs, backfill=True, source_prefix='reconcile_')
    # 部署者：(a) 里新出现的部署者马上查一次 nonce
    poll_devs(ctx, upto, backfill=True)
    flush('(a)')
    # (b) 另一条信号：Transfer(from=0) 按代币聚合 → 铸造交易的 tx.to
    log(f'对账(a) 完成：{len(logs)} 条日志，扫到 {to}，新增 {len(ctx.adds)}；开始 (b) Transfer(from=0)')
    tlogs, tto, terr, tused = scan_all(f, upto, [TRANSFER, ZERO32], heads, t0 + 480, small=False)
    log(f'对账(b) 日志 {len(tlogs)} 条，扫到 {tto}，错误 {terr}')
    res['b_logs'] = len(tlogs); res['b_scanned_to'] = tto
    if terr: res['errors'].append('b: ' + terr)
    known_tok = {(v.get('token') or '').lower() for v in found.values()}
    toks = {}
    for l in tlogs:
        if len(l['topics']) < 3: continue
        a = l['address'].lower()
        if a in known_tok: continue
        e = toks.setdefault(a, {'r': set(), 'tx': l['transactionHash'], 'b': int(l['blockNumber'], 16)})
        e['r'].add(l['topics'][2])
    cands = sorted([(len(e['r']), a) for a, e in toks.items() if len(e['r']) >= 3], reverse=True)[:300]
    res['b_tokens'] = len(cands)
    tos = {}
    if cands:
        txs = F.bat([('eth_getTransactionByHash', [toks[a]['tx']]) for _, a in cands], chunk=40, parallel=4)
        for (_, a), tx in zip(cands, txs):
            if tx and tx.get('to'): tos.setdefault(tx['to'].lower(), toks[a]['b'])
    tgt = [a for a in tos if not ctx.known(a)]
    ci = code_info(tgt, ctx.impl_cache) if tgt else {}
    # 字节码指纹：已知 fair mint 盘（found.json）的指纹集合，用于认出同模板但事件签名不同的合约
    try:
        import codefp
        fp_known = set(dst.get('tpl_fps', []))
        if not fp_known:
            sample = [a for a, v in found.items() if v.get('template') in ('jager', 'jagent', 'boss', 'mao')][:120]
            fp_known = {x for x in codefp.fps(sample).values() if x}
            tpl_fps[0] = sorted(fp_known)
    except Exception: fp_known = set()
    fp_hit = 0
    for a in tgt:
        fc = ci.get(a)
        if not fc: continue
        try: fpm = codefp.norm('0x' + fc.split('0x')[1]) in fp_known if fp_known else False  # 只取合约自身码（full_code 后面拼的是实现合约码）
        except Exception: fpm = False
        if fpm: fp_hit += 1
        if (fm_like(fc) and (CU.REG in fc or F.TOPICS[0][2:] in fc or fm_trio(fc))) or (fpm and CU.REG in fc):
            add_fm(ctx, a, tos[a], 'new', 'reconcile_transfer_mint', True, extra={'template_fp_match': fpm, 'agentid_registry_in_code': CU.REG in fc}, code=fc)
    res['b_mint_contracts'] = len(tos); res['b_checked'] = len(tgt); res['b_fp_hit'] = fp_hit
    flush('(b)')
    res['missed'] = [{'contract': a, 'name': r.get('name'), 'block': r.get('first_block_seen'), 'source': r.get('source'),
                      'opened': r.get('opened'), 'open_pct': (round((r.get('total_minted') or 0) * 100 / r['open_at_shares'], 2) if r.get('open_at_shares') else None),
                      'bscscan': r.get('bscscan')} for a, r in all_adds.items()]
    res['agentid_other_missed'] = [e for e in all_events if e.get('kind') == 'agentid_deployed']
    res['elapsed_s'] = round(time.time() - t0, 1)
    dump(RECON_OUT, res)
    print(json.dumps(res, ensure_ascii=False, indent=1))


# ---------------- 常驻 ----------------
SW_LAST = [0.0]
NEWTPL_LAST = [0.0]
def daemon():
    lk = open(STATE + '.daemon.lock', 'w')
    try: fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError: print(json.dumps({'daemon': 'already_running'})); return
    open(os.path.join(OUTD, 'discoverd.pid'), 'w').write(str(os.getpid()))
    beat = [time.time()]
    HB = os.path.join(OUTD, 'discoverd_hb.json')
    def wd():  # 进程级心跳（每 15 秒，fmkick 看这个）；单轮卡死 >240 秒 → 退出，fmkick 发现心跳过期会重启（游标在状态文件里，不丢块）
        while True:
            if time.time() - beat[0] > 240: log('看门狗：单轮超过 240 秒，退出等 fmkick 重启'); os._exit(5)
            try: dump(HB, {'heartbeat': int(time.time()), 'pid': os.getpid(), 'cycle_age_s': int(time.time() - beat[0])})
            except Exception: pass
            time.sleep(15)
    threading.Thread(target=wd, daemon=True).start()
    # 2026-11-05 修：改了本文件但常驻进程还跑旧代码（salearm/gas_topup 新加的调度没生效，Gachagent 12:01 开售 12:50 才拉起）。
    # 每轮开头检查源码 mtime，变了且能编译就原地 execv 重载（同 pid，文件锁随 exec 释放）。
    SRC = os.path.abspath(__file__); SRC_MT = os.path.getmtime(SRC)
    while True:
        try:
            if os.path.getmtime(SRC) != SRC_MT:
                import py_compile; py_compile.compile(SRC, doraise=True)
                log('源码已更新，原地重载'); sys.stdout.flush(); sys.stderr.flush()
                os.execv(sys.executable, [sys.executable, SRC] + sys.argv[1:])
        except Exception as ex:
            log(f'重载跳过（编译失败或其他）：{repr(ex)[:120]}'); SRC_MT = os.path.getmtime(SRC)
        t = time.time(); beat[0] = t
        slk = open(STATE + '.lock', 'w'); fcntl.flock(slk, fcntl.LOCK_EX)
        try:
            st = load(STATE, {})
            live = cycle(st)
            if live.get('err') or live.get('fail_block'): log(f"本轮未全部成功：{live.get('err')} fail_block={live.get('fail_block')}（游标停在 {live['cursor']}）")
            maybe_reconcile(st); dump(STATE, st)
        except Exception as e:
            log(f'本轮异常（游标不动）：{str(e)[:200]}')
            try:
                lv = load(LIVE, {}); lv.update(heartbeat=int(time.time()), pid=os.getpid(), err=str(e)[:200], time_cst=now_cst()); dump(LIVE, lv)
            except Exception: pass
        finally: fcntl.flock(slk, fcntl.LOCK_UN); slk.close()
        # 2026-11-02：顺手每轮（约 60 秒）跑一次 fmkick.sh 看门狗：fastprogress/possell 常驻进程寿命 14 分钟、原来只靠定时任务每 5 分钟拉起，
        # 中间最多有 5 分钟空档（16:5x 实测 fastprogress 不在）。现在任何常驻进程掉了 ≤60 秒就被拉起；flock 保证 fmkick 不重叠。
        if '--no-kick' not in argv:
            try: subprocess.Popen(['bash', '-c', f'cd {L} && flock -n /tmp/fmkick.lock bash fmkick.sh > {OUTD}/fmkick_auto.json 2>&1'], start_new_session=True,
                                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception: pass
            # 2026-11-03 用户「为什么这个免费的没自动打成功」：BurnAgentMixed 类首份免费，每轮（约 60 秒）检查一次，flock 不重叠
            try: subprocess.Popen(['bash', '-c', f'cd {L} && flock -n /tmp/burnfree.lock timeout 600 python3 burnfree_watch.py >> {OUTD}/burnfree.log 2>&1'], start_new_session=True,
                                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception: pass
            # 2026-11-03 01:56 用户「要」每天自动挖 +「这种为什么没监控到 自动打」：押金全退型挖矿（AGC 模板）自动挖/领回
            try: subprocess.Popen(['bash', '-c', f'cd {L} && flock -n /tmp/refundmine.lock timeout 300 python3 refundmine.py >> {OUTD}/refundmine.log 2>&1'], start_new_session=True,
                                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception: pass
            # 2026-11-07 01:35 用户「打」：AGD A 档 24h 到期自动 claimA 并全卖
            try: subprocess.Popen(['bash', '-c', f'cd {L} && flock -n /tmp/agdclaim.lock timeout 300 python3 agd_claim.py >> {OUTD}/agd_claim.log 2>&1'], start_new_session=True,
                                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception: pass
            # 2026-11-05 用户「彻底解决同时掉线」：会话看门狗，约 10 分钟一次，只读
            if time.time() - SW_LAST[0] >= 590:
                SW_LAST[0] = time.time()
                try: subprocess.Popen(['bash', '-c', f'cd {L} && flock -n /tmp/sessionwatch.lock timeout 300 python3 session_watch.py >> {OUTD}/session_watch.log 2>&1'], start_new_session=True,
                                      stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except Exception: pass
                # 2026-11-05 主号 BNB 掉到 10U 没被补（巡逻没按时跑 gas_topup），导致 AGC A 档 10 份没打：常驻每 10 分钟补一次（用户 11-01 19:34 授权 <25U 补到 30U）
                try: subprocess.Popen(['bash', '-c', f'cd {L} && flock -n /tmp/gastopup.lock timeout 200 python3 gas_topup.py >> {OUTD}/gas_topup.log 2>&1'], start_new_session=True,
                                      stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except Exception: pass
            # 2026-11-03 02:08 用户「要」：开售定时预埋免费份
            try: subprocess.Popen(['bash', '-c', f'cd {L} && flock -n /tmp/salearm.lock timeout 110 python3 salearm.py >> {OUTD}/salearm.log 2>&1'], start_new_session=True,
                                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception: pass
            # 2026-11-03 02:01 用户要求盯 KOL 阿峰_Afeng 的 AGC 买卖（只读只提醒）
            try: subprocess.Popen(['bash', '-c', f'cd {L} && flock -n /tmp/kolwatch.lock timeout 120 python3 kolwatch.py >> {OUTD}/kolwatch.log 2>&1'], start_new_session=True,
                                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception: pass
            # 2026-11-03 新模板自动识别（只读，不交易）：不在已知模板里的热门付费/挖矿/铸造合约自动分析，约 120 秒一次
            if time.time() - NEWTPL_LAST[0] >= 115:
                NEWTPL_LAST[0] = time.time()
                try: subprocess.Popen(['bash', '-c', f'cd {L} && flock -n /tmp/newtpl.lock timeout 170 python3 newtpl.py >> {OUTD}/newtpl.log 2>&1'], start_new_session=True,
                                      stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except Exception: pass
        lag = load(LIVE, {}).get('lag_blocks') or 0
        gap = 5 if lag > SEG * 2 else max(5, EVERY - (time.time() - t))  # 落后时连续补，追上后约 60 秒一轮
        time.sleep(gap)


# ---------------- 回放（只读） ----------------
def replay(f, t, sim_every=60):
    """历史回放：从 f 到 t，按「每 sim_every 秒一轮」模拟（BSC 约 0.45 秒/块 → 每轮约 133 块），用与常驻进程相同的 handle_logs/judge 判定，
    记录每个合约「最早被哪一轮发现」。不写 found.json、不写状态（found 用空表，判断“是否新”只看回放内部）。"""
    F.T0 = time.time(); F.DEADLINE = F.T0 + 3000
    heads = node_heads()
    st = {'devs': {}, 'checked': {}, 'pair_tx_seen': {}, 'other': {}}
    ctx = Ctx(st, {}, replay=True)
    per = max(1, int(sim_every / 0.45)); first = {}
    logs, to, err, _ = scan_all(f, t, [ALL_TOPICS], heads, time.time() + 2400)
    if err: sys.stderr.write(f'replay 扫到 {to} 停：{err}\n')
    logs.sort(key=lambda l: (int(l['blockNumber'], 16), int(l['logIndex'], 16)))
    # 按轮切分
    rounds = {}
    for l in logs: rounds.setdefault((int(l['blockNumber'], 16) - f) // per, []).append(l)
    for r in sorted(rounds):
        before = set(ctx.adds); end_blk = f + (r + 1) * per - 1
        handle_logs(ctx, rounds[r])
        poll_devs(ctx, end_blk)
        for a in set(ctx.adds) - before: first[a] = {'round_end_block': min(end_blk, t), 'name': ctx.adds[a].get('name'), 'source': ctx.adds[a].get('source')}
        for e in ctx.events:
            if e.get('kind') == 'agentid_deployed' and e['contract'] not in first: first[e['contract']] = {'round_end_block': min(end_blk, t), 'name': e.get('name'), 'source': 'agentid_deployed'}
    print(json.dumps({'from': f, 'to': to, 'err': err, 'logs': len(logs), 'found': first}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    if '--daemon' in argv: daemon()
    elif '--reconcile' in argv: reconcile(float(argv[argv.index('--hours') + 1]) if '--hours' in argv else 2.0)
    elif '--replay' in argv:
        i = argv.index('--replay'); replay(int(argv[i + 1]), int(argv[i + 2]), float(argv[argv.index('--sim-every') + 1]) if '--sim-every' in argv else 60)
    else:
        slk = open(STATE + '.lock', 'w'); fcntl.flock(slk, fcntl.LOCK_EX)
        st = load(STATE, {}); print(json.dumps(cycle(st), ensure_ascii=False, indent=1))
