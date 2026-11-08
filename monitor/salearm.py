#!/usr/bin/env python3
"""开售定时预埋（用户 2026-11-03 02:08 原话「要」，回应「提前审完并读出开售时间，到点当秒六号领免费份」）。
只做免费份（value=0）。付费部分不在本脚本范围，仍按原有付费规则。

discoverd 每约 60 秒拉起（flock /tmp/salearm.lock）：
1. 候选：fairmint/state.json 的 pre_other（预部署识别的合约，近 3 天）+ chainscan/candidates.json。
2. 源码必须已验证（verify_source.py），从 sourcify ABI 读：
   - 开售时间：无参 view、名字含 start/open/launch/begin 的 uint，值在 [现在, 现在+3 天]；
   - 免费入口：nonpayable、名字含 free/gift/claim/airdrop/mint 的函数，参数为 () / (uint256) / (address) / (uint256,address)，
     排除 fee/withdraw/owner/refund/admin/set/pause 等。
   写 salearm_plan.json。
3. 距开售 ≤ 90 秒：起一个独立进程 fire：睡到开售前 1 秒，之后每 0.4 秒用每个号 eth_call 试各免费入口，
   某号某入口不 revert 就立刻并发对该号跑 mint_many.py（源码验证 + preview 只增不减、无授权无风险才 execute，unknown 不重发）。最多试 180 秒。
   每个合约只 fire 一次；结果写 discover_out/salearm_events.jsonl。
"""
import sys, os, json, time, re, subprocess, urllib.request
D = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, D); os.chdir(D)
from rpcpool import rpc
from codeutil import sel

PLAN = 'salearm_plan.json'; EV = 'discover_out/salearm_events.jsonl'
EARLY_SPAWN = 900; LATE_FIRE = 6 * 3600
ACCTS = json.load(open('$AIDBOT_HOME/baw/accounts/accounts.json'))
ACCTS.sort(key=lambda x: 0 if x['label'] == '主' else 1)   # 2026-11-09 用户「主先打」：主永远第一个探测、第一个发
BAD = re.compile(r'sponsor|referr|invite|commission|rebate|dividend|owed|release|vest|stake|fee|withdraw|owner|refund|admin|set|pause|rescue|sweep|transfer|approve|burn|pull|pay|sell|redeem|snapshot|upgrade|renounce|init', re.I)
GOOD = re.compile(r'free|gift|claim|airdrop|mint', re.I)
STARTN = re.compile(r'start|open|launch|begin', re.I)


def load(p, d, tries=1):
    for i in range(tries):
        try: return json.load(open(p))
        except Exception:
            if i < tries - 1: time.sleep(0.2)
    return d


def save_plan(plan):
    # 原子写：先写临时文件再 os.replace，读方永远读到完整文件（修 11-03 18:00 开火读空表 KeyError）
    import fcntl
    with open(PLAN + '.lock', 'w') as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        tmp = f'{PLAN}.{os.getpid()}.tmp'
        with open(tmp, 'w') as f: json.dump(plan, f, ensure_ascii=False)
        os.replace(tmp, PLAN)


def now_cst(): return time.strftime('%m-%d %H:%M:%S', time.gmtime(time.time() + 8 * 3600))


def ev(rec):
    rec['time_cst'] = now_cst()
    open(EV, 'a').write(json.dumps(rec, ensure_ascii=False) + '\n'); print(json.dumps(rec, ensure_ascii=False), flush=True)


def abi_of(a):
    try:
        r = urllib.request.urlopen(f'https://sourcify.dev/server/v2/contract/56/{a}?fields=abi', timeout=15)
        return json.load(r).get('abi')
    except Exception:
        return None


def u(x): return hex(x)[2:].rjust(64, '0')


POW = []
def build(a, abi):
    starts, frees = [], []; POW.clear()
    for f in abi or []:
        if f.get('type') != 'function': continue
        n = f['name']; ins = [i['type'] for i in f['inputs']]; m = f.get('stateMutability')
        # 2026-11-09：openTime/openBlockNumber 这类是「已开池时间」，不是开售时间
        if m in ('view', 'pure') and not ins and STARTN.search(n) and not re.search(r'^open(Time|Block|edAt|BlockNumber)$', n) and f.get('outputs') and f['outputs'][0]['type'].startswith('uint'):
            starts.append(n)
        if m == 'nonpayable' and GOOD.search(n) and not BAD.search(n):
            # 2026-11-09 StonkChain 教训：mintEgg(nonce, referrer) 是解题型（keccak(seed,nonce) ≤ target），填 1 必失败
            if any(re.search(r'nonce|solution|proof|sig|answer', i.get('name') or '', re.I) for i in f['inputs']):
                POW.append(n); continue
            s = '0x' + sel(f"{n}({','.join(ins)})")
            if ins == []: frees.append((n, s))
            elif ins == ['uint256']: frees.append((n + '(1)', s + u(1)))
            elif ins == ['address']: frees.append((n + '(0)', s + u(0)))
            elif ins == ['uint256', 'address']: frees.append((n + '(1,0)', s + u(1) + u(0)))
    # 2026-11-05 修：Gachagent 把 claimSponsorReward（只给推荐人领佣金）当成免费入口排在 freeMint 前，三个号走错函数。
    # 现在排除推荐/佣金/分红/释放类，且 mint/free 类排前、claim 类排后。
    frees.sort(key=lambda f: 0 if re.search(r'mint|free', f[0], re.I) else 1)
    t = None
    for n in starts:
        try:
            v = int(rpc('eth_call', [{'to': a, 'data': '0x' + sel(n + '()')}, 'latest']), 16)
            if time.time() - 60 < v < time.time() + 3 * 86400: t = v; break
        except Exception: pass
    return t, frees


def fire(a, frees, start):
    while time.time() < start - 1: time.sleep(0.2)
    done = set(); t_end = max(start, time.time()) + 180
    ev({'contract': a, 'stage': 'armed_fire', 'start': start})
    procs = []
    while time.time() < t_end and len(done) < len(ACCTS):
        def probe(ac):   # 2026-11-05：六号并发试，不再一个个排队
            for name, cd in frees:
                try:
                    rpc('eth_call', [{'from': ac['bsc'], 'to': a, 'data': cd, 'value': '0x0'}, 'latest']); return ac, name, cd
                except Exception: continue
            return None
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(6) as ex:
            hits = [h for h in ex.map(probe, [ac for ac in ACCTS if ac['label'] not in done]) if h]
        for ac, name, cd in hits:
            done.add(ac['label'])
            procs.append((ac['label'], name, subprocess.Popen(['python3', 'mint_many.py', a, cd, ac['label']], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                                              env=dict(os.environ, MM_FAST='1'))))
        time.sleep(0.3)
    for lab, name, p in procs:
        try: out = p.communicate(timeout=300)[0]
        except Exception as e: out = f'超时 {e}'
        ev({'contract': a, 'stage': 'result', 'acct': lab, 'fn': name, 'out': out[-400:]})
    if len(done) < len(ACCTS):
        ev({'contract': a, 'stage': 'not_open_or_reverted', 'missing': [x['label'] for x in ACCTS if x['label'] not in done]})


def main():
    if len(sys.argv) > 1 and sys.argv[1] == 'fire':
        a = sys.argv[2]
        if len(sys.argv) > 3:   # 新：排期直接随参数传入，不再开火时读文件
            p = json.loads(sys.argv[3])
        else:
            p = None
            for _ in range(25):
                p = load(PLAN, {}, tries=3).get(a)
                if p and p.get('frees'): break
                time.sleep(0.2)
        ev({'contract': a, 'stage': 'fire_alive', 'pid': os.getpid()})
        if not p or not p.get('frees'):
            ev({'contract': a, 'stage': 'fire_error', 'why': '读不到排期'}); return
        try:
            fire(a, [tuple(x) for x in p['frees']], p['start'])
        except Exception as e:
            ev({'contract': a, 'stage': 'fire_error', 'why': repr(e)[:300]}); raise
        return
    plan = load(PLAN, None, tries=5)
    if plan is None: ev({'stage': 'plan_read_fail'}); return   # 读失败绝不用空表覆盖排期
    st = load('fairmint/state.json', {})
    cands = set()
    for k, v in (st.get('pre_other') or {}).items(): cands.add(k.lower())
    for c in load('chainscan/candidates.json', []) or []:
        if isinstance(c, dict) and c.get('contract'): cands.add(c['contract'].lower())
    t0 = time.time()
    order = sorted(cands, key=lambda a: (0 if (plan.get(a) or {}).get('start') else 1))
    for a in order:
        save_plan(plan)
        p = plan.get(a)
        if not (p and p.get('start')) and time.time() - t0 > 80: continue
        if p and p.get('spawned_at') and not p.get('alive_ok') and p.get('start', 0) > time.time() - LATE_FIRE:
            # 看门狗：拉起 30 秒还没 fire_alive 就重拉（最多 2 次）
            try: alive = any(f'"{a}"' in l and 'fire_alive' in l for l in open(EV).readlines()[-400:])
            except Exception: alive = False
            if alive: p['alive_ok'] = True
            elif time.time() - p['spawned_at'] > 30 and p.get('respawns', 0) < 2:
                p['respawns'] = p.get('respawns', 0) + 1; p['fired'] = None
                ev({'contract': a, 'stage': 'respawn', 'n': p['respawns']})
        if p and p.get('spawned_at') and not p.get('lost_reported') and time.time() > max(p.get('start', 0), p['spawned_at']) + 400:
            try: got = any(f'"{a}"' in l and ('"result"' in l or 'not_open_or_reverted' in l or 'fire_error' in l) for l in open(EV).readlines()[-800:])
            except Exception: got = True
            p['lost_reported'] = True
            if not got: ev({'contract': a, 'stage': 'fire_lost', 'why': '开火进程中途消失，没有任何结果'})
        if p and (p.get('fired') or p.get('skip_until', 0) > time.time()): continue
        if p and p.get('start') and p.get('frees'):
            pass
        else:
            try:
                vs = json.loads(subprocess.run(['python3', 'verify_source.py', a], capture_output=True, text=True, timeout=60).stdout.splitlines()[0]).get('verified')
            except Exception: vs = None
            if vs is not True:
                plan[a] = {'skip_until': time.time() + 1800, 'why': 'unverified'}; continue
            t, frees = build(a, abi_of(a))
            try: opened = int(rpc('eth_call', [{'to': a, 'data': '0x' + sel('opened()')}, 'latest']), 16) == 1
            except Exception: opened = False
            if opened:
                plan[a] = {'skip_until': time.time() + 6 * 3600, 'why': 'already_opened'}; ev({'contract': a, 'stage': 'skip_opened'}); continue
            if POW and not frees:
                plan[a] = {'skip_until': time.time() + 6 * 3600, 'why': 'pow_needs_solver', 'pow': list(POW)}
                ev({'contract': a, 'stage': 'pow_needs_solver', 'fns': list(POW), 'start': t}); continue
            if not t or not frees:
                plan[a] = {'skip_until': time.time() + 6 * 3600, 'why': 'no_start_or_free', 'frees': frees}; continue
            plan[a] = {'start': t, 'frees': frees, 'start_cst': time.strftime('%m-%d %H:%M:%S', time.gmtime(t + 8 * 3600))}
            ev({'contract': a, 'stage': 'planned', 'start_cst': plan[a]['start_cst'], 'frees': [f[0] for f in frees]})
        p = plan[a]
        # 2026-11-05 修：原来只在开售前 90 秒内拉起，而本脚本约 60 秒一轮、单轮最长 110 秒（flock -n 忙就跳过），
        # 11-04 四个盘（0x5843/0x539b/0xac79/0xa178）全部错过窗口被标 expired、一笔没领。
        # 改为提前 15 分钟拉起（fire 自己睡到开售前 1 秒）；错过开售 6 小时内仍补拉一次（免费份常常还在）。
        if p['start'] < time.time() - LATE_FIRE:
            p['fired'] = 'expired'; continue
        if p['start'] - time.time() <= EARLY_SPAWN:
            p['fired'] = now_cst(); p['spawned_at'] = time.time()
            arg = json.dumps({'start': p['start'], 'frees': p['frees']})
            log = open(os.path.join(D, 'discover_out/salearm.log'), 'a')
            subprocess.Popen(['python3', 'salearm.py', 'fire', a, arg], cwd=D, start_new_session=True,
                             stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
            ev({'contract': a, 'stage': 'spawned', 'start_cst': p['start_cst']})
        save_plan(plan)
    save_plan(plan)


if __name__ == '__main__':
    main()
