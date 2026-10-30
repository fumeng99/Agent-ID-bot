#!/usr/bin/env python3
"""读 config/config.json 的账号：python3 accounts.py [--auto|--paid] -> 每行 "dir label address"。--auto 只列允许自动免费 Mint 的。"""
import os, sys, json
here = os.path.dirname(os.path.abspath(__file__))
for p in [os.environ.get('AIDBOT_CONFIG', ''), os.path.join(here, '..', 'config', 'config.json'), os.path.join(here, 'config.json')]:
    if p and os.path.exists(p): cfg = json.load(open(p)); break
else: sys.exit('找不到 config/config.json（复制 config/config.example.json）')
for a in cfg.get('accounts', []):
    if '--auto' in sys.argv and not a.get('autoFreeMint'): continue
    if '--paid' in sys.argv and not a.get('allowPaid'): continue
    print(a['dir'], a.get('label', ''), a.get('address', ''))
