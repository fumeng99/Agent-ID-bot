import sys as _s; _s.path.append('$AIDBOT_HOME/pylib')
# 70% 自动打满（2026-10-31 20:21 用户「到60%提醒 进入监控 到70%自动打满」，20:21「可以」= 每天最多 100U，20:22「不够记得提醒我补」）
# 流程：巡逻在开池进度 ≥60% 时做完整审计，通过的写进 fairmint/audit_ok.json {合约: {"ok": true, "token": ..., "note": ..., "time": ...}}
# 本脚本只买 audit_ok 里 ok=true、未开池、开池进度 ≥70%、源码已验证的；五号各打满剩余额度（fair_buy.py 精确授权 + preview）。
# 每天预算：每个号 100U（2026-11-01 03:40 用户原话「改每个号」，含该号当天 30U 规则已花的），某号超了只跳过该号并提醒。
# 2026-11-01 02:45 用户原话「以后自动打满时 BNB 不够，也要自动用 U 换吗？ 要」：BNB 计价的盘打满前，BNB 不够的号自动用 USDT 换够（按本盘满额 + 0.0045 BNB 余量），换不够才提醒补。
# usage: python3 autofill.py [--dry]
import sys, os, json, time, subprocess
D = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, D)
from rpcpool import batch
DRY = '--dry' in sys.argv; CAP = 100.0
FM = json.load(open(f'{D}/discover_out/fairmint.json')); FOUND = json.load(open(f'{D}/fairmint/found.json'))
AOK = json.load(open(f'{D}/fairmint/audit_ok.json')) if os.path.exists(f'{D}/fairmint/audit_ok.json') else {}
LED = f'{D}/autofill.json'; led = json.load(open(LED)) if os.path.exists(LED) else {}
day = time.strftime('%Y-%m-%d', time.gmtime(time.time() + 8 * 3600))
ab = json.load(open(f'{D}/auto_buy.json')) if os.path.exists(f'{D}/auto_buy.json') else {}
spent = (ab.get(day) or {}).get('spent_u', 0) + (led.get(day) or {}).get('spent_u', 0)
LABELS = [x['label'] for x in json.load(open('$AIDBOT_HOME/baw/accounts/accounts.json'))]
per_acc = json.loads(subprocess.run(['python3', f'{D}/quota.py'], capture_output=True, text=True).stdout)['rule30_per_account_used_u']
for b in (led.get(day) or {}).get('buys', []):
    for l in (b.get('accounts') or LABELS): per_acc[l] = per_acc.get(l, 0) + b.get('est_u_each', b.get('est_u', 0) / len(LABELS))
U = '0x55d398326f99059ff775485246999027b3197955'; WBNB = '0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c'
R = '0x10ed43c718714eb63d5aa57b78b54704e256024e'
r = batch([('eth_call', [{'to': R, 'data': '0xd06ca61f' + hex(10**18)[2:].rjust(64, '0') + '40'.rjust(64, '0') + '2'.rjust(64, '0') + WBNB[2:].rjust(64, '0') + U[2:].rjust(64, '0')}, 'latest'])])[0]
BNBU = int(r[-64:], 16) / 1e18
out = {'time': day, 'bnb_u': round(BNBU, 1), 'spent_today_u': round(spent, 2), 'cap_u_per_account': CAP, 'bought': [], 'skipped': [], 'remind': []}
def topup_bnb(need_bnb, bnbu):
    """BNB 不够打满的号用 USDT 换 BNB（用户 2026-11-01 02:45 授权）。返回每号结果。"""
    import os
    sys.path.insert(0, D); from rpcpool import batch
    A = json.load(open('$AIDBOT_HOME/baw/accounts/accounts.json')); U = '0x55d398326f99059fF775485246999027B3197955'
    R = batch([c for x in A for c in (('eth_getBalance', [x['bsc'], 'latest']), ('eth_call', [{'to': U, 'data': '0x70a08231' + x['bsc'][2:].lower().rjust(64, '0')}, 'latest']))])
    res = []
    for i, x in enumerate(A):
        bnb = int(R[2*i], 16) / 1e18; usdt = int(R[2*i+1], 16) / 1e18
        if bnb >= need_bnb: continue
        amt = round((need_bnb - bnb) * bnbu * 1.05 + 0.3, 2)
        if usdt < amt: res.append((x['label'], 'usdt_insufficient', round(usdt, 2), amt)); continue
        env = dict(os.environ, HOME=x['home'], npm_config_cache='/tmp/npmcache')
        r = subprocess.run(['$AIDBOT_HOME/baw/node_modules/.bin/baw', 'market-order', 'swap', '--binanceChainId', '56', '--fromTokenQty', str(amt), '--fromToken', U, '--toToken', '0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee', '--json'], env=env, capture_output=True, text=True, timeout=120)
        res.append((x['label'], 'swap', amt, r.stdout.strip()[-160:]))
    if any(t[1] == 'swap' for t in res): time.sleep(20)
    return res
for row in FM.get('watch_unopened', []):
    a = row['contract'].lower(); v = FOUND.get(a, {})
    if row['open_pct'] < 70 or v.get('opened'): continue
    if row.get('autofill_supported') is False: out['skipped'].append((row['name'], a, row['open_pct'], f"{row.get('template')} 模板，fair_buy.py 不支持，只提醒不自动打满")); continue  # 2026-11-01 审计：份数固定 10（与 100U 预算一致，原 999 可能超额）；fairmint 新识别的 JAGENT/Boss/Mao 模板不进自动买
    if row.get('reserve_missing'): out['skipped'].append((row['name'], a, row['open_pct'], f"募资 {row.get('total_paid_bnb')} BNB 没留在合约做开池储备（pendingBnb=0），资金已流出，审计不过")); continue
    if row.get('progress_suspect') or row['open_pct'] > 100: out['skipped'].append((row['name'], a, row['open_pct'], '进度超过 100% 仍未开池，开池份数读错，不可信，不自动打满')); continue
    if row.get('open_pct_guess'): out['skipped'].append((row['name'], a, row['open_pct'], '开池份数是自动猜的总量函数，只提醒不自动打满')); continue  # 2026-11-01 风控：猜的进度不触发自动买
    if (led.get('filled') or {}).get(a): continue
    # 2026-11-01 16:40 用户「按照你推荐来 学习优化」，学  v2.6.0/v2.5.1：
    # ① 可升级合约（fair mint 或代币）不打满——保本/储备逻辑可被事后换掉；读失败也不打。
    sys.path.insert(0, D); import codeutil as CU
    from rpcpool import rpc as _rpc
    _tok = (AOK.get(a) or {}).get('token') or v.get('token')
    upg = [(x,) + CU.upgradeable(_rpc, x) for x in [a, _tok] if x]
    bad = [f"{x[0][:6]}…{x[0][-4:]} {x[2] or '读取失败'}" for x in upg if x[1] is not False]
    if bad: out['skipped'].append((row['name'], a, row['open_pct'], '可升级合约或读不到，不自动打满：' + '；'.join(bad))); continue
    # ③ 学  部署者风控：同一部署者 24 小时内在 found.json 里出现 ≥3 个不同名字的盘（批量发盘）→ 不打满
    dep = v.get('deployer')
    if dep:
        import calendar as _cal
        def _t(x):
            try: return _cal.timegm(time.strptime(x.get('first_seen_cst'), '%Y-%m-%d %H:%M')) - 8 * 3600
            except Exception: return 0
        sib = {x.get('name') for x in FOUND.values() if x.get('deployer') == dep and time.time() - _t(x) < 86400}
        if len(sib) >= 3: out['skipped'].append((row['name'], a, row['open_pct'], f'部署者 {dep[:6]}…{dep[-4:]} 24 小时内发了 {len(sib)} 个不同名字的盘，疑似批量发盘，不自动打满')); continue
    # ② 保本：合约有退款函数，或者链上实测资金真的留在开池储备（pendingBnb ≥ 已募资 75%，即国库抽成 ≤20% 加少量误差）。两者都没有不打满。
    pb, tp = v.get('pending_bnb_bnb'), v.get('total_paid_bnb')
    reserve_ok = bool(pb and tp and pb >= 0.75 * tp)
    if not v.get('has_refund') and not reserve_ok:
        out['skipped'].append((row['name'], a, row['open_pct'], f"没有退款函数，且开池储备未实测达标（pendingBnb={pb}，已募资={tp} BNB），不自动打满")); continue
    ok = AOK.get(a)
    if not (ok and ok.get('ok')): out['skipped'].append((row['name'], a, row['open_pct'], '未通过/未做 60% 审计')); continue
    vs = json.loads(subprocess.run(['python3', f'{D}/verify_source.py', a], capture_output=True, text=True, timeout=60).stdout.splitlines()[0])
    if not vs.get('verified'): out['skipped'].append((row['name'], a, '源码未验证')); continue
    tu = v.get('cost_1share_token_units'); per_u = tu if tu else (v.get('cost_1share_bnb') or 0) * BNBU
    NACC = len(json.load(open('$AIDBOT_HOME/baw/accounts/accounts.json')))
    each = per_u * 10  # 基础额度 10 份/号（fair_buy 按实际剩余额度买）
    accs = [l for l in LABELS if per_acc.get(l, 0) + each <= CAP]
    over = [l for l in LABELS if l not in accs]
    if over: out['remind'].append(f"{row['name']} 已到 {row['open_pct']}%，每号打满约 {each:.0f}U，{'/'.join(over)} 今天会超 100U/号，未买")
    if not accs: continue
    est = each * len(accs)
    if not DRY and not tu and v.get('cost_1share_bnb'):
        out.setdefault('swaps', []).extend(topup_bnb(v['cost_1share_bnb'] * 10 + 0.0045, BNBU))
    res = subprocess.run(['python3', f'{D}/fair_buy.py', a, ok.get('token') or v.get('token'), '10', '--max', str(round(per_u * 1.1, 4))] + (['--dry'] if DRY else []) + accs,
                         capture_output=True, text=True, timeout=600).stdout
    lines = res.splitlines(); out['bought'].append({'name': row['name'], 'contract': a, 'open_pct': row['open_pct'], 'per_share_u': round(per_u, 3), 'result': lines})
    if any('insufficient' in l or 'low_bnb' in l for l in lines):
        out['remind'].append(f"{row['name']} 有账号余额不够打满，需要补 USDT/BNB：" + '; '.join(l for l in lines if 'insufficient' in l or 'low_bnb' in l))
    if not DRY:
        dl = led.setdefault(day, {'spent_u': 0, 'buys': []}); dl['spent_u'] = round(dl['spent_u'] + est, 2)  # 按满额估算记账（保守）
        dl['buys'].append({'name': row['name'], 'contract': a, 'est_u': round(est, 2), 'accounts': accs, 'est_u_each': round(each, 2), 'time': time.strftime('%H:%M', time.gmtime(time.time() + 8 * 3600))})
        led.setdefault('filled', {})[a] = True; spent += est
        for l in accs: per_acc[l] = per_acc.get(l, 0) + each
        json.dump(led, open(LED, 'w'), ensure_ascii=False, indent=1)
print(json.dumps(out, ensure_ascii=False))
