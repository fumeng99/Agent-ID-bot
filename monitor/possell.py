#!/usr/bin/env python3
"""持仓自动卖（用户 2026-11-01 16:47「按照你推荐来」采纳  v2.6.5 卖出规则 B3 + B4；B5 不扩全天）。
付费仓位（auto_buy.json positions，含自动买/打满/手动确认买入）：
  回本前：涨到成本 2 倍 → 卖 50%（回本，一次）；从开池后最高价回落 40% → 全卖（移动止损，取代原「跌到买入价 -40%」）
  回本后：10 倍 → 卖剩余 30%；20 倍 → 再卖剩余 50%；从最高价回落 60% → 卖到只剩原始数量 20%（长抱，不自动卖）；50/100 倍只通知
免费铸造所得（seen.json 免费类且有 token）：开池 3 分钟后，从最高价回落 50% → 全卖。
2026-11-01 19:54 用户「按照你推荐来」：回撤门槛改为 回本前 35% 全卖、回本后 50% 卖到留 20%、免费所得 40% 全卖（替代 40/60/50）。
通用：每号价值 <0.3U 不卖；卖失败按 1/2/4/8… 分钟退避重试，价值 <3U 的失败 6 次放弃；各号并行（fast_sell.py）。
2026-11-01 18:45 用户「按照你推荐来」（A–D）：A 自动买入（auto_buy 非 manual）和免费所得全天自动卖；手动确认买入的仍只在 00:00–09:00 自动卖、白天提醒。
B 常驻：--daemon 写 possell_live.json 心跳，持仓盘开池 15 分钟内 3 秒一轮，否则 20 秒（fmkick.sh 拉起）。
C 开池 10 分钟内曾到 1.5 倍、又跌回成本 → 卖 50% 保本（一次，算作回本）。
D 预估冲击 >15%（卖出额 > 池子单边 15%）就分两笔、间隔 15 秒；滑点上限 15%；fast_sell 每号先 quote 预演，预演失败不下单。价格取 DexScreener BSC 流动性最大的池子，连续 2 次低于门槛才算。
用法：python3 possell.py [--dry] [--loop 秒 --every 秒]
输出 JSON：sold / alerts / notify / errors
"""
import os, sys, json, time, subprocess, urllib.request
sys.path.insert(0, '$AIDBOT_HOME/pylib'); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rpcpool import batch
D = os.path.dirname(os.path.abspath(__file__))
ST = f'{D}/possell_state.json'; ACC = json.load(open('$AIDBOT_HOME/baw/accounts/accounts.json'))
LAB = {a['label']: a['bsc'].lower() for a in ACC}
args = sys.argv[1:]; DRY = '--dry' in args
def opt(k, d): return float(args[args.index(k) + 1]) if k in args else d
LOOP, EVERY = opt('--loop', 0), opt('--every', 20)
R = dict(backX=2, steps=[(10, 30), (20, 50)], crash=0.35, crashAfterBack=0.50, moonPct=20, moonNotify=[50, 100], notifyX=[5, 10, 20],
         freeDrop=0.40, freeWaitSec=180, minU=0.3, smallU=3, smallMaxFail=6, earlyX=1.5, earlyWin=600, impact=0.15, fastWin=1800)


def cst_hour(): return time.gmtime(time.time() + 8 * 3600).tm_hour
def night(): return 0 <= cst_hour() < 9
def now(): return time.strftime('%m-%d %H:%M', time.gmtime(time.time() + 8 * 3600))


def get(u):
    return json.load(urllib.request.urlopen(urllib.request.Request(u, headers={'User-Agent': 'Mozilla/5.0'}), timeout=15))


def chain_prices(pairs):
    """2026-11-03 用户「太慢了 这种开盘的波动很快的」：已知池子的直接链上读 getReserves（每块新鲜），不等 DexScreener 索引延迟。
    pairs: {token: pair}，只支持代币/WBNB 池；读失败的返回里没有，调用方再走 DexScreener。"""
    out = {}; W = 'bb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c'
    miss = [t for t in pairs if not pairs[t]]
    if miss:  # 刚开池还没池子地址：PancakeSwap V2 工厂 getPair 链上直接查
        try:
            prs = batch([('eth_call', [{'to': '0xca143ce32fe78f1f7019d7d551a6402fc5350c73', 'data': '0xe6a43905' + t[2:].rjust(64, '0') + W.rjust(64, '0')}, 'latest']) for t in miss], tolerant=True)
            for t, r in zip(miss, prs):
                if isinstance(r, str) and len(r) >= 66 and int(r, 16): pairs[t] = '0x' + r[-40:]
        except Exception: pass
    ks = [t for t in pairs if pairs[t]]
    if not ks: return out
    bu = bnb_u() or 0
    if not bu: return out
    try:
        res = batch([('eth_call', [{'to': pairs[t], 'data': '0x0902f1ac'}, 'latest']) for t in ks] + [('eth_call', [{'to': pairs[t], 'data': '0x0dfe1682'}, 'latest']) for t in ks]
                    + [('eth_call', [{'to': pairs[t], 'data': '0xd21220a7'}, 'latest']) for t in ks] + [('eth_call', [{'to': t, 'data': '0x313ce567'}, 'latest']) for t in ks], tolerant=True)
    except Exception: return out
    n = len(ks)
    for i, t in enumerate(ks):
        try:
            rv, dc = res[i], int(res[3 * n + i] or '0x12', 16)
            t0 = (res[n + i] or '')[-40:].lower(); t1 = (res[2 * n + i] or '')[-40:].lower()
            # 2026-11-04 20:5x 修：原来 token0/token1 读失败时「猜」另一边是 WBNB，BNBbaby 的 USDT 池被当成 WBNB 池，价格虚高约 BNB 价倍（x=81 实际约 0.1），误触发 10/20 倍卖出。现在两边都读到才算，且支持 USDT 池
            if not t0 or not t1: continue
            US = '55d398326f99059ff775485246999027b3197955'
            if t[2:] not in (t0, t1): continue
            qt = t1 if t0 == t[2:] else t0
            if qt == W: qm = bu
            elif qt == US: qm = 1.0
            else: continue
            r0, r1 = int(rv[2:66], 16), int(rv[66:130], 16)
            rt, rw = (r0, r1) if t0 == t[2:] else (r1, r0)
            liq = 2 * rw / 1e18 * qm
            if rt and liq >= 50: out[t] = {'px': rw / 1e18 / (rt / 10 ** dc) * qm, 'liq': liq, 'url': f'https://dexscreener.com/bsc/{pairs[t]}', 'created': 0, 'pair': pairs[t], 'src': 'chain'}
        except Exception: pass
    return out


_DS = [0]
UNPRICED = [False]
NOLIQ = set()


def prices(tokens, pairs=None):
    NOLIQ.clear()
    out = chain_prices(pairs or {})
    tokens_all = tokens; tokens = [t for t in tokens if t not in out]
    if tokens and time.time() - _DS[0] < 15: return out  # 外部接口限流保护：链上读不到的（未开池/非 WBNB 池）最多 15 秒查一次 DexScreener
    if tokens: _DS[0] = time.time()
    for i in range(0, len(tokens), 30):
        try: ps = get('https://api.dexscreener.com/latest/dex/tokens/' + ','.join(tokens[i:i + 30])).get('pairs') or []
        except Exception: continue
        _seen = {str((p.get('baseToken') or {}).get('address', '')).lower() for p in ps}
        for _t in tokens[i:i + 30]:  # 2026-11-10：接口正常返回但没有 ≥50U 的池 = 池子没流动性了，不是读取失败
            if _t not in _seen or all(((p.get('liquidity') or {}).get('usd') or 0) < 50 for p in ps if str((p.get('baseToken') or {}).get('address', '')).lower() == _t): NOLIQ.add(_t)
        for p in ps:
            t = str((p.get('baseToken') or {}).get('address', '')).lower(); liq = (p.get('liquidity') or {}).get('usd', 0) or 0
            if p.get('chainId') != 'bsc' or t not in tokens or liq < 50: continue
            if t not in out or liq > out[t]['liq']:
                out[t] = {'px': float(p.get('priceUsd') or 0), 'liq': liq, 'url': p.get('url'), 'created': (p.get('pairCreatedAt') or 0) / 1000, 'pair': str(p.get('pairAddress') or '').lower()}
    miss = [t for t in tokens if t not in out]  # DexScreener 限流/没收录 → GeckoTerminal 补（流动性取 total_reserve_in_usd）
    for i in range(0, len(miss), 30):
        try: ds = get('https://api.geckoterminal.com/api/v2/networks/bsc/tokens/multi/' + ','.join(miss[i:i + 30])).get('data') or []
        except Exception: continue
        for d in ds:
            a = d.get('attributes') or {}; t = str(a.get('address', '')).lower(); liq = float(a.get('total_reserve_in_usd') or 0)
            if t in miss and a.get('price_usd') and liq >= 50:
                out[t] = {'px': float(a['price_usd']), 'liq': liq, 'url': f'https://www.geckoterminal.com/bsc/tokens/{t}', 'created': 0}
    miss = [t for t in tokens if t not in out]  # 两个接口都限流时：链上 PancakeSwap V2（代币/WBNB 池）兜底
    if miss:
        W = 'bb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c'; bu = bnb_u() or 0
        prs = batch([('eth_call', [{'to': '0xca143ce32fe78f1f7019d7d551a6402fc5350c73', 'data': '0xe6a43905' + t[2:].rjust(64, '0') + W.rjust(64, '0')}, 'latest']) for t in miss], tolerant=True)
        pa = {t: '0x' + r[-40:] for t, r in zip(miss, prs) if isinstance(r, str) and len(r) >= 66 and int(r, 16)}
        ks = list(pa)
        res = batch([('eth_call', [{'to': pa[t], 'data': '0x0902f1ac'}, 'latest']) for t in ks] + [('eth_call', [{'to': pa[t], 'data': '0x0dfe1682'}, 'latest']) for t in ks] + [('eth_call', [{'to': t, 'data': '0x313ce567'}, 'latest']) for t in ks], tolerant=True) if ks else []
        n = len(ks)
        for i, t in enumerate(ks):
            try:
                rv, t0, dc = res[i], res[n + i], int(res[2 * n + i], 16)
                r0, r1 = int(rv[2:66], 16), int(rv[66:130], 16)
                rt, rw = (r0, r1) if t0[-40:].lower() == t[2:] else (r1, r0)
                liq = 2 * rw / 1e18 * bu
                if rt and liq >= 50: out[t] = {'px': rw / 1e18 / (rt / 10 ** dc) * bu, 'liq': liq, 'url': f'https://dexscreener.com/bsc/{pa[t]}', 'created': 0, 'pair': pa[t]}
            except Exception: pass
    return out


_BU = [0, 0]
def bnb_u():
    if time.time() - _BU[1] < 30 and _BU[0]: return _BU[0]
    v = _bnb_u_raw()
    if v: _BU[0], _BU[1] = v, time.time()
    return v


def _bnb_u_raw():  # PancakeSwap 路由报价 1 BNB → USDT（Binance API 在此地区 451）
    try:
        r = batch([('eth_call', [{'to': '0x10ed43c718714eb63d5aa57b78b54704e256024e', 'data': '0xd06ca61f' + hex(10**18)[2:].rjust(64, '0') + '40'.rjust(64, '0') + '2'.rjust(64, '0') + 'bb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c'.rjust(64, '0') + '55d398326f99059ff775485246999027b3197955'.rjust(64, '0')}, 'latest'])])[0]
        return int(r[-64:], 16) / 1e18
    except Exception: return None


def positions():
    pos = {}
    for p in json.load(open(f'{D}/auto_buy.json')).get('positions', []):
        t = str(p.get('token') or '').lower()
        if not t.startswith('0x'): continue
        e = pos.setdefault(t, {'name': p.get('project'), 'paid': True, 'manual': bool(p.get('manual')), 'acc': {}})
        for a in p.get('accounts', []):
            e['acc'][a] = {'qty0': float(p.get('qty_each') or 0), 'cost_bnb': float(p.get('cost_bnb_each') or 0), 'cost_u': p.get('cost_u_each')}
    try: seen = json.load(open(f'{D}/seen.json'))
    except Exception: seen = []
    for x in seen:
        t = str(x.get('token') or '').lower(); ty = str(x.get('type') or '')
        if not t.startswith('0x') or t in pos or x.get('cost_usd') not in (0, None, 0.0): continue
        if not ('free' in ty or '免费' in ty): continue
        pos.setdefault(t, {'name': x.get('name'), 'paid': False, 'acc': {a: {} for a in LAB}})
    return pos


def sell(t, pct, labels, slip='8', px=None):
    if DRY: return {l: {'dry': pct} for l in labels}
    r = subprocess.run(['python3', f'{D}/fast_sell.py', t, str(round(pct, 2)), '--slip', slip] + (['--px', repr(px)] if px else []) + labels, capture_output=True, text=True, timeout=240)
    try: return json.loads(r.stdout.splitlines()[0])['orders']
    except Exception: return {l: {'error': (r.stdout + r.stderr)[-200:]} for l in labels}


def step(st, out):
    pos = positions(); toks = list(pos)
    if not toks: return
    _pairs = {t: (st.get(t) or {}).get('pair') or (str((st.get(t) or {}).get('url') or '').split('/bsc/')[-1] if '/bsc/0x' in str((st.get(t) or {}).get('url') or '') else None) for t in toks}
    pr = prices(toks, _pairs); bu = bnb_u()
    UNPRICED[0] = any(t not in pr and not (st.get(t) or {}).get('last') for t in toks)  # 有仓位还没开池 → 2 秒一轮查开池
    if bu is None:  # 2026-11-02：BNB 价读失败 → 付费仓位倍数算不出，重试 2 次，仍失败记 unknown（不当没触发也不乱卖）
        for _k in range(2):
            time.sleep(1 + _k); bu = bnb_u()
            if bu: break
        if not bu: out.setdefault('unknown', []).append('BNB/USDT 报价读取失败，本轮付费仓位倍数未判断')
    _miss = [t for t in toks if t not in pr and st.get(t, {}).get('last')]  # 之前读到过价格（已开池）、这轮读不到 = 价格源失败
    _dead = [t for t in _miss if t in NOLIQ]; _miss = [t for t in _miss if t not in NOLIQ]
    if _miss: out.setdefault('unknown', []).append('价格读取失败：' + '、'.join(pos[t]['name'] or t[:8] for t in _miss[:10]))
    if _dead: out['no_liquidity'] = [pos[t]['name'] or t[:8] for t in _dead]  # 池子已无流动性（<50U），卖不出，不算读取失败
    dec = dict(zip(toks, batch([('eth_call', [{'to': t, 'data': '0x313ce567'}, 'latest']) for t in toks], tolerant=True)))
    calls = [(t, l) for t in toks if t in pr for l in pos[t]['acc'] if l in LAB]
    bals = batch([('eth_call', [{'to': t, 'data': '0x70a08231' + LAB[l][2:].rjust(64, '0')}, 'latest']) for t, l in calls], tolerant=True) if calls else []
    bal = {}
    for (t, l), b in zip(calls, bals):
        try: bal[(t, l)] = int(b, 16) / 10 ** int(dec[t], 16)
        except Exception: pass
    for t in toks:
        if t not in pr: continue
        P = pos[t]; q = pr[t]; px = q['px']; s = st.setdefault(t, {'name': P['name'], 'acc': {}})
        s.setdefault('first', time.time()); s['peak'] = max(s.get('peak', 0), px); s['last'] = px; s['url'] = q['url']
        if q.get('pair'): s['pair'] = q['pair']
        opened_at = q['created'] or s['first']
        # 学  貔貅/转账锁检查：开池后先用持币号模拟「把 1% 转进池子」（等于卖出路径），revert 就提醒且不自动卖
        if 'hp' not in s and q.get('pair', '').startswith('0x'):
            holder = next(((l, bal[(t, l)]) for l in P['acc'] if bal.get((t, l))), None)
            if holder:
                amt = int(holder[1] * 10 ** int(dec[t], 16) / 100) or 1
                # 2026-11-02：原来 batch 读失败返回 None 也记成 fail（永久不自动卖）。现在用 call_strict：明确 revert 才 fail，读失败 = unknown，下一轮重查
                from rpcpool import call_strict
                stt, r = 'unknown', None
                for _k in range(3):
                    stt, r = call_strict(t, '0xa9059cbb' + q['pair'][2:].rjust(64, '0') + hex(amt)[2:].rjust(64, '0'), LAB[holder[0]])
                    if stt != 'unknown': break
                    time.sleep(1 + _k)
                if stt == 'unknown':
                    out.setdefault('unknown', []).append(f"{P['name']}｜貔貅模拟读取失败（已重试 3 次），下一轮重查"); continue
                s['hp'] = 'ok' if stt == 'ok' and isinstance(r, str) and (r in ('0x',) or (len(r) >= 66 and int(r[:66], 16) == 1)) else 'fail'
                if s['hp'] == 'fail': out['alerts'].append(f"{P['name']}｜模拟转入池子失败（疑似貔貅/转账锁/白名单），不自动卖，请人工确认｜{q['url']}")
        if s.get('hp') == 'fail': continue
        acts = {}  # (kind, pct) -> [labels]
        for l, a in P['acc'].items():
            b = bal.get((t, l))
            if not b: continue
            v = b * px; sa = s['acc'].setdefault(l, {'stage': 0})
            if sa.get('done') or v < R['minU']: continue
            if sa.get('next', 0) > time.time(): continue
            act = None
            if P['paid']:
                if not a.get('qty0') or not (a.get('cost_u') or (a.get('cost_bnb') and bu)): continue
                entry_u = a['cost_u'] / a['qty0'] if a.get('cost_u') else a['cost_bnb'] * bu / a['qty0']
                x = px / entry_u; sa['x'] = round(x, 2)
                for n in R['notifyX'] + R['moonNotify']:
                    if x >= n and n not in sa.setdefault('nx', []): sa['nx'].append(n); out['notify'].append(f"{P['name']}｜{l}｜到 {n} 倍（现价 {px:.3g}U）｜{q['url']}")
                dd = 1 - px / s['peak'] if s['peak'] else 0
                sa['xmax'] = max(sa.get('xmax', 0), round(x, 3))
                if sa['stage'] == 0:
                    if x >= R['backX']: act = ('回本卖一半', 50)
                    elif sa['xmax'] >= R['earlyX'] and x <= 1.0 and time.time() - opened_at <= R['earlyWin']: act = ('回本卖一半（开池初期冲高 1.5 倍后跌回成本）', 50)
                    elif dd >= R['crash']: act = ('回本前从高点回落 35% 全卖', 100)
                else:
                    if x >= 20 and not sa.get('s20') and sa.get('s10'): act = ('20 倍卖剩余 50%', 50)
                    elif x >= 10 and not sa.get('s10'): act = ('10 倍卖剩余 30%', 30)
                    elif dd >= R['crashAfterBack'] and not sa.get('moon'):
                        keep = a['qty0'] * R['moonPct'] / 100
                        if b > keep * 1.02: act = ('回本后从高点回落 50%，卖到只留 20% 长抱', round(100 * (1 - keep / b), 2))
            else:
                # 2026-11-04 03:22 用户原话「要」：免费领到的币一能卖（已开池、有报价）就立刻全卖，替代开池 3 分钟后回落 40% 规则
                act = ('免费币一能卖立刻全卖', 100)
            if not act: sa.pop('hit', None); continue
            sa['hit'] = sa.get('hit', 0) + 1
            if sa['hit'] < 2 and P['paid']: continue  # 连续两次才算，防价格源跳动（免费币立刻卖，不等）
            if False:  # 2026-11-01 19:46 用户原话「改」：手动确认买入的仓位也全天自动卖（原 A 规则手动仓白天只提醒）
                key = act[0]
                if sa.get('alerted') != key: sa['alerted'] = key; out['alerts'].append(f"{P['name']}｜{l}｜触发「{key}」（价值约 {v:.2f}U）｜手动仓位白天不自动卖，00:00 后仍满足会自动执行｜{q['url']}")
                continue
            # 2026-11-01 19:56：8% 滑点在开池急跌时卖单回滚（BNBHolder），改首笔 12%、失败一次就 15%（上限 15%）
            acts.setdefault((act[0], act[1], '15' if sa.get('fail', 0) >= 1 else '12'), []).append((l, v))
        s['liq'] = q.get('liq') or 0  # 失败 2 次后滑点 8%→15%
        for (kind, pct, slip), ls in acts.items():
            sell_u = sum(v for _, v in ls) * pct / 100; side = (q.get('liq') or 0) / 2
            if side and sell_u > side * R['impact']:  # D：一次卖冲击太大 → 分两笔
                p1 = pct / 2; res = sell(t, p1, [l for l, _ in ls], slip, px); time.sleep(15)
                ok = [l for l, _ in ls if isinstance(res.get(l), dict) and 'orderId' in res[l]]
                if ok:
                    r2 = sell(t, round((pct - p1) / (100 - p1) * 100, 2) if p1 < 100 else 100, ok, slip, px)
                    for l in ok:
                        if not (isinstance(r2.get(l), dict) and 'orderId' in r2[l]): out['errors'].append(f"{P['name']}｜{l}｜{kind} 第二笔失败（第一笔已成交 {res[l].get('orderId')}），不重发，下轮按价格重判：{str(r2.get(l))[:100]}")
            else:
                res = sell(t, pct, [l for l, _ in ls], slip, px)
            for l, v in ls:
                r = res.get(l) or {}; sa = s['acc'][l]
                if isinstance(r, dict) and ('orderId' in r or 'dry' in r) or r == 'no_balance':
                    sa['fail'] = 0; sa.pop('hit', None)
                    if kind.startswith('回本卖'): sa['stage'] = 1  # 含 C 的早期保本
                    elif kind.startswith('10 倍'): sa['s10'] = True
                    elif kind.startswith('20 倍'): sa['s20'] = True
                    elif '长抱' in kind: sa['moon'] = True
                    else: sa['done'] = True
                    out['sold'].append({'name': P['name'], 'token': t, 'acc': l, 'why': kind, 'pct': pct, 'value_u': round(v * pct / 100, 2), 'order': r.get('orderId') if isinstance(r, dict) else r, 'url': q['url']})
                elif isinstance(r, dict) and 'quote_mismatch' in str(r.get('error', '')):
                    # 2026-11-04：实际报价远低于估算价 = 价格源错或池子太浅，不卖、不推进阶段、10 分钟后重判，并提醒一次
                    sa['next'] = time.time() + 600; sa['last_err'] = str(r)[:200]; sa.pop('hit', None)
                    if sa.get('mm_alerted') != kind: sa['mm_alerted'] = kind; out['alerts'].append(f"{P['name']}｜{l}｜{kind}：实际报价远低于估算（{str(r.get('error'))[:80]}），未卖，10 分钟后重判｜{q['url']}")
                elif isinstance(r, dict) and 'quote_unknown' in str(r.get('error', '')):
                    # 2026-11-02：报价读失败（网络/限流）≠ 卖不掉：不计失败次数、不放弃，60 秒后重试
                    sa['next'] = time.time() + 60; sa['last_err'] = str(r)[:200]; out.setdefault('unknown', []).append(f"{P['name']}｜{l}｜{kind}：报价读取失败，60 秒后重试")
                else:
                    sa['fail'] = sa.get('fail', 0) + 1; sa['next'] = time.time() + 60 * 2 ** min(sa['fail'] - 1, 5)
                    sa['last_err'] = str(r)[:200]  # 2026-11-02：<3U 的小仓失败原来不记原因（币安安放弃后查不到为什么），现在每次都记最后一次错误
                    if v < R['smallU'] and sa['fail'] >= R['smallMaxFail']: sa['done'] = True; sa['gaveup'] = now()
                    if sa['fail'] == 1 and v >= R['smallU']: out['errors'].append(f"{P['name']}｜{l}｜{kind} 卖出失败，自动退避重试：{str(r)[:120]}")


def main():
    import fcntl
    lk = open(ST + '.lock', 'w')
    try: fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError: print(json.dumps({'time_cst': now(), 'skipped': '循环版正在运行', 'sold': [], 'alerts': [], 'notify': [], 'errors': []}, ensure_ascii=False)); return
    st = json.load(open(ST)) if os.path.exists(ST) else {}
    out = {'time_cst': now(), 'night': night(), 'sold': [], 'alerts': [], 'notify': [], 'errors': []}
    end = time.time() + LOOP
    while True:
        out['unknown'] = []  # 2026-11-02：读失败项只保留最近一轮（常驻循环不累积刷屏）
        try: step(st, out)
        except Exception as e: out['errors'].append(f'step: {str(e)[:160]}')
        if not DRY: json.dump(st, open(ST + '.tmp', 'w'), ensure_ascii=False); os.replace(ST + '.tmp', ST)
        gap = EVERY
        if '--daemon' in args:
            json.dump({'heartbeat': int(time.time()), 'time_cst': now(), 'pid': os.getpid()}, open(f'{D}/discover_out/possell_live.json', 'w'))
            fresh = any(v.get('first') and time.time() - v['first'] <= R['fastWin'] for v in st.values() if isinstance(v, dict))
            gap = 1 if fresh else (2 if UNPRICED[0] else EVERY)  # 2026-11-03 用户「太慢了」：开池 30 分钟内 1 秒一轮（价格链上直读）
            json.dump(out, open(f'{D}/discover_out/possell_daemon_out.json', 'w'), ensure_ascii=False, indent=1)
        if not LOOP or time.time() + gap > end: break
        time.sleep(gap)
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == '__main__': main()
