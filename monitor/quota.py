#!/usr/bin/env python3
"""今日额度（CST 自然日，00:00 重置）：python3 quota.py。只读。
2026-11-01 03:38 用户原话「这个是单个号的才对」：30U 规则改为每个号每天 30U（单笔仍 ≤5U），按号分别统计。
打满 100U 也改为每号（03:40 用户「改每个号」），含该号 30U 规则已花。推送「▍额度」一律用本脚本 line 字段。"""
import json, os, datetime
D = os.path.dirname(os.path.abspath(__file__))
day = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)).strftime('%Y-%m-%d')
def load(n):
    try: return json.load(open(os.path.join(D, n)))
    except Exception: return {}
labels = [a['label'] for a in json.load(open('$AIDBOT_HOME/baw/accounts/accounts.json'))]
a = load('auto_buy.json').get(day, {}); f = load('autofill.json').get(day, {})
BNB_U = 700.0  # 只用于老记录没有 u_total 时的估算
per = {l: 0.0 for l in labels}
for b in a.get('buys', []):
    acc = b.get('accounts') or []
    if not acc: continue
    each = b['u_total'] / len(acc) if b.get('u_total') else float(b.get('bnb_each', 0)) * BNB_U
    for l in acc: per[l] = per.get(l, 0) + each
s30 = float(a.get('spent_u', 0)); sfill = float(f.get('spent_u', 0))
fill = dict(per)
for b in f.get('buys', []):
    for l in (b.get('accounts') or labels): fill[l] = fill.get(l, 0) + b.get('est_u_each', b.get('est_u', 0) / len(labels))
mx = max(per.values()) if per else 0
print(json.dumps({'day_cst': day, 'rule30_per_account_used_u': {k: round(v, 2) for k, v in per.items()},
                  'rule30_per_account_left_u': {k: round(max(0, 30 - v), 2) for k, v in per.items()},
                  'rule30_total_used_u': round(s30, 2),
                  'fill_per_account_used_u': {k: round(v, 2) for k, v in fill.items()}, 'fill_per_account_left_u': {k: round(max(0, 100 - v), 2) for k, v in fill.items()},
                  'line': '30U/号 已用 ' + ' · '.join(f'{k} {v:.2f}' for k, v in per.items()) + '｜打满 100U/号 最高已用 ' + f'{max(fill.values()):.2f}'}, ensure_ascii=False))
