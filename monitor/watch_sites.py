#!/usr/bin/env python3
"""Agent ID 项目官网指南变动监控：对 watchlist 里每个站读 /llms.txt /skill.md /agent.md，
内容变了就输出新出现的合约地址和摘要（项目常在同一站点开新系列）。只读。状态 watch_state.json。"""
import json, os, re, hashlib, urllib.request
from concurrent.futures import ThreadPoolExecutor
D = os.path.dirname(os.path.abspath(__file__)); SD = os.path.join(os.environ['AIDBOT_DATA'], 'sites') if os.environ.get('AIDBOT_DATA') else D; os.makedirs(SD, exist_ok=True); ST = os.path.join(SD, 'watch_state.json')
SITES = ['ptah.trade','bibiai.fun','agentpunk.fun','stockerpunk.com','anthrobit.fun','build-100.com','bncworld.fun','hashdroids.fun',
 'gridpunks-vert.vercel.app','aivy.me','aiagentid.vercel.app','agentpad.surge.sh','agentidlive.ai','agenticpad.meme','agenticinu.lol',
 'agentbrowser.fun','agent-skelly.fun','artrural.fun','gearz.fun','agenthash.fun','glyphmind.surge.sh','jager.live','boltbook.fun']
extra = os.path.join(D, 'watch_sites.txt')
if os.path.exists(extra): SITES += [l.strip() for l in open(extra) if l.strip() and not l.startswith('#')]
def grab(site):
    out = {}
    for p in ['/llms.txt', '/skill.md', '/agent.md']:
        try:
            r = urllib.request.urlopen(urllib.request.Request('https://' + site + p, headers={'user-agent': 'Mozilla/5.0'}), timeout=10)
            t = r.read(200000).decode('utf8', 'ignore')
            if not t.lstrip().lower().startswith(('<!doctype', '<html', '{"error')): out[p] = t
        except Exception: pass
    return site, out
st = json.load(open(ST)) if os.path.exists(ST) else {}
first = not st; changes = []
with ThreadPoolExecutor(8) as ex:
    for site, files in ex.map(grab, sorted(set(SITES))):
        for p, t in files.items():
            k = site + p; h = hashlib.sha256(t.encode()).hexdigest()
            old = st.get(k, {}); cas = sorted(set(a.lower() for a in re.findall(r'0x[0-9a-fA-F]{40}', t)))
            if old.get('h') != h:
                if not first:
                    changes.append({'url': 'https://' + k, 'new_contracts': [c for c in cas if c not in old.get('cas', [])],
                                    'free_hint': bool(re.search(r'free|免费|value\s*[:=]\s*0\b', t, re.I)), 'head': t[:600]})
                st[k] = {'h': h, 'cas': cas}
json.dump(st, open(ST, 'w'))
print(json.dumps({'sites': len(set(SITES)), 'baseline' if first else 'changes': len(st) if first else changes}, ensure_ascii=False, indent=1))
