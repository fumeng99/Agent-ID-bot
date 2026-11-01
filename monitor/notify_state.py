#!/usr/bin/env python3
"""推送去噪（2026-11-01 20:47 用户采纳）：「▍账户」「▍额度」只在比上次发送有变化时写。只读余额/额度，不交易。
  python3 notify_state.py check    → {"account_changed", "quota_changed", "account_line", "quota_line", "diff"}；不改状态
  python3 notify_state.py commit   → 消息真的发出、且写了「▍账户」/「▍额度」后运行，记下本次数值作为「上次发送」
判定：任一账号 BNB 变动 >0.001、账号增减 → account_changed；任一号已用额度变动 ≥0.01 或跨天 → quota_changed。
balances.py 查询失败（line 含「查询失败」）一律当作有变化（要如实写）。
"""
import json, os, subprocess, sys
D = os.path.dirname(os.path.abspath(__file__)); ST = os.path.join(D, 'discover_out', 'notify_last_sent.json'); CUR = '/tmp/notify_state_cur.json'


def run(f):
    try: return json.loads(subprocess.run(['python3', os.path.join(D, f)], capture_output=True, text=True, timeout=60).stdout)
    except Exception as e: return {'line': f'{f} 查询失败（{str(e)[:60]}）', 'error': True}


def check():
    b, q = run('balances.py'), run('quota.py')
    try: last = json.load(open(ST))
    except Exception: last = {}
    la, lq = last.get('accounts') or {}, last.get('quota') or {}
    diff = []
    acc = b.get('accounts') or {}
    a_ch = bool(b.get('error')) or '查询失败' in (b.get('line') or '') or set(acc) != set(la)
    for k, v in acc.items():
        o = la.get(k) or {}
        if abs((v.get('bnb') or 0) - (o.get('bnb') or 0)) > 0.001:
            a_ch = True; diff.append(f"{k} BNB {o.get('bnb')}→{v.get('bnb')}")
    qu = {'day': q.get('day_cst'), 'r30': q.get('rule30_per_account_used_u') or {}, 'fill': q.get('fill_per_account_used_u') or {}}
    q_ch = bool(q.get('error')) or qu['day'] != lq.get('day')
    for key in ('r30', 'fill'):
        for k, v in qu[key].items():
            if abs((v or 0) - ((lq.get(key) or {}).get(k) or 0)) >= 0.01: q_ch = True
    json.dump({'accounts': acc, 'quota': qu, 'b_ok': not b.get('error')}, open(CUR, 'w'), ensure_ascii=False)
    return {'account_changed': a_ch, 'quota_changed': q_ch, 'account_line': b.get('line'), 'quota_line': q.get('line'), 'diff': diff}


if __name__ == '__main__':
    if sys.argv[1:2] == ['commit']:
        try: cur = json.load(open(CUR))
        except Exception: check(); cur = json.load(open(CUR))
        if cur.get('b_ok'): json.dump(cur, open(ST, 'w'), ensure_ascii=False)
        print(json.dumps({'committed': bool(cur.get('b_ok'))}))
    else: print(json.dumps(check(), ensure_ascii=False))
