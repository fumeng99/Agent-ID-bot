#!/usr/bin/env python3
"""付费自动买额度（只读）：python3 quota.py [--project 0x合约 ...]
唯一规则（2026-11-03 03:21 用户「是都要等60%才对 全部改统一规则 适配全部项目」，合并 02:36/02:39）：
  ① 每号每天（CST 自然日，00:00 重置）所有自动付费合计 ≤100U（含 gas）；
  ② 每号每个项目累计 ≤30U（含 gas），跨天也累计（同一合约不论哪天买的都算）。
数据：auto_buy.json 各日 buys（旧的普通付费/夜间买入；有 mint/contract 字段才能按项目归集）+ autofill.json 各日 buys（autofill 唯一入口）。
手动「确认买入」的仓位不计入自动额度（用户当场确认，单独记账）。
推送「▍额度」一律用本脚本 line 字段。旧字段 rule30_* 保留兼容（含义已是「普通付费已用」与「100U 日总额剩余」）。"""
import json, os, sys, datetime
D = os.path.dirname(os.path.abspath(__file__))
DAILY_CAP, PROJECT_CAP = 100.0, 30.0
BNB_U = 700.0  # 只用于老记录没有 u_total 时的估算


def load(n):
    try: return json.load(open(os.path.join(D, n)))
    except Exception: return {}


def today():
    return (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)).strftime('%Y-%m-%d')


def labels():
    return [a['label'] for a in json.load(open('$AIDBOT_HOME/baw/accounts/accounts.json'))]


def _ab_each(b):
    acc = b.get('accounts') or []
    if not acc: return [], 0.0
    each = b['u_total'] / len(acc) if b.get('u_total') else float(b.get('bnb_each', 0)) * BNB_U
    if b.get('gas_bnb_total') and not b.get('u_total_incl_gas'): each += float(b['gas_bnb_total']) * BNB_U / len(acc)
    return acc, each


def _af_each(b, labs):
    if b.get('per_account_u'): return list(b['per_account_u'].items())
    acc = b.get('accounts') or labs
    return [(l, b.get('est_u_each', b.get('est_u', 0) / max(1, len(acc)))) for l in acc]


def daily_used(day=None):
    """{label: U}：当天自动付费（普通 + autofill）合计。返回 (普通部分, 合计)。"""
    day = day or today(); labs = labels()
    a = load('auto_buy.json').get(day, {}); f = load('autofill.json').get(day, {})
    per = {l: 0.0 for l in labs}
    for b in a.get('buys', []):
        acc, each = _ab_each(b)
        for l in acc: per[l] = per.get(l, 0) + each
    tot = dict(per)
    for b in f.get('buys', []):
        for l, u in _af_each(b, labs): tot[l] = tot.get(l, 0) + u
    return per, tot


def project_used(contract=None):
    """{合约: {label: U}}，跨所有日期累计（每号每项目 ≤30U）。给 contract 只返回该合约的 {label: U}。"""
    labs = labels(); out = {}
    ab = load('auto_buy.json'); af = load('autofill.json')
    for day, d in ab.items():
        if not (isinstance(d, dict) and isinstance(d.get('buys'), list)): continue
        for b in d['buys']:
            c = str(b.get('contract') or b.get('mint') or '').lower()
            if not c.startswith('0x'): continue
            acc, each = _ab_each(b)
            for l in acc: out.setdefault(c, {}); out[c][l] = out[c].get(l, 0) + each
    for day, d in af.items():
        if not (isinstance(d, dict) and isinstance(d.get('buys'), list)): continue
        for b in d['buys']:
            c = str(b.get('contract') or '').lower()
            if not c.startswith('0x'): continue
            for l, u in _af_each(b, labs): out.setdefault(c, {}); out[c][l] = out[c].get(l, 0) + u
    if contract is not None: return {k: round(v, 2) for k, v in out.get(contract.lower(), {}).items()}
    return {c: {k: round(v, 2) for k, v in m.items()} for c, m in out.items()}


def budget(contract):
    """{label: 可用 U} = min(100U 日总额剩余, 该项目 30U 累计剩余)，未扣 gas 余量（调用方按 ×0.97 算份数）。"""
    _, tot = daily_used(); pu = project_used(contract)
    return {l: round(max(0.0, min(DAILY_CAP - tot.get(l, 0), PROJECT_CAP - pu.get(l, 0))), 2) for l in labels()}


if __name__ == '__main__':
    per, tot = daily_used(); out = {'day_cst': today(), 'daily_total_cap_u': DAILY_CAP, 'per_project_cap_u': PROJECT_CAP,
                                    'rule30_per_account_used_u': {k: round(v, 2) for k, v in per.items()},
                                    'rule30_per_account_left_u': {k: round(max(0, DAILY_CAP - tot.get(k, 0)), 2) for k in per},
                                    'rule30_total_used_u': round(float(load('auto_buy.json').get(today(), {}).get('spent_u', 0)), 2),
                                    'fill_per_account_used_u': {k: round(v, 2) for k, v in tot.items()},
                                    'fill_per_account_left_u': {k: round(max(0, DAILY_CAP - v), 2) for k, v in tot.items()},
                                    'daily_used_u': {k: round(v, 2) for k, v in tot.items()},
                                    'daily_left_u': {k: round(max(0, DAILY_CAP - v), 2) for k, v in tot.items()}}
    args = [x.lower() for x in sys.argv[1:] if x.startswith('0x')]
    if '--project' in sys.argv and args:
        out['project'] = {c: {'used_u': project_used(c), 'budget_u': budget(c)} for c in args}
    else:
        out['per_project_used_u'] = project_used()
    out['line'] = '自动付费今日 ' + ' · '.join(f'{k} {v:.2f}' for k, v in tot.items()) + f'（每号每天 ≤{DAILY_CAP:.0f}U，每号每项目累计 ≤{PROJECT_CAP:.0f}U）'
    print(json.dumps(out, ensure_ascii=False))
