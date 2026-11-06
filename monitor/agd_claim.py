#!/usr/bin/env python3
"""AGD（0x9a46…6bac）A 档到期自动 claimA（退押金 + AGD），领到立即全卖成 USDT（sell_retry，滑点 ≤8%）。
2026-11-07 01:35 用户「打」（回答「…24 小时后自动领回并把 AGD 全卖成 U 吗」）。value=0、preview 干净才执行。只限该合约。"""
import sys, os, json, time, subprocess
DRY = '--dry' in sys.argv
M = '0x9a467a8844d1394a84bc502669faa30573576bac'; TOK = '0x2778875994faa223b2ee526601f8d956a276a878'
sys.argv = ['fair_buy.py', M, M, '10'] + (['--dry'] if DRY else [])
D = os.path.dirname(os.path.abspath(__file__))
src = open(os.path.join(D, 'fair_buy.py')).read()
exec(src.split('from concurrent.futures import ThreadPoolExecutor')[0])
ST = os.path.join(D, 'discover_out/agd_claim_state.json')
try: st = json.load(open(ST))
except Exception: st = {}
now = time.time()
for a in A:
    if now < st.get(a['label'], 0): continue
    pad = a['bsc'][2:].lower().rjust(64, '0')
    n = int(call(M, '0x' + sel('aPositionCount(address)') + pad), 16)
    ids = []
    for i in range(n):
        p = call(M, '0x' + sel('positionA(address,uint256)') + pad + u(i))
        opened = int(p[2:66], 16); claimed = int(p[194:258], 16)
        if not claimed and now >= opened + 86400 + 30: ids.append(i)
    if not ids: continue
    ids = ids[:50]
    data = '0x' + sel('claimA(uint256[])') + u(32) + u(len(ids)) + ''.join(u(i) for i in ids)
    r = go(a, M, data, value='0')
    print(time.strftime('%m-%d %H:%M:%S', time.gmtime(now + 8 * 3600)), a['label'], 'claimA', ids, r[0], str(r[1])[:90], flush=True)
    st[a['label']] = now + (60 if r[0] in ('ok', 'dry_ok') else 600)
    if r[0] == 'ok' and not DRY:
        time.sleep(6)
        lf = open(os.path.join(D, 'discover_out/agc_sell.log'), 'a')
        lf.write(time.strftime('%m-%d %H:%M:%S ', time.gmtime(time.time() + 8 * 3600)) + f'AGD claimA ok {a["label"]} -> sell {TOK}\n'); lf.flush()
        subprocess.Popen(['python3', 'sell_retry.py', TOK, '100', '--slip', '8', a['label']], stdout=lf, stderr=lf, cwd=D, start_new_session=True)
json.dump(st, open(ST, 'w'))
