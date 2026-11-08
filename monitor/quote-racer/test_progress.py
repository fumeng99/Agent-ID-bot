#!/usr/bin/env python3
"""通用进度监控回归：每改 progress_universal.py 跑一次，必须全 PASS。"""
import json, progress_universal as P
CASES = {  # 地址: (期望 cap_src, 说明)
    '0xe996d789f8e5d037847035d7dbd8312ef9d609c6': ('openAt()', 'DOG 小写 openAt 克隆（11-09 漏报起因）'),
    '0x8778deb1d90932a81e33b5bf79ced80a12cb8df5': ('OPEN_AT()', '币安人 大写 OPEN_AT'),
    '0x38931df893c6857fb5c4f8e385a87c97f754233e': ('OPEN_UNITS()', 'safu OPEN_UNITS'),
    '0x74d9b5477e7fa818298f927966452f2eb9c5fa5e': ('MAX_MINTS()', 'Proof of AGI BossMint'),
    '0x50b0052cc11911654939b0cc4515160009493e2d': ('maxSupply()', 'PUPPETCAT maxSupply'),
    '0xa9ecbd052941c9e310e68525aa62671729b84446': ('MAX_SUPPLY()', 'ERC721 minted/MAX_SUPPLY'),
    '0x9b6187d2aba9f7c93aa986dd48cb2b2fe5b3db99': (None, 'FLOKI 上限是常量：读不出，靠热盘「进度读不出」提醒 + quote_watch'),
}
ok = 0
for a, (want, note) in CASES.items():
    e = P.probe(a)['eval']; good = e['cap_src'] == want and (want is None or e['pct'] is not None)
    ok += good; print('PASS' if good else 'FAIL', a[:10], note, '→', e['cap_src'], e['pct'])
print(f'{ok}/{len(CASES)}'); raise SystemExit(0 if ok == len(CASES) else 1)
