#!/usr/bin/env python3
"""授权巡检（只读）。学 -tools v0.4.1 approvals watch：用 baw approvals list 查每个账号 BSC 上的授权，
和上次记下的比，新出现的授权提醒用户（无限额或币安标了风险 → 重要）。2026-11-06 起：放置超过一轮（约 10 分钟）的授权自动撤销（用户要求授权临时用、用完取消）。
输出 JSON：{first, accounts:{label:{total, new:[...]}}, alerts:[...]}；第一次运行只记底，不提醒。
状态：approvals_state.json（只存授权键，不含密钥）。"""
import json, os, subprocess, sys, time
D = os.path.dirname(os.path.abspath(__file__)); ST = os.path.join(D, 'approvals_state.json')
ACC = json.load(open('$AIDBOT_HOME/baw/accounts/accounts.json'))
UNL = 2 ** 200
def key(a):
    if a.get('id') not in (None, ''): return str(a['id'])
    return f"{str(a.get('tokenContract','')).lower()}:{str(a.get('spender','')).lower()}:{a.get('type') or a.get('approvalType') or ''}"
def unlimited(x):
    s = str(x).strip().lower()
    if s == 'unlimited': return True
    try: return int(s) >= UNL
    except Exception: return False
def risky(a):
    rl = a.get('riskyLevel')
    try: bad = float(rl) > 0
    except Exception: bad = str(rl).lower() not in ('', 'none', 'null', 'low')
    return bad or a.get('noInteractive') is True
def fetch(home):
    env = dict(os.environ, HOME=home, npm_config_cache='/tmp/npmcache')
    r = subprocess.run(['bash', '$AIDBOT_HOME/baw/baw-wrapper.sh', 'approvals', 'list', '--limit', '100', '--json'], capture_output=True, text=True, timeout=40, env=env)
    j = json.loads(r.stdout)
    if not j.get('success'): raise RuntimeError(str(j.get('error'))[:80])
    return j['data'].get('list') or []
state = json.load(open(ST)) if os.path.exists(ST) else {}
out = {'first': not state, 'accounts': {}, 'alerts': [], 'errors': {}}
for a in ACC:
    lab = a.get('label')
    try: L = fetch(a['home'])
    except Exception as e: out['errors'][lab] = str(e)[:80]; continue
    keys = sorted({key(x) for x in L}); prev = state.get(lab)
    new = [] if prev is None else [x for x in L if key(x) not in set(prev)]
    for x in new:
        lv = '重要' if unlimited(x.get('amount')) or risky(x) else '注意'
        out['alerts'].append({'level': lv, 'account': lab, 'token': x.get('tokenSymbol') or x.get('tokenContract'), 'token_contract': x.get('tokenContract'), 'spender': x.get('spender'), 'spender_name': x.get('spenderName'), 'amount': 'unlimited' if unlimited(x.get('amount')) else x.get('amount'), 'risky': x.get('riskyMsg') or x.get('riskyLevel'), 'type': x.get('type')})
    # 2026-11-06 用户原话「全部授权应该是临时用的 用完取消才对」：上一轮已存在、本轮仍在的授权（已放置 ≥1 轮约 10 分钟，交易早已用完）自动撤销
    rev = []
    if prev is not None and os.environ.get('NO_REVOKE') != '1':
        for x in L:
            if key(x) in set(prev):
                env = dict(os.environ, HOME=a['home'], npm_config_cache='/tmp/npmcache')
                try:
                    r = subprocess.run(['bash', '$AIDBOT_HOME/baw/baw-wrapper.sh', 'approvals', 'revoke', '--binanceChainId', '56', '--tokenContract', x.get('tokenContract'), '--spender', x.get('spender'), '--type', x.get('type') or 'approve', '--json'], capture_output=True, text=True, timeout=90, env=env)
                    j = json.loads(r.stdout); ok = j.get('success')
                    rev.append({'token': x.get('tokenSymbol'), 'spender': x.get('spender'), 'ok': ok, 'tx': (j.get('data') or {}).get('txHash'), 'err': None if ok else str(j.get('error'))[:80]})
                except Exception as e:
                    rev.append({'token': x.get('tokenSymbol'), 'spender': x.get('spender'), 'ok': False, 'err': str(e)[:80]})
    if rev: out.setdefault('revoked', {})[lab] = rev
    out['accounts'][lab] = {'total': len(L), 'new': len(new)}
    state[lab] = keys
tmp = ST + '.tmp'; json.dump(state, open(tmp, 'w')); os.replace(tmp, ST)
print(json.dumps(out, ensure_ascii=False))
