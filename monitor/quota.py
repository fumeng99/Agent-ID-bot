#!/usr/bin/env python3
"""今日额度（CST 自然日，00:00 重置）：python3 quota.py → 30U 规则已用 / 打满已用。只读。
推送「▍额度」一律用本脚本输出，不要沿用昨天的数字，不要手工加估算。"""
import json, os, datetime
D = os.path.dirname(os.path.abspath(__file__))
day = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)).strftime('%Y-%m-%d')
def load(n):
    try: return json.load(open(os.path.join(D, n)))
    except Exception: return {}
a = load('auto_buy.json').get(day, {}); f = load('autofill.json').get(day, {})
s30 = float(a.get('spent_u', 0)); sfill = float(f.get('spent_u', 0))
print(json.dumps({'day_cst': day, 'rule30_used_u': round(s30, 2), 'rule30_left_u': round(max(0, 30 - s30), 2),
                  'fill_used_u': round(s30 + sfill, 2), 'fill_left_u': round(max(0, 100 - s30 - sfill), 2),
                  'line': f'30U 规则 已用 {s30:.2f}/30｜打满 已用 {s30 + sfill:.2f}/100'}, ensure_ascii=False))
