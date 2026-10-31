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
OPENED = '0x1235c671a6b39ea1918628853eceb400b8413f8cdb8ca6110e250065affa20d3'  # Opened(uint256,uint256,uint256)（Jager/AGI）
OPEN_TOPICS = [OPENED] + ['0x' + CU.kec(x.encode()).hex() for x in ('EarlyOpened(uint256)', 'PoolOpened(address,uint256,uint256)', 'Finalized(address,uint256,uint256,uint256,address)', 'LiquidityTargetReached(uint256,uint256,uint256)')]  # 2026-11-01：提前开池/其他模板开池事件
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


RPC_FAIL = [0]  # 本轮最终没读到（非 revert）的调用数


def _retryable(err):
    """2026-11-01 审计：blockrazor 限流回的是 'too many requests'（不含 limit/rate），原来被当成「函数不存在」静默返回 None，
    批量读进度时一半合约读空、found.json 保留旧值、Δ 显示 0。现在只有 revert 才算「没有这个函数」，其余错误一律换节点重试。"""
    m = str((err or {}).get('message', err) if isinstance(err, dict) else err).lower()
    return 'revert' not in m and 'invalid opcode' not in m


def rpc(m, p):
    err = None
    for i in range(3):
        for r in _nodes():
            try:
                d = _post(r, {"jsonrpc": "2.0", "id": 1, "method": m, "params": p})
                if 'result' in d: return d['result']
                err = d.get('error')
                if m == 'eth_call' and not _retryable(err): return None  # revert：函数不存在，不重试
                BAD[r] = time.time() + 120
            except Exception as e: err = e; BAD[r] = time.time() + 120
            if time.time() > DEADLINE - 5: raise RuntimeError(f'deadline: {err}')
        time.sleep(1.5 * (i + 1))
    raise RuntimeError(err)


def bat(calls, chunk=40):
    """批量 JSON-RPC：[(method, params)] -> [result|None]。revert 的单项返回 None；限流/节点错误的单项换节点只重发失败项。"""
    out = []
    for k in range(0, len(calls), chunk):
        if k: time.sleep(0.35)  # QuickNode 125 次/秒按批内条数计，连发会被限流
        part = calls[k:k + chunk]; res = [None] * len(part); pending = list(range(len(part)))
        for rnd in range(2):
            for u in _nodes():
                if not pending: break
                if time.time() > DEADLINE - 8: break
                body = [{'jsonrpc': '2.0', 'id': i, 'method': part[i][0], 'params': part[i][1]} for i in pending]
                try:
                    r = _post(u, body, 20)
                    if not isinstance(r, list): raise ValueError(str(r)[:100])
                    r = {x.get('id'): x for x in r}; left = []
                    for i in pending:
                        x = r.get(i)
                        if x is None: left.append(i)
                        elif 'result' in x: res[i] = x['result']
                        elif _retryable(x.get('error')): left.append(i)
                        # revert：保持 None
                    if len(left) > len(pending) // 4: BAD[u] = time.time() + 120
                    pending = left
                except Exception: BAD[u] = time.time() + 120
            if not pending: break
            time.sleep(1.5)
        RPC_FAIL[0] += len(pending)
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
# 2026-11-01 审计：模板识别。原来只读 Jager/AGI 字段（totalMinted/OPEN_AT/opened），JAGENT 类（sharesMinted/MAX_SHARES/poolOpen）、
# BossMint（totalMinted/MAX_MINTS/finalized）、Mao（totalPaidShares/LIQUIDITY_TARGET_SHARES）三类同 Minted 事件签名的盘进度一直是 0。
# 字段：key -> (函数签名, 参数)
F = {'cost_q': ('bnbQuote(uint256)', ONE), 'cost_c': ('costOf(uint256)', ONE), 'opened_raw': ('opened()', ''),
     'total_minted': ('totalMinted()', ''), 'total_shares': ('TOTAL_SHARES()', ''), 'total_shares2': ('totalShares()', ''),
     'total_paid_wei': ('totalPaid()', ''), 'open_at_shares': ('OPEN_AT()', ''), 'open_units': ('OPEN_UNITS()', ''), 'open_bnb_wei': ('OPEN_BNB()', ''),
     'pending_bnb_wei': ('pendingBnb()', ''), 'treasury_bps': ('TREASURY_BPS()', ''),
     'max_per_tx': ('MAX_PER_TX()', ''), 'paused': ('paused()', ''), 'renounced': ('renounced()', ''),
     'token_raw': ('token()', ''), 'pair_raw': ('pair()', ''),
     # JAGENT 类（jager.live JAGENT/JAGENTV2、HALLOWEEN、币安大表哥 DaBiaoGe）：卖满 MAX_SHARES 自动开池；deadline 后任何人可 openPool()
     'shares_minted': ('sharesMinted()', ''), 'max_shares': ('MAX_SHARES()', ''), 'pool_open': ('poolOpen()', ''),
     'price_wei': ('PRICE()', ''), 'deadline': ('deadline()', ''), 'usdt_raw': ('usdt()', ''), 'wbnb_raw': ('wbnb()', ''),
     # BossMint（Agent Boss）：卖满 MAX_MINTS 后 finalize() 开池；未卖满到期可退款
     'max_mints': ('MAX_MINTS()', ''), 'finalized': ('finalized()', ''),
     # Mao（Agent Mao）：付费份数到 LIQUIDITY_TARGET_SHARES 触发 LiquidityTargetReached
     'total_paid_shares': ('totalPaidShares()', ''), 'liq_target': ('LIQUIDITY_TARGET_SHARES()', ''), 'liq_reached': ('liquidityTargetReached()', '')}
ADDR_KEYS = {'token_raw', 'pair_raw', 'usdt_raw', 'wbnb_raw'}
TPL = {
    'jager': ['cost_q', 'cost_c', 'opened_raw', 'total_minted', 'total_shares', 'total_shares2', 'total_paid_wei', 'open_at_shares', 'open_units',
              'open_bnb_wei', 'pending_bnb_wei', 'treasury_bps', 'max_per_tx', 'paused', 'renounced', 'token_raw', 'pair_raw', 'deadline'],
    'jagent': ['shares_minted', 'max_shares', 'pool_open', 'price_wei', 'deadline', 'usdt_raw', 'wbnb_raw', 'token_raw', 'pair_raw'],
    'boss': ['total_minted', 'max_mints', 'finalized', 'price_wei', 'deadline', 'token_raw', 'pair_raw'],
    'mao': ['total_paid_shares', 'total_shares2', 'liq_target', 'liq_reached', 'price_wei', 'max_shares', 'token_raw'],
}
# 旧版读的字段（未识别模板、且本轮识别名额用完时用，保证不比原来慢）
LEGACY = ['cost_q', 'cost_c', 'opened_raw', 'total_minted', 'total_shares', 'total_shares2', 'total_paid_wei', 'open_at_shares', 'open_bnb_wei',
          'max_per_tx', 'paused', 'renounced', 'token_raw', 'pair_raw']
TPL['other'] = LEGACY
UNION = list(F)
CORE = {'jager': 'total_minted', 'jagent': 'shares_minted', 'boss': 'total_minted', 'mao': 'total_paid_shares'}
TEMPLATE_BUDGET = int(os.environ.get('FM_TPL_BUDGET', '40'))  # 每轮最多给多少个未识别合约读全量字段（首次识别 132 个旧盘分几轮做完，避免超时）
EARLY_SELS = {'openEarly()': 'treasury', 'openEarlyFor(address)': 'treasury', 'openPool()': 'anyone_after_deadline', 'forceOpen()': 'owner', 'openTrading()': 'owner?'}


def _int(r): return int(r[:66], 16) if isinstance(r, str) and len(r) >= 66 else None
def _str(r):
    try: return bytes.fromhex(r[130:130 + 2 * int(r[66:130], 16)]).decode(errors='ignore') if r and len(r) > 130 else None
    except Exception: return None
def _addr(r): return '0x' + r[26:66] if isinstance(r, str) and len(r) >= 66 else None


def classify(raw):
    g = lambda k: raw.get(k)
    if g('total_minted') is not None and (g('open_at_shares') or g('open_units')): return 'jager'
    if g('liq_target') and g('total_paid_shares') is not None: return 'mao'
    if g('shares_minted') is not None and g('max_shares') and g('pool_open') is not None: return 'jagent'
    if g('total_minted') is not None and g('max_mints') and g('finalized') is not None: return 'boss'
    if any(g(k) is not None for k in ('token_raw', 'cost_c', 'cost_q', 'total_minted', 'opened_raw')): return 'other'
    return None  # 本轮全读失败：不下结论，下轮再识别


def normalize(raw, tpl):
    """把各模板的原始读数统一成：total_minted / open_at_shares / opened / cost_1share_* / open_bnb_progress_pct 等。"""
    i = {}; g = raw.get
    if tpl == 'jagent':
        i['total_minted'] = g('shares_minted'); i['open_at_shares'] = g('max_shares'); i['open_at_src'] = 'MAX_SHARES(卖满自动开池)'
        i['opened'] = bool(g('pool_open')) if g('pool_open') is not None else None
        if g('price_wei'):
            if g('usdt_raw') and int(g('usdt_raw'), 16): i['cost_1share_token_units'] = g('price_wei') / 1e18; i['price_currency'] = 'USDT'
            else: i['cost_1share_bnb'] = g('price_wei') / 1e18
        i['can_open_early'] = 'anyone_after_deadline'
    elif tpl == 'boss':
        i['total_minted'] = g('total_minted'); i['open_at_shares'] = g('max_mints'); i['open_at_src'] = 'MAX_MINTS(卖满后 finalize 开池)'
        i['opened'] = bool(g('finalized')) if g('finalized') is not None else None
        if g('price_wei'): i['cost_1share_bnb'] = g('price_wei') / 1e18
    elif tpl == 'mao':
        i['total_minted'] = g('total_paid_shares'); i['open_at_shares'] = g('liq_target'); i['open_at_src'] = 'LIQUIDITY_TARGET_SHARES(付费份数达标)'
        i['opened'] = bool(g('liq_reached')) if g('liq_reached') is not None else None
        if g('price_wei'): i['cost_1share_bnb'] = g('price_wei') / 1e18
        if g('total_shares2') is not None: i['total_shares_incl_free'] = g('total_shares2')
    else:  # jager / other：保持原逻辑
        q, c, o = g('cost_q'), g('cost_c'), g('opened_raw')
        i['cost_1share_bnb'] = (q or c) / 1e18 if (q or c) else None  # bnbQuote 优先；有些克隆用 USD1/FIST 等代币计价，costOf 是代币数量
        if q and c and c != q: i['cost_1share_token_units'] = c / 1e18
        i['opened'] = bool(o) if o is not None else None
        for k in ('total_minted', 'total_shares', 'total_shares2', 'total_paid_wei', 'open_bnb_wei', 'max_per_tx', 'paused', 'renounced', 'treasury_bps', 'pending_bnb_wei'):
            if g(k) is not None: i[k] = g(k)
        oa = g('open_at_shares') or g('open_units')
        if oa: i['open_at_shares'] = oa; i['open_at_src'] = 'OPEN_AT' if g('open_at_shares') else 'OPEN_UNITS'
    if g('deadline'): i['deadline_ts'] = g('deadline')
    for k in ('token', 'pair'):
        v = _addr(raw.get(k + '_raw')) if raw.get(k + '_raw') else None
        if v: i[k] = v
    return i


def info_many(addrs, prev=None, code_cache=None):
    """批量读多个合约的 fair mint 参数（按已识别模板只读需要的字段，合并成一次批量请求）。"""
    prev = prev or {}; code_cache = code_cache if code_cache is not None else {}
    addrs = list(dict.fromkeys(addrs)); calls = []; plan = {}; budget = [TEMPLATE_BUDGET]
    for a in addrs:
        tpl = prev.get(a, {}).get('template')
        recheck = tpl == 'other' and time.time() - prev.get(a, {}).get('_tpl_at', 0) > 6 * 3600  # 'other' 每 6 小时重识别一次，防一次读失败被永久归错
        if tpl in TPL and not (recheck and budget[0] > 0): keys = TPL[tpl]
        elif budget[0] > 0: keys = UNION; budget[0] -= 1
        else: keys = LEGACY
        plan[a] = (tpl, keys)
        calls += [('eth_call', [{'to': a, 'data': sel(F[k][0]) + F[k][1]}, 'latest']) for k in keys]
        if a not in code_cache and not prev.get(a, {}).get('_code_checked'): calls.append(('eth_getCode', [a, 'latest']))
    R = bat(calls); k = 0; out = {}
    for a in addrs:
        tpl, keys = plan[a]; raw = {}
        for key in keys:
            r = R[k]; k += 1
            raw[key] = r if key in ADDR_KEYS else _int(r)
        if a not in code_cache and not prev.get(a, {}).get('_code_checked'):
            code_cache[a] = (R[k] or '0x').lower(); k += 1
        i = {'contract': a}
        if keys is UNION:
            tpl = classify(raw) or (tpl if tpl in TPL else None)
            if tpl: i['_tpl_at'] = int(time.time())
        if tpl: i['template'] = tpl
        i.update(normalize(raw, tpl or 'other'))
        core = CORE.get(tpl or 'other', 'total_minted')
        i['_read_ok'] = raw.get(core) is not None if tpl in CORE else any(v is not None for v in raw.values())
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
            ce = [who for s, who in EARLY_SELS.items() if ('63' + CU.sel(s)) in fc]
            if ce and 'can_open_early' not in i: i['can_open_early'] = ce[0]
            i['_code_v2'] = True
        tm, ts = i.get('total_minted'), i.get('total_shares') or i.get('total_shares2')
        if tm is not None and ts and i.get('template') in (None, 'jager', 'other'): i['progress_pct'] = round(tm * 100 / ts, 2)
        if i.get('open_at_shares') and tm is not None: i['open_progress_pct'] = round(min(tm * 100 / i['open_at_shares'], 999), 2)
        # 2026-11-01 审计（币安人）：OPEN_BNB 是开池时注入池子的 BNB（=开池份数×单价×(1-国库抽成)），不是另一个门槛。
        # 原来用 totalPaid（含 20% 国库抽成、开池后的付款）去比，币安人 8000 份时就显示 100%，max() 后会在真实 56% 份数时触发 70% 打满。
        # 改为：有 pendingBnb（开池储备）就用 pendingBnb/OPEN_BNB（与份数进度一致）；有 OPEN_AT 但读不到 pendingBnb 时不用 BNB 进度；
        # 只有 OPEN_BNB 没有开池份数的，才按 totalPaid×(1-TREASURY_BPS) 估算并标 open_bnb_est。
        ob, pb, tp = i.get('open_bnb_wei'), i.get('pending_bnb_wei'), i.get('total_paid_wei')
        if ob and not i.get('opened'):
            if pb is not None: i['open_bnb_progress_pct'] = round(pb * 100 / ob, 2)
            elif tp and not i.get('open_at_shares'):
                bps = i.get('treasury_bps') or 0
                i['open_bnb_progress_pct'] = round(tp * (10000 - bps) / 10000 * 100 / ob, 2); i['open_bnb_est'] = True
        if tp and ob: i['raised_vs_open_bnb_pct'] = round(tp * 100 / ob, 2)  # 仅供参考：总募资/开池注入额，含国库抽成，不作为进度
        for f in ('total_paid_wei', 'open_bnb_wei', 'pending_bnb_wei'):
            if f in i: i[f.replace('_wei', '_bnb')] = i.pop(f) / 1e18
    return out


def info(a):
    return info_many([a])[a]


def guess_cap(a, v):
    """读不到 OPEN_AT/OPEN_BNB 时自动找总量函数（v2.5.1）。结果缓存 cap_sel；只作提醒参考。"""
    sold = v.get('total_minted')
    if sold is None or v.get('open_at_shares') or v.get('open_bnb_bnb'): return
    if v.get('cost_1share_bnb') is None and not v.get('_has_minted_evt'): return  # 不像 fair mint（读不到单价、码里没有 Minted 事件）不猜
    # 2026-11-01：Hallowed Agents 误报教训——ERC721 NFT 的 publicSupply 被当成开池份数。NFT 不是代币盘，没有开池，直接排除；顺手补项目名。
    if 'is_nft' not in v:
        r = call(a, '0x01ffc9a780ac58cd' + '0' * 56)
        v['is_nft'] = bool(isinstance(r, str) and len(r) >= 66 and int(r, 16) == 1)
    if not v.get('name'):
        try: v['name'] = _str(call(a, '0x06fdde03')) or None
        except Exception: pass
    if v.get('is_nft'):
        v.pop('cap_guess', None); v.pop('open_pct_guess', None); return
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
    # 2026-11-01 审计：found.json/state.json 是「读-改-写」，两份 fairmint 同时跑（巡逻 + 手动/快打任务）后写的会覆盖先写的。加独占锁。
    import fcntl
    global _LOCK
    _LOCK = open(f'{SD}/.fairmint.lock', 'w')
    while True:
        try: fcntl.flock(_LOCK, fcntl.LOCK_EX | fcntl.LOCK_NB); break
        except OSError:
            if time.time() > DEADLINE - 45:
                print(json.dumps({'error': 'another fairmint.py is running (lock busy); 沿用 discover_out/fairmint.json 上一份输出', 'lock_busy': True}, ensure_ascii=False)); sys.exit(3)
            time.sleep(2)
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
    # 2026-11-01 审计：原来某段 getLogs 失败就 f=t+1 跳过、且最后无条件 st['last']=head —— 失败段里的新盘永久漏掉。
    # 现在失败段换节点重试 2 次（缩小区间），仍失败就停在这里，st['last'] 只推进到最后一段成功的块，下轮从断点续扫。
    f = start; scanned_to = start - 1; seg_fail = 0
    while f <= head:
        t = min(f + (RANGE if seg_fail == 0 else max(200, RANGE // (2 * seg_fail))) - 1, head)
        try:
            logs = rpc('eth_getLogs', [{'fromBlock': hex(f), 'toBlock': hex(t), 'topics': [TOPICS + OPEN_TOPICS]}])
            for l in logs:
                a = l['address'].lower()
                if l['topics'][0] != TOPICS[0]:  # 开池类事件：只标记已知盘，陌生合约（同名事件很常见）不当成新 fair mint
                    if a in found or a in seen_now: seen_now.setdefault(a, {'mints': 0, 'opened_evt': False, 'first_block': int(l['blockNumber'], 16)})['opened_evt'] = True
                    continue
                e = seen_now.setdefault(a, {'mints': 0, 'opened_evt': False, 'first_block': int(l['blockNumber'], 16)}); e['mints'] += 1
            scanned_to = t; f = t + 1; seg_fail = 0; sys.stderr.write(f'logs {t} n={len(seen_now)}\n')
        except Exception:
            errors += 1; seg_fail += 1
            if seg_fail > 2 or errors > 8 or time.time() > DEADLINE - 40: break
            time.sleep(1)
    prev_open = {a: v.get('opened') for a, v in found.items()}
    prev_minted = {a: v.get('total_minted') for a, v in found.items()}
    prev_tpl = {a: v.get('template') for a, v in found.items()}  # 本轮刚识别出模板的盘：第一次算出的进度不算「暴涨」、opened 变化不算「刚开池」
    # ② 已部署未开铸
    pre_new, pre_other, pre_err = [], [], None
    try: pre_new, pre_other = predeploy(st, found, start, head)
    except Exception as e: pre_err = str(e)[:160]
    # ①③ 本轮有事件的 + 全部未开池（3 天内发现的）一次批量刷新
    now = time.time()
    def fresh(v):  # first_seen_cst 是北京时间
        # 2026-11-01 审计：3 天 → 7 天（Jager 克隆铸造窗口 30 天，慢盘第 4 天起就不再刷新）；进度 ≥30% 的不论多老都刷新
        try: return now - (calendar.timegm(time.strptime(v.get('first_seen_cst'), '%Y-%m-%d %H:%M')) - 8 * 3600) < 7 * 86400
        except Exception: return True
    unopened = [a for a, v in found.items() if not v.get('opened') and not v.get('is_nft') and a not in seen_now
                and (v.get('premint') or fresh(v) or (v.get('last_open_pct') or 0) >= 30)]
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
            if i.get('_read_ok'):  # 本轮读到了：清掉旧算法留下的 BNB 进度（币安人 213% 那类），以本轮为准
                for k2 in ('open_bnb_progress_pct', 'open_bnb_est'):
                    if k2 not in i: found[a].pop(k2, None)
            found[a].update({k: v for k, v in i.items() if v is not None})
            if a in seen_now: found[a]['mints_in_scan'] = seen_now[a]['mints']
    # 名称补读（2026-11-01 03:07 用户问「未命名」：首次批量读 token()/name() 偶发失败后一直为空）
    for a in [a for a in targets if not found[a].get('name')]:
        try:
            r = info_many([a], code_cache={a: '0x'})[a]
            for k2 in ('token', 'name', 'symbol'):
                if r.get(k2): found[a][k2] = r[k2]
        except Exception: pass
    # 旧盘补查「能否提前开池」（openEarly/openPool 选择器），每轮最多 15 个，结果缓存
    for a in [a for a in targets if not found[a].get('_code_v2')][:15]:
        if time.time() > DEADLINE - 50: break
        try:
            fc = CU.full_code(rpc, call, a)
            if len(fc) > 4:
                ce = [who for s_, who in EARLY_SELS.items() if ('63' + CU.sel(s_)) in fc]
                if ce and 'can_open_early' not in found[a]: found[a]['can_open_early'] = ce[0]
                found[a]['_code_v2'] = True
        except Exception: pass
    # ④ 读不到开池份数的：自动找总量函数（只提醒）
    for a in targets:
        try: guess_cap(a, found[a])
        except Exception: pass
    backfill_deployers(found, head)
    stale_set = {a for a in targets if not infos.get(a, {}).get('_read_ok')}
    for a in targets: found[a].pop('_read_ok', None)
    def save():
        st['last'] = max(st.get('last', 0), scanned_to)
        json.dump(st, open(ST + '.tmp', 'w')); os.replace(ST + '.tmp', ST)
        json.dump(found, open(OUT + '.tmp', 'w'), ensure_ascii=False, indent=1); os.replace(OUT + '.tmp', OUT)
    save()  # 2026-11-01 审计：先落一次盘，后面 20 秒重读阶段被外层 timeout 杀掉也不会整轮白跑

    near_guess = []
    def build_rows():
        watch, near, just_opened, started = [], [], [], []; near_guess.clear()
        for a in set(targets):
            v = found[a]
            if v.get('is_nft'): continue  # NFT 没有开池，不进开池进度
            op, guessed = open_pct(v)
            last = v.get('last_open_pct'); d = round(op - last, 2) if last is not None else 0
            first_tpl = a in prev_tpl and not prev_tpl.get(a) and v.get('template') not in (None, 'other')
            if first_tpl: d = 0
            row = {'contract': a, 'name': v.get('name'), 'cost_1share_bnb': v.get('cost_1share_bnb'), 'open_pct': op, 'delta_pct': d,
                   'total_minted': v.get('total_minted'), 'open_at_shares': v.get('open_at_shares') or v.get('cap_guess'),
                   'has_refund': v.get('has_refund'), 'template': v.get('template'), 'bscscan': f'https://bscscan.com/address/{a}'}
            if v.get('token'): row['token_url'] = f"https://bscscan.com/token/{v['token']}"
            if v.get('cost_1share_token_units') and not v.get('cost_1share_bnb'): row['cost_1share_token_units'] = v['cost_1share_token_units']; row['price_currency'] = v.get('price_currency')
            if v.get('template') not in (None, 'jager'): row['autofill_supported'] = False  # fair_buy.py 只会 Jager/AGI 的 quotaOf/costOf/mint：其他模板 ≥70% 只提醒
            if v.get('can_open_early'): row['can_open_early'] = v['can_open_early']
            if v.get('deadline_ts'): row['deadline_cst'] = time.strftime('%m-%d %H:%M', time.gmtime(v['deadline_ts'] + 8 * 3600))
            if a in stale_set: row['stale'] = True  # 本轮读数失败，显示的是上一轮的数
            if v.get('open_at_src'): row['open_at_src'] = v['open_at_src']
            if guessed: row['open_pct_guess'] = True  # 总量是猜的：只提醒，autofill 不自动买
            if v.get('premint'): row['premint'] = True
            if v.get('deployer'): row['dev_contracts'] = sum(1 for x in found.values() if x.get('deployer') == v['deployer'])  # 同一部署者的 fair mint 数（≥3 = 重复 DEV）
            if v.get('opened'):
                if (prev_open.get(a) is False or (prev_open.get(a) is None and a in seen_now and last is not None)) and not first_tpl:
                    # 提前开池：开池时已铸份数 < 开池份数（币安人 openEarly / JAGENT 到期 openPool）
                    if v.get('open_at_shares') and (v.get('total_minted') or 0) < v['open_at_shares']: row['early_open'] = True; row['open_pct_at_open'] = last
                    just_opened.append(row)
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
            for a in hs:
                found[a].update({k: v for k, v in ii[a].items() if v is not None and k != '_read_ok'})
                if ii[a].get('_read_ok'): stale_set.discard(a)
            for a in hs:
                try: guess_cap(a, found[a])
                except Exception: pass
            watch, near, just_opened2, started2 = build_rows()
            just_opened += [r for r in just_opened2 if r['contract'] not in {x['contract'] for x in just_opened}]
            started += [r for r in started2 if r['contract'] not in {x['contract'] for x in started}]
            if any(r['open_pct'] >= 70 for r in watch if r['contract'] in hs): break  # 过 70% 立刻交给巡逻处理
    for a in set(targets):
        if a not in stale_set: found[a]['last_open_pct'] = open_pct(found[a])[0]  # 读失败的不更新基线，下轮 Δ 才准
    save()
    print(json.dumps({'head': head, 'blocks': head - start + 1, 'scanned_to': scanned_to, 'blocks_left': head - scanned_to, 'log_errors': errors,
                      'read_failed': len(stale_set), 'refreshed': len(set(targets)), 'rpc_failed_calls': RPC_FAIL[0],
                      'rpc_degraded': bool(targets) and len(stale_set) > len(set(targets)) / 2,  # 过半读失败：进度是旧数，巡逻要写「进度读取失败」，不能当作无变化
                      'early_open_capable': sorted({r['contract'] for r in watch if r.get('can_open_early')}), 'active_contracts': len(seen_now), 'elapsed_s': round(time.time() - T0, 1), 'rechecks': rechecks,
                      'new': new,
                      'premint_new': pre_new,          # 已部署未开铸的 fair mint（新加入观察）→ 消息里单独一行「已部署未开铸」
                      'mint_started': started,         # 观察中的未开铸盘本轮开铸 → 第一行提醒
                      'agentid_deployed': pre_other,   # 其他新部署的 Agent ID 专属合约（可能是免费 mint）→ 交给 precheck/人工核验
                      'premint_watch': [r for r in watch if r.get('premint')], 'predeploy_error': pre_err, 'watched_devs': len(st.get('pre_devs', {})),
                      'watch_unopened': sorted(watch, key=lambda r: -r['open_pct']), 'near_open': near, 'near_open_guess': near_guess, 'just_opened': just_opened,
                      'active': {a: {k: found[a].get(k) for k in ('cost_1share_bnb', 'opened', 'progress_pct', 'open_progress_pct', 'open_bnb_progress_pct', 'mints_in_scan')} for a in seen_now}},
                     ensure_ascii=False, indent=1))


if __name__ == '__main__': main()
