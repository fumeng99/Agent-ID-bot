#!/usr/bin/env python3
"""历史经验库：保存每个项目的合约特征（字节码指纹、选择器集合、代理实现）、Mint 方法、交易参数、结果和失败原因。
新项目先按「指纹完全一致 → 同模板」「选择器集合 Jaccard ≥ 0.8 → 相似模板」匹配，复用识别方法（函数、价格/进度读法），
但地址、价格、参数每次都重新上链核对，库里的数字只作参考。

用法：
  python3 mintlib.py stats                         # 库里有多少项目/模板/成功失败
  python3 mintlib.py show <CA>                     # 某个合约的全部历史记录
  python3 mintlib.py match <CA>                    # 只做匹配（读代码 → 指纹/相似度）
  python3 mintlib.py record <CA> --result success|fail|skip [--reason 文本] [--tx 0x..] [--method 'mint(uint256)'] [--value-wei N] [--note 文本]
  python3 mintlib.py template <CA> --name 名字 [--note 文本]   # 把某合约的识别方法登记成命名模板
  python3 mintlib.py timing [--last 50]            # 最近报告的各环节平均耗时
  python3 mintlib.py seed                          # 从 launch-monitor 历史（soldout 等）和已知项目导入
"""
import json, os, sys, time
D = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, D)
import chain as C

PROJ = os.path.join(C.LIB, 'projects.jsonl'); TPL = os.path.join(C.LIB, 'templates.json')


def now(): return time.strftime('%Y-%m-%d %H:%M', time.gmtime(time.time() + 8 * 3600))


def records():
    out = []
    try:
        for ln in open(PROJ):
            ln = ln.strip()
            if ln:
                try: out.append(json.loads(ln))
                except Exception: pass
    except FileNotFoundError: pass
    return out


def add(rec):
    rec.setdefault('at_cst', now())
    with open(PROJ, 'a') as f: f.write(json.dumps(rec, ensure_ascii=False) + '\n')


def templates(): return C._load(TPL, {})


def save_templates(t): C._save(TPL, t)


def jaccard(a, b):
    a, b = set(a or []), set(b or [])
    return len(a & b) / len(a | b) if a and b else 0.0


def match(info, exclude=None):
    """info = chain.resolve() 结果。返回 {'exact': [...], 'similar': [...], 'template': {...}|None}。"""
    fp = info.get('fingerprint'); sels = info.get('selectors') or []
    recs = [r for r in records() if r.get('address') != (exclude or info.get('address'))]
    exact, sim, seen = [], [], set()
    for r in reversed(recs):
        a = r.get('address')
        if a in seen: continue
        if fp and r.get('fingerprint') == fp: exact.append(r); seen.add(a); continue
        j = jaccard(sels, r.get('selectors'))
        if j >= 0.8: sim.append(dict(r, _similarity=round(j, 3))); seen.add(a)
    sim.sort(key=lambda r: -r['_similarity'])
    tpl = templates().get(fp) if fp else None
    if not tpl and sim:
        tpl = templates().get(sim[0].get('fingerprint'))
        if tpl: tpl = dict(tpl, _via='similar')
    return {'exact': exact[:5], 'similar': sim[:5], 'template': tpl}


def summarize(addr):
    rs = [r for r in records() if r.get('address') == addr.lower()]
    return rs


def stats():
    rs = records(); by = {}
    for r in rs: by[r.get('result', '?')] = by.get(r.get('result', '?'), 0) + 1
    fps = {r.get('fingerprint') for r in rs if r.get('fingerprint')}
    return {'records': len(rs), 'contracts': len({r.get('address') for r in rs}), 'fingerprints': len(fps),
            'templates': len(templates()), 'by_result': by}


KNOWN = [  # 已知项目（本机历史），只存特征与结论，不存余额等时点数据
    ('0x6288dfe86f22b53eeb6886ae2d78941de7483743', 'AGC 押金全退挖矿', 'success', '押 0.002 BNB/份，24h 后 claimFree 全退 + 奖励币', 'agc-refund-mine'),
    ('0x08adcd7912c12cf4c755a96512e06817f2bb2bb5', 'FLYSale', 'skip', '第3轮 buy(uint256) USDT 付款，50% 进 fundWallet 超国库 30% 上限', 'fly-usdt-round-sale'),
    ('0x95115e644c87df8ba170393de232402c60005fe4', 'BBF Bnb Burn Forge', 'skip', 'mint(Claim[],maxTickets) 0.01 USDT/票，需链下 burn Merkle 证明，进度低', 'bbf-burn-proof-mint'),
    ('0xd64cc60b49d91fcb3d671be7131b4ae6642d7a10', 'BNBFARM GEN0', 'skip', 'mint(count,maxBnb) 付费盲盒，进度 <60%', 'bnbfarm-blindbox'),
    ('0x858e78d395c9075dfa763f85e1e55671eff68d2b', '未命名 claimGift', 'success', '开售后六号 claimGift value=0 全部 status=1', 'claimgift-free'),
]


def seed():
    n = 0; have = {r.get('address') for r in records()}
    t = templates()
    for a, name, res, reason, tname in KNOWN:
        if a in have: continue
        try: info = C.resolve(a)
        except Exception as e: print('seed fail', a, e); continue
        add({'address': a, 'name': name, 'fingerprint': info['fingerprint'], 'selectors': info['selectors'], 'proxy': info['proxy'],
             'impl': info['impl'], 'result': res, 'reason': reason, 'source': 'seed:known'})
        if info['fingerprint'] and info['fingerprint'] not in t:
            t[info['fingerprint']] = {'name': tname, 'example': a, 'note': reason, 'at_cst': now()}
        n += 1
    sold = C._load('$AIDBOT_DATA/soldout.json', {})
    addrs = [a for a in sold if a not in have][:300]
    codes = C.batch([('eth_getCode', [a, 'latest']) for a in addrs]) if addrs else []
    for a, code in zip(addrs, codes):
        if not isinstance(code, str) or len(code) < 6: continue
        add({'address': a, 'name': sold[a].get('name', ''), 'fingerprint': C.fingerprint(code), 'selectors': C.push4(code),
             'result': 'fail', 'reason': 'SoldOut（免费盘售罄，历史预检）', 'source': 'seed:soldout', 'at_cst': sold[a].get('at', now())})
        n += 1
    save_templates(t)
    return n


def main():
    a = sys.argv[1:]
    if not a: sys.exit(__doc__)
    cmd = a[0]
    def opt(k, d=None): return a[a.index(k) + 1] if k in a else d
    if cmd == 'stats': print(json.dumps(stats(), ensure_ascii=False, indent=1))
    elif cmd == 'show': print(json.dumps(summarize(a[1]), ensure_ascii=False, indent=1))
    elif cmd == 'match':
        info = C.resolve(a[1]); m = match(info)
        print(json.dumps({'fingerprint': info['fingerprint'], 'exact': [(r['address'], r.get('name'), r.get('result')) for r in m['exact']],
                          'similar': [(r['address'], r.get('name'), r['_similarity']) for r in m['similar']], 'template': m['template']}, ensure_ascii=False, indent=1))
    elif cmd == 'record':
        addr = a[1].lower(); info = C.resolve(addr)
        rec = {'address': addr, 'fingerprint': info['fingerprint'], 'selectors': info['selectors'], 'proxy': info['proxy'], 'impl': info['impl'],
               'result': opt('--result', 'note'), 'reason': opt('--reason', ''), 'tx': opt('--tx'), 'method': opt('--method'),
               'value_wei': opt('--value-wei'), 'note': opt('--note'), 'source': 'manual'}
        add({k: v for k, v in rec.items() if v is not None}); print('ok', rec['result'])
    elif cmd == 'template':
        info = C.resolve(a[1]); t = templates()
        t[info['fingerprint']] = {'name': opt('--name', a[1]), 'example': a[1].lower(), 'note': opt('--note', ''), 'at_cst': now()}
        save_templates(t); print('ok template', info['fingerprint'][:16])
    elif cmd == 'timing':  # 各环节平均耗时（来自 library/reports）
        import glob; agg = {}; n = 0
        for f in sorted(glob.glob(os.path.join(C.LIB, 'reports', '*.json')))[-int(opt('--last', '50')):]:
            try: t = json.load(open(f)).get('timing_s', {})
            except Exception: continue
            n += 1
            for k, v in t.items(): agg.setdefault(k, []).append(v)
        print(json.dumps({'reports': n, 'avg_s': {k: round(sum(v) / len(v), 2) for k, v in sorted(agg.items())}, 'max_total_s': max(agg.get('total', [0]))}, ensure_ascii=False, indent=1))
    elif cmd == 'seed': print('seeded', seed())
    else: sys.exit(__doc__)


if __name__ == '__main__': main()
