#!/usr/bin/env python3
"""监控公开 TG 频道（默认 聪哥 Agent ID Call @agentidcall），只输出上次以来的新帖。
用法: python3 tgcall.py [频道名...]   状态存 tg/state.json。只读。"""
import json, os, re, sys, html, urllib.request
D = os.path.dirname(os.path.abspath(__file__)); SD = os.path.join(os.environ['AIDBOT_DATA'], 'tg') if os.environ.get('AIDBOT_DATA') else D; os.makedirs(SD, exist_ok=True); ST = os.path.join(SD, 'state.json')
chs = sys.argv[1:] or ['agentidcall']
st = json.load(open(ST)) if os.path.exists(ST) else {}
out = {}
for ch in chs:
    req = urllib.request.Request(f'https://t.me/s/{ch}', headers={'user-agent': 'Mozilla/5.0'})
    h = urllib.request.urlopen(req, timeout=20).read().decode('utf8', 'ignore')
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
json.dump(st, open(ST, 'w'))
print(json.dumps(out, ensure_ascii=False, indent=1))
