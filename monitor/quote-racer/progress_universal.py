#!/usr/bin/env python3
"""通用付费 mint 进度监控（2026-11-09 00:40，用户「彻底解决这个问题」，起因 DOG 0xe996 小写 openAt 漏报）。

不再依赖固定函数名列表：
1. 入表：所有扫描器见过的 mint 合约（chainscan targets/candidates、fairmint found、newtpl、refundmine、quote_watch pw）。
2. 识别：读代码（追 EIP-1167 / EIP-1967 / Beacon），取全部 PUSH4 选择器 → openchain 反查函数名 →
   名字去下划线转小写后按正则分角色：已售 / 开池门槛（强上限）/ 总量（弱上限）/ 单价（零参或 uint256=1）/ 已开池标志。
3. 每轮批量读，进度 = 已售 ÷ 开池门槛（没有就用总量，且总量 ≥ 已售×1.0）；已开池/已 launched/满 100% 标 done。
4. 提醒（每盘一次）：进度首次 ≥60%；付费热盘（chainscan 30 分钟 ≥5 钱包）读不出进度也报一次「进度读不出」，不再静默。
5. 与 quote_watch pw 表、fairmint 共用去重：pw[a].alert60 已报过的不重复报。
只读，不签名、不付款。由 quote_watch.py 末尾调用，alerts 并入巡逻单发。
"""
import sys, os, re, json, time
sys.path.insert(0, '$AIDBOT_HOME/bsc-mint-skill'); sys.path.insert(0, '$AIDBOT_HOME/bsc-mint-skill/vendor')
import chain

LM = '$AIDBOT_DATA'
ST = '$AIDBOT_HOME/quote-racer/progress_universal_state.json'
QW = '$AIDBOT_HOME/quote-racer/quote_watch_state.json'
ARG1 = '%064x' % 1
N = lambda s: re.sub(r'[^a-z0-9]', '', s.split('(')[0].lower())

R_SOLD = re.compile(r'^(totalminted|minted|mintedcount|mintcount|totalmints|totalmintcount|totalsold|sold|soldcount|sharesminted|sharessold|'
                    r'mintedshares|soldshares|totalsharesminted|totalsharessold|totalpaidshares|paidshares|unitssold|totalunitssold|'
                    r'totalpurchased|purchased|totalbought|currentsupply|mintedsupply|totalissued|issued|nexttokenid|currentindex|totalclaimed)$')
R_SOLD_WEAK = re.compile(r'^totalsupply$')
R_CAP = re.compile(r'^(openat|openunits|openshares|opencount|openthreshold|launchat|launchthreshold|launchshares|graduationshares|'
                   r'graduationthreshold|gradthreshold|maxshares|maxmints|maxunits|liquiditytargetshares|targetshares|targetunits|'
                   r'salecap|hardcapshares|hardcap|mintcap|maxmintsupply|maxsale|salesupply|presalesupply|presaleshares|fairmintsupply|'
                   r'publicsupply|maxpublic|totalforsale|sharesforsale|unitsforsale|poolthreshold|opensupply)$')
R_CAP_WEAK = re.compile(r'^(maxsupply|cap|maxmint|totalshares|totalsupplycap|maxtokens|maxtotalsupply|collectionsize|supplycap|maxcount|maxtokenid)$')
R_CAP_GEN = re.compile(r'^(?!.*(per|wallet|tx|address|agent|id$|duration|delay|window|blocks|fee|bps|price|reward))(.*(max|cap|target|limit|threshold).*(share|mint|unit|sale|supply|slot|seat|egg|ticket|spot).*|.*(share|mint|unit|sale|slot|seat|egg|ticket|spot).*(max|cap|target|limit|threshold|total).*)$')
R_PRICE0 = re.compile(r'^(price|baseprice|mintprice|cost|mintcost|pricepershare|shareprice|unitprice|pricepermint|publicprice|saleprice|'
                      r'priceperunit|currentprice|getprice|mintfee|fee|pricewei|sharepricewei)$')
R_PRICE1 = re.compile(r'^(costof|priceof|bnbquote|quote|getcost|getmintprice|pricefor|costfor|quotemint|mintcost|getprice|totalcost)$')
R_OPEN = re.compile(r'^(opened|launched|poolopen|ispoolopen|poolopened|finalized|liquidityadded|liquiditytargetreached|graduated|'
                    r'tradingenabled|tradingopen|saleended|ended|soldout|isended|issoldout|migrated)$')


def load(p, d):
    try: return json.load(open(p))
    except Exception: return d


def sources():
    out = {}
    def add(a, src, heat=0):
        if isinstance(a, str) and re.fullmatch(r'0x[0-9a-fA-F]{40}', a):
            a = a.lower(); o = out.setdefault(a, {'src': set(), 'heat': 0}); o['src'].add(src); o['heat'] = max(o['heat'], heat)
    for x in load(f'{LM}/chainscan/candidates.json', []):
        if not x.get('free_like'): add(x.get('mint_target'), 'chainscan', x.get('wallets_30m') or 0)
    cs = load(f'{LM}/chainscan/state.json', {})
    for t, m in (cs.get('targets') or {}).items(): add(m if isinstance(m, str) else t, 'chainscan_t')
    for a in (load(f'{LM}/fairmint/found.json', {}) or {}): add(a, 'fairmint')
    for a in load(f'{LM}/newtpl_state.json', {}): add(a, 'newtpl')
    for a in load(f'{LM}/refundmine_state.json', {}): add(a, 'refundmine')
    for a in (load(QW, {}).get('pw') or {}): add(a, 'pw')
    return out


def eff_code(a):
    info = chain.resolve(a)
    return info.get('effective_code') or '0x', info.get('proxy'), info.get('impl')


def plan_for(a):
    code, proxy, impl = eff_code(a)
    if len(code) < 200: return {'none': True}
    sels = chain.push4(code)
    names = chain.lookup_sigs(sels)
    p = {'sold': [], 'sold_w': [], 'cap': [], 'cap_w': [], 'cap_g': [], 'price0': [], 'price1': [], 'open': [], 'proxy': proxy, 'impl': impl}
    for s, nm in names.items():
        if not nm: continue
        base = N(nm); args = nm[nm.find('(') + 1:-1] if '(' in nm else ''
        if args == '':
            if nm[:1].isupper() and not re.match(r'^[A-Z0-9_]+\(', nm): continue  # 自定义错误/事件名（SoldOut()）不是读函数
            hit = False
            for k, rx in (('sold', R_SOLD), ('sold_w', R_SOLD_WEAK), ('cap', R_CAP), ('cap_w', R_CAP_WEAK), ('price0', R_PRICE0), ('open', R_OPEN)):
                if rx.match(base): p[k].append([s, nm]); hit = True
            if not hit and R_CAP_GEN.match(base): p['cap_g'].append([s, nm])
        elif args == 'uint256' and R_PRICE1.match(base): p['price1'].append([s, nm])
    p['t'] = int(time.time())
    return p


def plan_many(addrs, plans, t0, limit):
    from concurrent.futures import ThreadPoolExecutor
    def one(a):
        if time.time() - t0 > limit: return a, None
        try: return a, plan_for(a)
        except Exception as e: return a, {'err': str(e)[:120], 't': int(time.time())}
    # 2026-11-10：原来 with 会等所有已开始的识别做完（openchain/节点慢时一个要几分钟），整轮超 115 秒被杀、没有任何输出。
    # 改为到时限就不等，没做完的下一轮再识别
    from concurrent.futures import wait
    ex = ThreadPoolExecutor(8); fs = [ex.submit(one, a) for a in addrs]
    done, _ = wait(fs, timeout=max(1, limit - (time.time() - t0)))
    for f in done:
        try:
            a, p = f.result()
            if p is not None: plans[a] = p
        except Exception: pass
    ex.shutdown(wait=False, cancel_futures=True)


def backfill(limit=1100):
    t0 = time.time(); st = load(ST, {}); plans = st.setdefault('plans', {}); src = sources()
    todo = [a for a in src if a not in plans]
    plan_many(todo, plans, t0, limit)
    cur = load(ST, {}); cur.setdefault('plans', {}).update(plans); json.dump(cur, open(ST, 'w')); return {'todo': len(todo), 'planned': sum(1 for a in todo if a in plans), 'secs': round(time.time() - t0)}


MAX_CALLS = int(os.environ.get('PU_MAX_CALLS', '2400'))


def pbatch(calls, size=60):
    from concurrent.futures import ThreadPoolExecutor
    parts = [calls[i:i + size] for i in range(0, len(calls), size)]
    def one(part):
        try: return chain.batch(part)
        except Exception: return [None] * len(part)
    with ThreadPoolExecutor(8) as ex: out = list(ex.map(one, parts))
    return [x for part in out for x in part]


def calls_for(a, p):
    c = []
    for k in ('sold', 'sold_w', 'cap', 'cap_w', 'cap_g', 'price0', 'open'):
        for s, nm in p.get(k, []): c.append((k, nm, {'to': a, 'data': s}))
    for s, nm in p.get('price1', []): c.append(('price1', nm, {'to': a, 'data': s + ARG1}))
    return c


def u(v):
    return int(v[:66], 16) if isinstance(v, str) and v.startswith('0x') and len(v) >= 66 else None


def evaluate(vals):
    g = lambda k: [(nm, x) for nm, x in vals.get(k, []) if x is not None]
    opened = any(x == 1 for nm, x in g('open'))
    sold = next((x for nm, x in g('sold') if x), None); sold_src = next((nm for nm, x in g('sold') if x), None)
    if sold is None: sold = next((x for nm, x in g('sold_w') if x), None); sold_src = 'totalSupply()' if sold else None
    price = next((x for nm, x in g('price1') if x and x < 10 ** 21), None) or next((x for nm, x in g('price0') if x and x < 10 ** 21), None)
    cap = cap_src = None
    if sold:
        sane = lambda x: x and not (x > 10 ** 15 and x > sold * 10 ** 6)  # 18 位精度的代币总量不当份数上限
        cap, cap_src = next(((x, nm) for nm, x in g('cap') if sane(x)), (None, None))  # 开池门槛：已售超过也用（=已满）
        for k in ('cap_w', 'cap_g'):
            if cap: break
            for nm, x in g(k):
                if sane(x) and x >= sold * 0.95: cap, cap_src = x, nm; break
    pct = round(100 * sold / cap, 1) if (sold and cap) else None
    return {'sold': sold, 'sold_src': sold_src, 'cap': cap, 'cap_src': cap_src, 'price_wei': price, 'opened': opened, 'pct': pct}


def run(max_new=120, budget=80):
    t0 = time.time(); now = int(t0)
    st = load(ST, {}); plans = st.setdefault('plans', {}); mon = st.setdefault('mon', {})
    src = sources()
    order = sorted((a for a in src if a not in plans), key=lambda a: (-src[a]['heat'], 'chainscan' not in src[a]['src'], 'fairmint' not in src[a]['src']))
    new = order[:max_new]
    plan_many(new, plans, t0, budget * 0.5)
    live = [a for a in src if a in plans and not plans[a].get('none') and not plans[a].get('err') and not mon.get(a, {}).get('done')]
    qw = load(QW, {}); pw = qw.get('pw') or {}
    # 每轮最多读 MAX_CALLS 个 eth_call：热盘优先，其余按上次读取时间轮转（全表约 3–4 轮扫一遍）
    live.sort(key=lambda a: (-src[a]['heat'], mon.get(a, {}).get('t', 0)))
    batch, idx, pick = [], [], []
    for a in live:
        cs = calls_for(a, plans[a])
        if len(batch) + len(cs) > MAX_CALLS: break
        pick.append(a)
        for k, nm, call in cs: batch.append(('eth_call', [call, 'latest'])); idx.append((a, k, nm))
    live = pick
    res = pbatch(batch)
    vals = {}
    for (a, k, nm), r in zip(idx, res): vals.setdefault(a, {}).setdefault(k, []).append((nm, u(r)))
    alerts = []
    for a in live:
        m = mon.setdefault(a, {'first': now}); e = evaluate(vals.get(a, {}))
        prev = (m.get('last') or {}).get('sold')
        if prev is not None and e['sold'] is not None and e['sold'] > prev: m['moved'] = now  # 已售在涨＝盘还活着
        m['last'] = e; m['t'] = now
        heat = src[a]['heat']
        if e['opened'] or (e['pct'] is not None and e['pct'] >= 100): m['done'] = True; continue
        paid = bool(e['price_wei']) or heat > 0
        alive = now - m.get('moved', 0) <= 3 * 3600  # 只报 3 小时内已售还在涨的盘，避开早已停滞的旧盘（首扫 40+ 个死盘教训）
        if e['pct'] is not None and e['pct'] >= 60 and paid and alive and not m.get('alert60') and not pw.get(a, {}).get('alert60'):
            m['alert60'] = now; pw.setdefault(a, {'first': now})['alert60'] = now
            try: nm = chain.dec_str(chain.call(a, chain.sel('name()'))[1]) or '未命名'
            except Exception: nm = '未命名'
            pr = f"{e['price_wei'] / 1e18:g} BNB" if e['price_wei'] else '读不到'
            alerts.append(f"⚠️ 需处理｜付费 mint 进度已到 {e['pct']}%（{e['sold']}/{e['cap']}，{e['sold_src']} ÷ {e['cap_src']}）｜{nm}｜{a[:6]}…{a[-4:]}｜单价读数 {pr}（以简报核实为准）｜"
                          f"请跑 mintcheck 出完整简报，需要买回复「确认买入 XX 金额 + 账号」｜https://bscscan.com/address/{a}")
        elif e['pct'] is None and heat >= 5 and e['sold'] and not m.get('alert_unread'):
            m['alert_unread'] = now
            alerts.append(f"⚠️ 需处理｜付费热盘进度读不出（30 分钟 {heat} 个钱包在铸）｜{a[:6]}…{a[-4:]}｜已售读数 {e['sold']}（{e['sold_src']}）上限读数 {e['cap']}｜"
                          f"请跑 mintcheck 人工判断进度｜https://bscscan.com/address/{a}")
    if True:
        try:
            qw2 = load(QW, {}); qw2.setdefault('pw', {}).update({k: v for k, v in pw.items() if v.get('alert60')}); json.dump(qw2, open(QW, 'w'))
        except Exception: pass
    if len(plans) > 8000: st['plans'] = dict(list(plans.items())[-8000:])
    json.dump(st, open(ST, 'w'))
    return {'sources': len(src), 'new_planned': len(new), 'live': len(live),
            'readable': sum(1 for a in live if mon.get(a, {}).get('last', {}).get('pct') is not None), 'alerts': alerts, 'secs': round(time.time() - t0, 1)}


def probe(a):
    a = a.lower(); p = plan_for(a); vals = {}
    cs = calls_for(a, p); res = chain.batch([('eth_call', [c, 'latest']) for _, _, c in cs]) if cs else []
    for (k, nm, _), r in zip(cs, res): vals.setdefault(k, []).append((nm, u(r)))
    return {'plan': {k: [x[1] for x in v] for k, v in p.items() if isinstance(v, list)}, 'eval': evaluate(vals)}


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'backfill': print(json.dumps(backfill()))
    elif len(sys.argv) > 1 and sys.argv[1] == 'probe':
        for a in sys.argv[2:]: print(a, json.dumps(probe(a), ensure_ascii=False))
    else:
        print(json.dumps(run(), ensure_ascii=False))
