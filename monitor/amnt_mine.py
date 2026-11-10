#!/usr/bin/env python3
"""AMNT（agentmining.fun，0x2d07…4e4E）Plan B（lane 2，28 bit，每号每天 20 份，每份 2500 AMNT）CPU 挖矿。
value=0、无授权：按 0 元免费 Mint 自动规则（每笔 baw preview 干净才 execute）。2026-11-10 用户发 agentmining.fun「看看这个」。
每天 21:00–23:00 CST 窗口：pinSession（如需）→ 未选计划的号 selectPlan(2)（首次免费）→ 按号轮流挖，凑 10 份或临近收窗提交 mineBatch。
usage: amnt_mine.py [--dry] [--once]"""
import sys, os, json, time, subprocess
HERE = os.path.dirname(os.path.abspath(__file__))
dry = '--dry' in sys.argv
sys.argv = ['fair_buy.py', '0x2d07b9ccdf5fcdbe26167358b94eeb4c9e995e4e', '0x2d07b9ccdf5fcdbe26167358b94eeb4c9e995e4e', '1'] + (['--dry'] if dry else [])
exec(open(os.path.join(HERE, 'fair_buy.py')).read().split('from concurrent.futures import ThreadPoolExecutor')[0])
T = '0x2d07b9ccdf5fcdbe26167358b94eeb4c9e995e4e'; ADP = '0x3c44b8ee0581f70026a7b650ec02373e25ef3200'
KM = '$AIDBOT_HOME/amnt/km'; OUT = os.path.join(HERE, 'discover_out', 'amnt_mine.json'); LOG = os.path.join(HERE, 'discover_out', 'amnt_mine.log')
ORDER = ['主', '001', '002', '海贼王', '蜡笔小新', '高']
ACC = sorted([a for a in A if a['label'] in ORDER], key=lambda a: ORDER.index(a['label']))
def v(n, arg=''): return ci(T, '0x' + sel(n) + arg)
def log(*x):
    s = time.strftime('%m-%d %H:%M:%S') + ' ' + ' '.join(str(i) for i in x)
    print(s, flush=True); open(LOG, 'a').write(s + '\n')
state = {'events': []}
def save(**k):
    state.update(k); state['time_cst'] = time.strftime('%m-%d %H:%M'); json.dump(state, open(OUT, 'w'), ensure_ascii=False, default=str)
def ev(x): state['events'].append(time.strftime('%H:%M:%S') + ' ' + x); save()
def session():
    while True:
        try:
            if v('inSession()'): return True
        except Exception as e: log('read err', e)
        anchor, dl = v('anchor()'), v('dayLength()'); now = time.time()
        nxt = anchor if now < anchor else anchor + (int((now - anchor) // dl) + 1) * dl
        save(status='waiting', next_open=time.strftime('%m-%d %H:%M', time.localtime(nxt))); time.sleep(max(3, min(60, nxt - now - 2)))
def main():
    session()
    d = v('dayIndex()'); end = v('anchor()') + d * v('dayLength()') + v('sessionLength()')
    log('session day', d, 'ends', time.strftime('%H:%M', time.localtime(end)))
    if int(call(T, '0x' + sel('sessionSeedBase(uint256)') + u(d)), 16) == 0:
        st = go(ACC[0], T, '0x' + sel('pinSession()')); log('pin', st); ev('pinSession ' + str(st[0]))
        time.sleep(3)
    seed = call(T, '0x' + sel('sessionSeedBase(uint256)') + u(d))
    if int(seed, 16) == 0: log('seed still 0'); time.sleep(10); seed = call(T, '0x' + sel('sessionSeedBase(uint256)') + u(d))
    info = {}
    for a in ACC:
        aid = ci(ADP, '0x' + sel('agentIdOf(address)') + ad(a['bsc']))
        if not aid: log(a['label'], 'no agent id'); continue
        lane = v('effectiveLane(uint256)', u(aid))
        if lane == 0:
            st = go(a, T, '0x' + sel('selectPlan(uint8)') + u(2)); log(a['label'], 'selectPlan(2)', st); ev(a['label'] + ' selectPlan ' + str(st[0]))
            time.sleep(2); lane = v('effectiveLane(uint256)', u(aid))
        if lane != 2: log(a['label'], 'lane', lane, 'skip'); continue
        done = v('cpuCountOfId(uint256)', u(aid)) if v('cpuDayOfId(uint256)', u(aid)) == d else 0
        info[a['label']] = {'a': a, 'id': aid, 'done': done, 'pending': [], 'mined': 0}
    tgt = '0x%064x' % v('cpuTarget()')
    def submit(lab):
        x = info[lab]; n = x['pending'][:10]
        if not n: return
        data = '0x' + sel('mineBatch(uint8,uint256[])') + u(2) + u(64) + u(len(n)) + ''.join(z[2:].rjust(64, '0') for z in n)
        st = go(x['a'], T, data); log(lab, 'mineBatch', len(n), st); ev(f'{lab} mineBatch {len(n)} {st[0]} {str(st[1])[:70]}')
        if st[0] in ('ok', 'dry_ok'): x['done'] += len(n); x['mined'] += len(n) * 2500
        x['pending'] = x['pending'][len(n):] if st[0] in ('ok', 'dry_ok', 'unknown') else []
    for lab, x in info.items():
        while x['done'] + len(x['pending']) < 20:
            left = end - time.time()
            if left < 240:
                submit(lab); break
            if v('cpuProofsToday()') >= v('cpuDailyCap()'): log('network cap reached'); submit(lab); break
            try:
                r = subprocess.run([KM, seed, x['a']['bsc'], hex(x['id']), '2', hex(d), tgt, '1', '2'], capture_output=True, text=True, timeout=max(30, left - 200))
                nonce = r.stdout.split()[0]
            except Exception as e:
                log(lab, 'km timeout/err', e); submit(lab); break
            pd = call(T, '0x' + sel('proofDigest(uint256,address,uint256,uint8,uint256)') + u(d) + ad(x['a']['bsc']) + u(x['id']) + u(2) + nonce[2:].rjust(64, '0'))
            if int(pd, 16) > int(tgt, 16): log(lab, 'bad proof', nonce); continue
            x['pending'].append(nonce); save(status='mining', progress={k: {'done': y['done'], 'pending': len(y['pending'])} for k, y in info.items()})
            if len(x['pending']) >= 10 or x['done'] + len(x['pending']) >= 20: submit(lab)
        if x['pending']: submit(lab)
        if end - time.time() < 240: break
    save(status='finished_day', day=d, progress={k: {'done': y['done'], 'amnt': y['done'] * 2500} for k, y in info.items()})
    log('day done', {k: y['done'] for k, y in info.items()})
while True:
    try: main()
    except Exception as e: log('error', repr(e)); time.sleep(30)
    if '--once' in sys.argv: break
    time.sleep(300)
