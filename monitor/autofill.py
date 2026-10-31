import sys as _s; _s.path.append('$AIDBOT_HOME/pylib')
# 70% 自动打满（2026-10-31 20:21 用户「到60%提醒 进入监控 到70%自动打满」，20:21「可以」= 每天最多 100U，20:22「不够记得提醒我补」）
# 流程：巡逻在开池进度 ≥60% 时做完整审计，通过的写进 fairmint/audit_ok.json {合约: {"ok": true, "token": ..., "note": ..., "time": ...}}
# 本脚本只买 audit_ok 里 ok=true、未开池、开池进度 ≥70%、源码已验证的；五号各打满剩余额度（fair_buy.py 精确授权 + preview）。
# 每天预算 100U（含当天 30U 规则已花的，较窄读法），超出或余额不够只提醒，不自动换币。
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
U = '0x55d398326f99059ff775485246999027b3197955'; WBNB = '0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c'
R = '0x10ed43c718714eb63d5aa57b78b54704e256024e'
r = batch([('eth_call', [{'to': R, 'data': '0xd06ca61f' + hex(10**18)[2:].rjust(64, '0') + '40'.rjust(64, '0') + '2'.rjust(64, '0') + WBNB[2:].rjust(64, '0') + U[2:].rjust(64, '0')}, 'latest'])])[0]
BNBU = int(r[-64:], 16) / 1e18
out = {'time': day, 'bnb_u': round(BNBU, 1), 'spent_today_u': round(spent, 2), 'cap_u': CAP, 'bought': [], 'skipped': [], 'remind': []}
for row in FM.get('watch_unopened', []):
    a = row['contract'].lower(); v = FOUND.get(a, {})
    if row['open_pct'] < 70 or v.get('opened'): continue
    if row.get('open_pct_guess'): out['skipped'].append((row['name'], a, row['open_pct'], '开池份数是自动猜的总量函数，只提醒不自动打满')); continue  # 2026-11-01 风控：猜的进度不触发自动买
    if (led.get('filled') or {}).get(a): continue
    ok = AOK.get(a)
    if not (ok and ok.get('ok')): out['skipped'].append((row['name'], a, row['open_pct'], '未通过/未做 60% 审计')); continue
    vs = json.loads(subprocess.run(['python3', f'{D}/verify_source.py', a], capture_output=True, text=True, timeout=60).stdout.splitlines()[0])
    if not vs.get('verified'): out['skipped'].append((row['name'], a, '源码未验证')); continue
    tu = v.get('cost_1share_token_units'); per_u = tu if tu else (v.get('cost_1share_bnb') or 0) * BNBU
    est = per_u * 10 * 5  # 基础额度 10 份 × 五号（fair_buy 按实际剩余额度买）
    if spent + est > CAP:
        out['remind'].append(f"{row['name']} 已到 {row['open_pct']}%，打满约 {est:.0f}U，今天已用 {spent:.1f}U/100U，超额度未买"); continue
    res = subprocess.run(['python3', f'{D}/fair_buy.py', a, ok.get('token') or v.get('token'), '999', '--max', str(round(per_u * 1.1, 4))] + (['--dry'] if DRY else []),
                         capture_output=True, text=True, timeout=600).stdout
    lines = res.splitlines(); out['bought'].append({'name': row['name'], 'contract': a, 'open_pct': row['open_pct'], 'per_share_u': round(per_u, 3), 'result': lines})
    if any('insufficient' in l or 'low_bnb' in l for l in lines):
        out['remind'].append(f"{row['name']} 有账号余额不够打满，需要补 USDT/BNB：" + '; '.join(l for l in lines if 'insufficient' in l or 'low_bnb' in l))
    if not DRY:
        dl = led.setdefault(day, {'spent_u': 0, 'buys': []}); dl['spent_u'] = round(dl['spent_u'] + est, 2)  # 按满额估算记账（保守）
        dl['buys'].append({'name': row['name'], 'contract': a, 'est_u': round(est, 2), 'time': time.strftime('%H:%M', time.gmtime(time.time() + 8 * 3600))})
        led.setdefault('filled', {})[a] = True; spent += est
        json.dump(led, open(LED, 'w'), ensure_ascii=False, indent=1)
print(json.dumps(out, ensure_ascii=False))
