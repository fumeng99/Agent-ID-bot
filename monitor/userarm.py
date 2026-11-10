#!/usr/bin/env python3
"""用户点名项目的「到 X% 立马打满」预埋（2026-11-10 19:51 用户原话「到60% 立马打满」，指 AKITA akita.bar）。
只执行 userarm.json 里用户逐项授权的条目；每条：合约、门槛、每号份数上限、单价、账号、用户原话。
条件：mintEnabled()=true、launched()=false、totalMinted()/MAX_UNITS() ≥ 门槛 → 每号补到上限（10 - unitsOf），
交给 confirmed_buy.py（规则 B 源码验证、余额留 0.003、preview 干净才 execute、unknown 不重发）。每号每条只发一次。
discoverd 每轮（约 60 秒）用 flock 拉起，本进程内 1.5 秒一读，最长跑 540 秒。
"""
import sys, os, json, time, subprocess
D = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, D); os.chdir(D)
from rpcpool import rpc
from codeutil import sel as _sel


def sel(sig):  # 2026-11-10 AKITA 首发失败根因：codeutil.sel 不带 0x，confirmed_buy 拒收（打印用法退出）
    x = _sel(sig); return x if x.startswith('0x') else '0x' + x

CFG = 'userarm.json'; EV = 'discover_out/userarm_events.jsonl'
ACCTS = json.load(open('$AIDBOT_HOME/baw/accounts/accounts.json'))


def ev(**k):
    k['t'] = time.strftime('%m-%d %H:%M:%S', time.gmtime(time.time() + 8 * 3600)) + ' CST'
    with open(EV, 'a') as f: f.write(json.dumps(k, ensure_ascii=False) + '\n')


def call(to, data):
    try: r = rpc('eth_call', [{'to': to, 'data': data}, 'latest']); return int(r, 16) if r and r != '0x' else None
    except Exception: return None


def save(cfg):
    tmp = CFG + '.tmp'; json.dump(cfg, open(tmp, 'w'), ensure_ascii=False, indent=1); os.replace(tmp, CFG)


def step(cfg):
    busy = False
    for it in cfg:
        if it.get('done'): continue
        busy = True
        C = it['to']
        en = call(C, sel('mintEnabled()')); la = call(C, sel('launched()'))
        tm = call(C, sel('totalMinted()')); mx = call(C, sel('MAX_UNITS()')) or it.get('max_units')
        if la == 1: it['done'] = 'launched'; ev(to=C, state='launched_stop'); continue
        if None in (en, tm) or not mx: continue
        pct = tm / mx
        it['last'] = {'enabled': en, 'minted': tm, 'pct': round(pct * 100, 2), 'ts': int(time.time())}
        if en != 1 or pct < it['threshold']: continue
        fired = it.setdefault('fired', {})
        groups = {}
        for a in ACCTS:
            if a['label'] not in it['accounts'] or a['label'] in fired: continue
            have = call(C, sel('unitsOf(address)') + a['bsc'][2:].lower().rjust(64, '0')) or 0
            n = min(it['per_wallet'] - have, mx - tm)
            if n <= 0: fired[a['label']] = 'full'; continue
            groups.setdefault(n, []).append(a['label'])
        for n, labs in groups.items():
            for l in labs: fired[l] = 'sent'
            save(cfg)
            data = sel('mint(uint256)') + hex(n)[2:].rjust(64, '0')
            ev(to=C, state='fire', pct=round(pct * 100, 2), units=n, labels=labs)
            r = subprocess.run(['python3', 'confirmed_buy.py', '--to', C, '--data', data, '--value-bnb', f"{n * it['price_bnb']:.6f}"] + (['--token', it['token']] if it.get('token') else []) + labs,
                               capture_output=True, text=True, timeout=400)
            ev(to=C, state='result', units=n, labels=labs, out=r.stdout[-3000:], err=r.stderr[-500:])
        if all(l in fired for l in it['accounts']): it['done'] = 'fired'
        save(cfg)
    return busy


def main():
    t0 = time.time()
    while time.time() - t0 < 540:
        try: cfg = json.load(open(CFG))
        except Exception: return
        if not step(cfg): return
        save(cfg)
        time.sleep(1.5)


if __name__ == '__main__':
    main()
