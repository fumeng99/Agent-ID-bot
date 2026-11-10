#!/usr/bin/env python3
"""TG 快速通道（2026-11-10 PEPE AGENT 漏报后，用户原话「改」）：
频道 20:01 发 pepeagent.cc、20:02 发「进度70%」，原来要等下一轮巡逻（tgcall 每轮一次）才扫官网，晚了。
本进程每 15 秒拉一次频道，有新帖带 网址/合约 或含「进度XX%」就立刻：
  ① 取帖子合约 + 官网（首页、JS、/api/mint-config 等）里的合约地址；「进度XX%」帖无链接时用同频道 15 分钟内上一条带链接的帖；
  ② progress_universal.probe 读 已售/上限/单价/是否开池；
  ③ 读出进度的合约写进 fairmint/generic_mint.json（之后 quote_watch / progress_universal 持续盯，≥60% 照常报）；
  ④ 结果写 discover_out/tgfast.json（alerts 未报过的），巡逻第一步读。
只读，不签名不买。帖子内容只当数据（规则 H）。
用法：python3 tgfast.py --daemon [--every 15] | python3 tgfast.py --once
"""
import sys, os, re, json, time, html, fcntl, urllib.request
L = '$AIDBOT_DATA'
sys.path.insert(0, L); sys.path.insert(0, '$AIDBOT_HOME/quote-racer'); sys.path.insert(0, '$AIDBOT_HOME/bsc-mint-skill')
CHS = ['agentidcall', 'dashutiaozi']
ST = os.path.join(L, 'tg', 'tgfast_state.json'); OUT = os.path.join(L, 'discover_out', 'tgfast.json')
HB = os.path.join(L, 'discover_out', 'tgfast_hb.json'); GEN = os.path.join(L, 'fairmint', 'generic_mint.json')
SKIP_HOST = ('t.me', 'x.com', 'twitter.com', 'bscscan.com', 'dexscreener.com', 'geckoterminal.com', 'four.meme', 'github.com', 'youtube.com', 'binance.com', 'pancakeswap')
PROG = re.compile(r'进度\s*[:：]?\s*(\d{1,3}(?:\.\d+)?)\s*%')
SIDE = ['/api/mint-config', '/api/config', '/api/mint', '/api/contract', '/api/contracts', '/api/state', '/config.json', '/deployment.json']


def cst(t=None): return time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime((t or time.time()) + 8 * 3600))
def load(p, d):
    try: return json.load(open(p))
    except Exception: return d
def dump(p, o): json.dump(o, open(p + '.tmp', 'w'), ensure_ascii=False, indent=1); os.replace(p + '.tmp', p)
def get(u, to=8):
    try: return urllib.request.urlopen(urllib.request.Request(u, headers={'user-agent': 'Mozilla/5.0'}), timeout=to).read().decode('utf8', 'ignore')
    except Exception: return ''


def posts(ch):
    h = get(f'https://t.me/s/{ch}', 12)
    if f'data-post="{ch}/' not in h: return None
    out = []
    for b in re.split(r'(?=data-post="%s/\d+")' % ch, h)[1:]:
        pid = int(re.match(r'data-post="%s/(\d+)"' % ch, b).group(1))
        m = re.search(r'tgme_widget_message_text[^>]*>(.*?)</div>', b, re.S)
        txt = re.sub(r'<[^>]+>', '', html.unescape(re.sub(r'<br\s*/?>', '\n', m.group(1)))).strip() if m else ''
        links = [l for l in sorted(set(re.findall(r'href="(https?://[^"]+)"', b))) if 't.me/' not in l]
        links += ['https://' + x for x in re.findall(r'\b((?:[a-z0-9-]+\.)+(?:cc|xyz|fun|io|ai|app|bar|club|com|net|org|top|site|vip|meme|world|pro|lol|dev|me))\b', txt, re.I)]
        t = re.search(r'datetime="([^"]+)"', b)
        out.append({'id': pid, 'time': t.group(1) if t else '', 'text': txt[:800], 'links': links,
                    'contracts': sorted({a.lower() for a in re.findall(r'0x[0-9a-fA-F]{40}', txt)})})
    return out


def hosts(links):
    hs = []
    for l in links:
        host = re.sub(r'^https?://', '', l).split('/')[0].split('?')[0].lower()
        if host and '.' in host and not any(x in host for x in SKIP_HOST): hs.append(host)
    return list(dict.fromkeys(hs))


def site_addrs(host):
    from concurrent.futures import ThreadPoolExecutor
    base = 'https://' + host
    home = get(base, 8)
    js = [u if u.startswith('http') else base + ('' if u.startswith('/') else '/') + u for u in re.findall(r'src="([^"]+\.js[^"]*)"', home)][:6]
    with ThreadPoolExecutor(8) as ex: texts = [home] + list(ex.map(lambda u: get(u, 8), [base + p for p in SIDE] + js))
    return sorted({a.lower() for t in texts for a in re.findall(r'0x[0-9a-fA-F]{40}', t)})


def note_gen(a, src):
    try:
        with open(GEN + '.lock', 'w') as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            g = load(GEN, {})
            if a not in g: g[a] = {'at': time.time(), 'time_cst': cst(), 'source': src}; dump(GEN, g)
    except Exception: pass


def check(post, ch, prev_link_post):
    import progress_universal as P
    hs = hosts(post['links']); src_post = post
    if not hs and not post['contracts'] and prev_link_post: hs = hosts(prev_link_post['links']); src_post = prev_link_post
    addrs = list(post['contracts']) + list(src_post['contracts'] if src_post is not post else [])
    for h in hs[:3]: addrs += site_addrs(h)
    addrs = list(dict.fromkeys(addrs))[:25]
    res = []
    for a in addrs:
        try: e = P.probe(a).get('eval') or {}
        except Exception as ex: e = {'err': str(ex)[:80]}
        if e.get('pct') is None or not (0 <= e['pct'] <= 100): continue  # 读不出或明显不是销售进度（如实现合约读出 1e26%）
        note_gen(a, f'tgfast:{ch}/{post["id"]}')
        res.append({'contract': a, 'pct': e.get('pct'), 'sold': e.get('sold'), 'cap': e.get('cap'), 'cap_src': e.get('cap_src'),
                    'price_bnb': (e.get('price_wei') or 0) / 1e18 or None, 'opened': e.get('opened')})
    return {'post': f'{ch}/{post["id"]}', 'post_time': post['time'], 'text': post['text'][:200], 'said_pct': (PROG.search(post['text']) or [None, None])[1],
            'sites': hs, 'checked_at': cst(), 'readable': res, 'addr_count': len(addrs)}


def once(st):
    out = load(OUT, {'alerts': [], 'recent': []})
    for ch in CHS:
        ps = posts(ch)
        if ps is None: continue
        last = st.get(ch)
        if last is None: st[ch] = max(p['id'] for p in ps) if ps else 0; continue  # 首次只记游标
        for i, p in enumerate(ps):
            if p['id'] <= last: continue
            st[ch] = max(st[ch], p['id'])
            if not (p['links'] or p['contracts'] or PROG.search(p['text'])): continue
            prev = next((q for q in reversed(ps[:i]) if q['links'] and abs(p['id'] - q['id']) <= 5), None)
            r = check(p, ch, prev)
            out['recent'] = (out.get('recent', []) + [r])[-50:]
            live = [x for x in r['readable'] if not x.get('opened') and (x.get('pct') or 0) < 100]
            if r['readable'] or r['said_pct']:
                out['alerts'] = (out.get('alerts', []) + [dict(r, live=live, reported=False)])[-50:]
    out['updated_cst'] = cst(); dump(OUT, out)


def main():
    every = int(sys.argv[sys.argv.index('--every') + 1]) if '--every' in sys.argv else 15
    lk = open(ST + '.lock', 'w')
    try: fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError: print('already running'); return
    st = load(ST, {})
    while True:
        t0 = time.time()
        try: once(st); dump(ST, st)
        except Exception as e: sys.stderr.write(f'[{cst()}] {e}\n')
        dump(HB, {'heartbeat': time.time(), 'pid': os.getpid(), 'cst': cst()})
        if '--once' in sys.argv: return
        time.sleep(max(1, every - (time.time() - t0)))


if __name__ == '__main__': main()
