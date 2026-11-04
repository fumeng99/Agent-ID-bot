#!/usr/bin/env python3
"""「前 N 份免费、之后付费」混合盘的免费那份（2026-11-02 15:11 用户「前面免费 后面付费的也不打」后加）。
起因：BNBbaby 0xbb7e…2637 每个 Agent ID 号免费 1 份、之后 1 USDT/份；mint(uint256 paid) 里 paid=0 只领免费份。
旧巡逻用 mint(1) 模拟 → 要扣 USDT → revert → 整盘当付费跳过，免费份也没领。

做法（只走免费路径，闸门不放宽）：
  1. 目标合约：命令行给的地址；或 --scan：seen.json 近 N 小时（默认 48）标成付费/revert/跳过的免费候选 + precheck.json 的 paid/revert + fairmint/found.json。
  2. 规则 B：verify_source.py 已验证（代理须实现合约也验证）才看。Flap 合约一律不碰。
  3. 用字节码里存在的选择器拼「免费份」写法：数量参数一律填 0（mint(0)/claim(0)/buy(0)/mint(0,自己编号,2^256-1)…），
     或明确的免费函数（claimFree()/freeMint()/mintFree()/claimFreeShare()…）。每号 value=0 eth_call，成功才算候选。
     绝不试 mint(1) 这类带付费数量的写法（那属于付费，按付费规则只提醒）。
  4. --mint：候选交给 mint_many.py 逐号 preview，必须 simulationCode 000000000、无风险、无 allowance/authority 变化、
     余额变化全为正且非空（即不扣 USDT/BNB、不 transferFrom 我们的币）才 execute；任何一项不满足就不发。
  5. 交易结果按 txHash 核对（confirm.py），「未确认」的记 pending，绝不重发；同一号同一盘 preview 不干净 2 次后停。
     合约允许多份免费时，下一轮 eth_call 仍成功就再领一份（每轮每号最多 1 笔），领完 eth_call 自然 revert。
输出 JSON：candidates（可领的号与 calldata）、minted、skipped（原因）。
用法：python3 mixedfree.py [--scan [--hours 48]] [--from-out] [--mint] [addr ...]
  discover_all.sh 每轮只读跑 --scan → discover_out/mixedfree.json；巡逻第 5 步跑 --from-out --mint 领取。
"""
import json, os, sys, time, subprocess
D = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, D)
from rpcpool import batch, rpc, call_strict
from codeutil import sel, full_code
from guard import agent_id, FLAP_BLOCK
from confirm import confirm_tx

ACC = json.load(open('$AIDBOT_HOME/baw/accounts/accounts.json'))
STF = os.path.join(D, 'mixedfree_state.json')
MINT = '--mint' in sys.argv; SCAN = '--scan' in sys.argv
HOURS = float(sys.argv[sys.argv.index('--hours') + 1]) if '--hours' in sys.argv else 48
W = lambda x: hex(x)[2:].rjust(64, '0'); MAXU = 'f' * 64; Z = W(0)


def load(p, d):
    try: return json.load(open(p))
    except Exception: return d


# 免费份写法：(名称, 选择器签名, 参数构造)。数量位一律 0。
def forms(a):
    aid = agent_id(a['agentId']); me = a['bsc'][2:].lower().rjust(64, '0')
    return [
        ('mint(0)', 'mint(uint256)', Z), ('claim(0)', 'claim(uint256)', Z), ('buy(0)', 'buy(uint256)', Z),
        ('mint(0,aid,ref)', 'mint(uint256,uint256,uint256)', Z + aid + MAXU), ('mint(0,aid)', 'mint(uint256,uint256)', Z + aid),
        ('mint(me,0)', 'mint(address,uint256)', me + Z), ('mintTo(me,0)', 'mintTo(address,uint256)', me + Z),
        ('claimFree()', 'claimFree()', ''), ('freeMint()', 'freeMint()', ''), ('mintFree()', 'mintFree()', ''),
        ('freeClaim()', 'freeClaim()', ''), ('claimFreeShare()', 'claimFreeShare()', ''), ('mintFreeShare()', 'mintFreeShare()', ''),
        ('claimFreeMint()', 'claimFreeMint()', ''), ('freeMint(1)', 'freeMint(uint256)', W(1)), ('mintFree(1)', 'mintFree(uint256)', W(1)),
        ('claimFree(aid)', 'claimFree(uint256)', aid),
    ]
FREE_VIEWS = ['freeAvailable(address)', 'freeClaimed(address)', 'freeRemaining(address)', 'freeLeft(address)', 'FREE_PER_WALLET()',
              'FREE_SHARES()', 'freeMinted()', 'freeMintsLeft(address)', 'hasClaimedFree(address)']


def targets_from_scan():
    out = {}
    cut = time.time() - HOURS * 3600
    for x in load(os.path.join(D, 'seen.json'), []):
        if not isinstance(x, dict) or not x.get('contract') or x.get('skip_permanent'): continue
        st = str(x.get('status', '')); t = str(x.get('time', ''))[:16]
        try: ts = time.mktime(time.strptime(t, '%Y-%m-%d %H:%M')) - 8 * 3600 - time.timezone
        except Exception: continue
        if ts < cut: continue
        if any(k in st for k in ('付费', 'revert', 'Payment', '跳过', '待查', '售罄')) and '已铸' not in st and '已领' not in st:
            out.setdefault(x['contract'].lower(), x.get('name') or '未命名')
    for r in load(os.path.join(D, 'discover_out/precheck.json'), []):
        if isinstance(r, dict) and r.get('status') in ('paid', 'revert') and r.get('target'): out.setdefault(r['target'].lower(), r.get('name') or '未命名')
    fd = load(os.path.join(D, 'fairmint/found.json'), {})
    for c, v in (fd.items() if isinstance(fd, dict) else []):  # 未开池、近 HOURS 小时发现的 fair mint
        v = v if isinstance(v, dict) else {}
        if v.get('opened'): continue
        try: ts = time.mktime(time.strptime(str(v.get('first_seen_cst', ''))[:16], '%Y-%m-%d %H:%M')) - 8 * 3600 - time.timezone
        except Exception: continue
        if ts >= cut: out.setdefault(str(c).lower(), v.get('name') or v.get('symbol'))
    return out


def main():
    import fcntl
    _lk = open(os.path.join(D, 'mixedfree.lock'), 'w')
    try: fcntl.flock(_lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError: print(json.dumps({'locked': True, 'note': '另一份 mixedfree 在跑，本次跳过'})); return
    tg = {a.lower(): None for a in sys.argv[1:] if a.startswith('0x') and len(a) == 42}
    if '--from-out' in sys.argv:  # 巡逻：只处理 discover_all 只读扫描（discover_out/mixedfree.json）找到的候选
        for x in load(os.path.join(D, 'discover_out/mixedfree.json'), {}).get('candidates', []): tg.setdefault(x['contract'].lower(), x.get('name'))
    if SCAN: tg.update({k: v for k, v in targets_from_scan().items() if k not in tg})
    tg = {k: v for k, v in tg.items() if k not in FLAP_BLOCK}
    state = load(STF, {}); res = {'targets': len(tg), 'candidates': [], 'minted': [], 'skipped': [], 'pending': []}
    # 先核对上轮未确认的交易（只按 txHash 查，不重发）
    for k, v in list(state.items()):
        if k != '_checked' and v.get('pending_tx'):
            c, lab = k.split('|'); a = next((x for x in ACC if x['label'] == lab), None)
            r = confirm_tx(v['pending_tx'], c, a['bsc'], None, timeout=8) if a else {'state': '账号不在'}
            if r['state'].startswith('未确认'): res['pending'].append({'contract': c, 'acct': lab, 'tx': v['pending_tx']}); continue
            v['last_result'] = r['state']; v.pop('pending_tx')
    # 规则 B：verify_cache 里已验证的（永久）/ 30 分钟内未验证的直接用；其余按预算查。每轮有时间预算，按上次检查时间轮转，跑不完下轮接着查。
    T0 = time.time(); BUDGET = float(os.environ.get('MF_BUDGET', '45' if MINT else '95'))
    chk = state.setdefault('_checked', {})
    addrs = sorted(tg, key=lambda a: (0 if a in [x.lower() for x in sys.argv[1:]] else 1, chk.get(a, 0)))
    # 先读合约码（便宜），字节码里没有任何免费份写法选择器的直接跳过，不去查源码验证（省 OKLink/Sourcify 限流额度）
    def call(to, data):
        st, r = call_strict(to, data)
        return r if st == 'ok' else None
    ALLSEL = {sel(sig) for _, sig, _ in forms(ACC[0])}
    codes = {}
    try: raw = dict(zip(addrs, batch([('eth_getCode', [c, 'latest']) for c in addrs], tolerant=True, chunk=40)))
    except Exception: raw = {}
    keep = []
    for c in addrs:
        code = (raw.get(c) or '').lower()
        if not code: res.setdefault('not_checked', []).append(c); continue
        if len(code) < 4000 or not any(x in code for x in ALLSEL):  # 小合约可能是代理，追实现
            if time.time() - T0 < BUDGET * 0.3:
                try: code = full_code(rpc, call, c, code)
                except Exception: pass
        codes[c] = code
        if len(code) < 10: res['skipped'].append({'contract': c, 'name': tg[c], 'why': '无合约码'}); chk[c] = time.time(); continue
        if not any(x in code for x in ALLSEL): res['skipped'].append({'contract': c, 'name': tg[c], 'why': '无免费份写法'}); chk[c] = time.time(); continue
        keep.append(c)
    addrs = keep
    vc = load(os.path.join(D, 'verify_cache.json'), {}); vs = {}
    for a in addrs:
        e = vc.get(a)
        if e and e.get('verified') is not None and (e.get('verified') is True or time.time() - e.get('t', 0) < 1800): vs[a] = e
    todo = []  # 2026-11-02：改为 eth_call 能领再查验证（见下），不预先批量查
    for i in range(0, len(todo), 15):
        if time.time() - T0 > BUDGET * 0.4: break
        try:
            r = subprocess.run(['python3', os.path.join(D, 'verify_source.py')] + todo[i:i + 15], capture_output=True, text=True, timeout=max(10, BUDGET * 0.4 - (time.time() - T0)))
            for ln in r.stdout.splitlines():
                try: j = json.loads(ln); vs[j['addr'].lower()] = j
                except Exception: pass
        except subprocess.TimeoutExpired: break
    for c in addrs:
        if time.time() - T0 > BUDGET: res.setdefault('not_checked', []).append(c); continue
        code = codes[c]
        has_free_view = [f for f in FREE_VIEWS if sel(f) in code]
        calls, meta = [], []
        for a in ACC:
            if (state.get(f"{c}|{a['label']}") or {}).get('pending_tx') or state.get(f"{c}|{a['label']}", {}).get('unclean', 0) >= 2: continue
            for n, sig, args in forms(a):
                s = sel(sig)
                if s not in code: continue
                calls.append(('eth_call', [{'from': a['bsc'], 'to': c, 'data': '0x' + s + args, 'value': '0x0'}, 'latest'])); meta.append((a['label'], n, '0x' + s + args))
        if not calls: res['skipped'].append({'contract': c, 'name': tg[c], 'why': '各号已停（有待核对交易或 preview 不干净 ≥2 次）'}); chk[c] = time.time(); continue
        out = batch(calls, tolerant=True, chunk=30)
        per = {}
        for (lab, n, d), r in zip(meta, out):
            if r is not None and lab not in per: per[lab] = (n, d)
        chk[c] = time.time()
        if not per: res['skipped'].append({'contract': c, 'name': tg[c], 'why': '免费份写法 eth_call 全 revert（无免费额度/已领/已结束）', 'free_views': has_free_view}); continue
        # 规则 B：能领的才查源码验证（缓存优先）；查询失败不算已检查，下轮再来
        v = vs.get(c)
        if v is None:
            try:
                r = subprocess.run(['python3', os.path.join(D, 'verify_source.py'), c], capture_output=True, text=True, timeout=60)
                v = json.loads(r.stdout.splitlines()[0])
            except Exception: v = {'verified': None}
        if v.get('verified') is None: res.setdefault('not_checked', []).append(c); continue
        if v.get('verified') is not True or v.get('impl_verified', True) is False:
            chk[c] = time.time()
            res['skipped'].append({'contract': c, 'name': tg[c], 'why': ('源码未验证（规则 B）' if v.get('verified') is not True else '代理实现未验证（规则 B）') + '；免费份 eth_call 可领：' + ','.join(sorted(per))}); continue
        res['candidates'].append({'contract': c, 'name': tg[c], 'free_views': has_free_view, 'accounts': {k: v[0] for k, v in per.items()}})
        if not MINT: continue
        # 每个写法分组交给 mint_many（它再做规则 B + preview 干净闸门 + 按 txHash 核对）
        for lab, (n, d) in per.items():
            if time.time() - T0 > float(os.environ.get('MF_MINT_STOP', '50')):  # 不开新的交易，留给下轮（避免外层超时杀进程造成不确定交易）
                res.setdefault('deferred', []).append({'contract': c, 'acct': lab, 'form': n}); continue
            r = subprocess.run(['python3', os.path.join(D, 'mint_many.py'), c, d, lab], capture_output=True, text=True, timeout=200).stdout.strip()
            key = f'{c}|{lab}'; s = state.setdefault(key, {'name': tg[c]})
            line = r.splitlines()[-1] if r else ''
            try: tup = eval(line, {}) if line.startswith('(') else None
            except Exception: tup = None
            item = {'contract': c, 'name': tg[c], 'acct': lab, 'form': n, 'result': line[:300]}
            if tup and len(tup) >= 3 and str(tup[2]).startswith('0x') and len(str(tup[2])) == 66:
                item['tx'] = tup[2]; s['last_tx'] = tup[2]
                if str(tup[1]).startswith('未确认'): s['pending_tx'] = tup[2]
                if tup[1] == '已确认到账': s['minted'] = s.get('minted', 0) + 1
            elif tup and tup[1] in ('unclean', 'preview_fail', 'flap_block'):
                s['unclean'] = s.get('unclean', 0) + 1
            s['t'] = time.strftime('%Y-%m-%d %H:%M')
            res['minted'].append(item)
    json.dump(state, open(STF + '.tmp', 'w'), ensure_ascii=False, indent=1); os.replace(STF + '.tmp', STF)
    res['not_checked'] = len(res.get('not_checked', []))
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
