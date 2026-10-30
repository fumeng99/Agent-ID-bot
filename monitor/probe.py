#!/usr/bin/env python3
"""项目官网 Agent 指南探测（借鉴 EZbot）：python3 probe.py <官网URL>
依次读 /skill.md /llms.txt /agent.md /agents.md /api/state /api/collections /.well-known/agent.json，输出存在的文件内容（截断）和其中的合约地址。只读。"""
import sys, re, json, urllib.request, urllib.parse
u = sys.argv[1].rstrip('/'); b = '{0.scheme}://{0.netloc}'.format(urllib.parse.urlparse(u if '://' in u else 'https://' + u))
out = {}
for p in ['/skill.md', '/llms.txt', '/agent.md', '/agents.md', '/api/state', '/api/collections', '/.well-known/agent.json']:
    try:
        r = urllib.request.urlopen(urllib.request.Request(b + p, headers={'user-agent': 'Mozilla/5.0'}), timeout=12)
        t = r.read().decode('utf8', 'ignore')
        if r.status == 200 and not t.lstrip().lower().startswith(('<!doctype', '<html')):
            out[p] = {'contracts': sorted(set(re.findall(r'0x[0-9a-fA-F]{40}', t))), 'text': t[:3000]}
    except Exception: pass
print(json.dumps({'base': b, 'found': list(out), 'files': out}, ensure_ascii=False, indent=1))
