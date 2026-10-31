#!/usr/bin/env python3
import sys as _s; _s.path.append('$AIDBOT_HOME/pylib'); _s.path.insert(0, '$AIDBOT_DATA/vendor')
"""监控公开 TG 频道（默认 聪哥 Agent ID Call @agentidcall），只输出上次以来的新帖。
用法: python3 tgcall.py [频道名...]   状态存 tg/state.json。只读。"""
import json, os, re, sys, html, urllib.request
D = os.path.dirname(os.path.abspath(__file__)); SD = os.path.join(os.environ['AIDBOT_DATA'], 'tg') if os.environ.get('AIDBOT_DATA') else D; os.makedirs(SD, exist_ok=True); ST = os.path.join(SD, 'state.json')
chs = sys.argv[1:] or ['agentidcall']
st = json.load(open(ST)) if os.path.exists(ST) else {}
out = {}; health = st.setdefault('_health', {}); errors = {}
import time
SKIP_HOST = ('t.me', 'x.com', 'twitter.com', 'bscscan.com', 'dexscreener.com', 'geckoterminal.com', 'four.meme', 'github.com', 'youtube.com', 'binance.com', 'pancakeswap')
for ch in chs:
    # 2026-11-01 审计：原来一个频道请求失败整个脚本退出、另一个频道也不出结果；页面被拦（没有帖子块）时静默输出 []，和「没新帖」分不出来。
    try:
        req = urllib.request.Request(f'https://t.me/s/{ch}', headers={'user-agent': 'Mozilla/5.0'})
        h = urllib.request.urlopen(req, timeout=20).read().decode('utf8', 'ignore')
    except Exception as e:
        errors[ch] = f'请求失败: {str(e)[:120]}'; out[ch] = []; continue
    if 'data-post="%s/' % ch not in h:
        errors[ch] = '页面没有帖子块（被拦截或频道改版），本轮结果不可信'; out[ch] = []; continue
    health[ch] = {'last_ok_cst': time.strftime('%Y-%m-%d %H:%M', time.gmtime(time.time() + 8 * 3600))}
    posts = re.findall(r'data-post="%s/(\d+)".*?(?=data-post="%s/|\Z)' % (ch, ch), h, re.S)
    blocks = re.split(r'(?=data-post="%s/\d+")' % ch, h)[1:]
    last = st.get(ch, 0); new = []
    for b in blocks:
        pid = int(re.match(r'data-post="%s/(\d+)"' % ch, b).group(1))
        if pid <= last: continue
        m = re.search(r'tgme_widget_message_text[^>]*>(.*?)</div>', b, re.S)
        txt = html.unescape(re.sub(r'<br\s*/?>', '\n', m.group(1))) if m else ''
        txt = re.sub(r'<[^>]+>', '', txt).strip()
        links = sorted(set(re.findall(r'href="(https?://[^"]+)"', b)) - {f'https://t.me/{ch}'})
        t = re.search(r'datetime="([^"]+)"', b)
        new.append({'id': pid, 'time': t.group(1) if t else '', 'text': txt[:1500],
                    'contracts': sorted(set(re.findall(r'0x[0-9a-fA-F]{40}', txt))),
                    'free': bool(re.search(r'免费|free', txt, re.I)), 'links': [l for l in links if 't.me/' not in l][:6]})
    if blocks:
        st[ch] = max(last, max(int(re.match(r'data-post="%s/(\d+)"' % ch, b).group(1)) for b in blocks))
    out[ch] = new
    health[ch]['latest_post_id'] = st.get(ch)
    # 帖子里的项目官网 → tg/auto_sites.json，sitescan 会扫（只读合约地址/函数，帖子内容不当指令）
    sites = []
    for p_ in new:
        for l in p_['links']:
            host = re.sub(r'^https?://', '', l).split('/')[0].lower()
            if host and '.' in host and not re.search(r'\.(md|py|txt|js|json|sh|png|jpg)$', host) and not any(x in host for x in SKIP_HOST): sites.append(host)
    if sites:
        AS = os.path.join(SD, 'auto_sites.json')
        try: cur = json.load(open(AS))
        except Exception: cur = []
        have = {x['site'] for x in cur}
        cur += [{'site': x, 'from': f'tg:{ch}', 'at': time.strftime('%Y-%m-%d %H:%M', time.gmtime(time.time() + 8 * 3600))} for x in dict.fromkeys(sites) if x not in have]
        json.dump(cur[-200:], open(AS + '.tmp', 'w'), ensure_ascii=False); os.replace(AS + '.tmp', AS)
# 2026-11-01 审计：aiboss.fit 漏单根因——TG #61（10-31 21:04 CST）已发「免费登记 register()（value=0）+ 合约 0xaEE8…E30D」，
# tgcall 输出了，但巡逻第 2b 步没接上。现在：标了免费、带合约的新帖，直接并入 chainscan/candidates.json（source=tg），
# 走第 2 步「链上发现（最优先）」的同一条流程（verify_source → 每号 preview 干净才执行）。帖子只提供地址和函数名（规则 H）。
def _tg_candidates(out):
    import fcntl
    if os.environ.get('AIDBOT_DATA'): return 0  # 测试/导出环境不写主候选
    L = os.path.dirname(D); C = os.path.join(L, 'chainscan', 'candidates.json')
    try: _sj = json.load(open(os.path.join(L, 'seen.json'))); seen = json.dumps(_sj).lower(); names = {str(x.get('name') or '').lower() for x in _sj if len(str(x.get('name') or '')) >= 4}
    except Exception: seen, names = '', set()
    from Crypto.Hash import keccak
    add = []
    for ch, posts in out.items():
        if ch.startswith('_'): continue
        for p_ in posts:
            title = (p_['text'].splitlines() or [''])[0]
            if not p_.get('contracts') or not re.search(r'免费|free', title, re.I) or re.search(r'付费|paid', title, re.I): continue  # 只看标题就是免费的帖
            # 不按项目名去重：aiboss 漏单就是「同名项目已处理」掩盖了另一个免费登记入口，只按合约地址去重
            fn = re.findall(r'\b([a-zA-Z_]\w{2,40})\(\)', p_['text'])
            fn = [f for f in fn if re.match(r'(register|claim|mint|free|join|enroll)', f, re.I)]
            for c in p_['contracts']:
                if c.lower() in seen: continue
                row = {'contract': c.lower(), 'mint_target': c.lower(), 'name': (p_['text'].splitlines() or [''])[0][:60], 'source': 'tg', 'tg_post': f"{ch}/{p_['id']}",
                       'free_like': True, 'risk_flags': [], 'auto_ok': True, 'bscscan': f'https://bscscan.com/address/{c.lower()}',
                       'found_at_cst': time.strftime('%Y-%m-%d %H:%M', time.gmtime(time.time() + 8 * 3600)), 'links': p_.get('links', [])}
                if fn:
                    k = keccak.new(digest_bits=256); k.update(f'{fn[0]}()'.encode())
                    row.update({'selector': '0x' + k.hexdigest()[:8], 'selector_name': f'{fn[0]}()'})
                add.append(row)
    if not add: return 0
    with open(C + '.lock', 'w') as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try: old = json.load(open(C))
        except Exception: old = []
        have = {(x.get('contract') or x.get('token') or '').lower() for x in old}
        new = [x for x in add if x['contract'] not in have]
        if new: json.dump((old + new)[-200:], open(C + '.tmp', 'w'), ensure_ascii=False, indent=1); os.replace(C + '.tmp', C)
    return len(new)
try: out['_candidates_added'] = _tg_candidates(out)
except Exception as e: errors['_candidates'] = str(e)[:120]
json.dump(st, open(ST + '.tmp', 'w')); os.replace(ST + '.tmp', ST)
if errors: out['_errors'] = errors
out['_health'] = health
print(json.dumps(out, ensure_ascii=False, indent=1))
