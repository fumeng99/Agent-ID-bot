#!/usr/bin/env python3
"""仓位登记：把手动确认买入的仓位写进 auto_buy.json positions，交给 possell 统一止盈止损。

2026-11-24 起因：AGENTMINT 是用户口头确认买入的，没进 positions，于是「跌 40% 自动卖」
的规则完全不认识它 —— 开池破发到 0.44 倍也没人卖。

用法：
  python3 positions.py add --project AGENTMINT --token 0x.. --labels 主号,小黑 \
        --cost-u 9.19 [--qty 1000000] [--mint 0x..] [--note 备注]
  python3 positions.py list
"""
import argparse
import fcntl
import json
import os
import sys
import time

D = os.path.dirname(os.path.abspath(__file__))
F = os.path.join(D, 'auto_buy.json')


def load():
    try:
        return json.load(open(F))
    except Exception:
        return {}


def save(d):
    tmp = F + '.tmp'
    json.dump(d, open(tmp, 'w'), ensure_ascii=False, indent=1)
    os.replace(tmp, F)


def cmd_add(a):
    labels = [x.strip() for x in a.labels.split(',') if x.strip()]
    if not labels:
        sys.exit('--labels 不能为空')
    tok = a.token.lower()
    if not tok.startswith('0x') or len(tok) != 42:
        sys.exit('--token 需要是 0x 开头的合约地址')
    with open(F + '.lock', 'w') as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        d = load()
        d['positions'] = [p for p in d.get('positions', [])
                          if str(p.get('token', '')).lower() != tok or not p.get('manual')]
        d.setdefault('positions', []).append({
            'project': a.project,
            'token': tok,
            'mint': (a.mint or '').lower() or None,
            'accounts': labels,
            'cost_u_each': float(a.cost_u),
            'cost_u_by_account': {l: float(a.cost_u) for l in labels},
            'qty_each': float(a.qty) if a.qty else 0,
            'manual': True,
            'auto': False,
            'via': 'manual/positions.py',
            'opened': False,
            'note': a.note or '',
            'time': time.strftime('%Y-%m-%d %H:%M', time.gmtime(time.time() + 8 * 3600)),
        })
        save(d)
    print(json.dumps({'added': 1, 'token': tok, 'labels': labels, 'manual': True},
                     ensure_ascii=False))


def cmd_list(a):
    d = load()
    rows = [{'project': p.get('project'), 'token': p.get('token'), 'labels': p.get('accounts'),
             'cost_u_each': p.get('cost_u_each'), 'manual': bool(p.get('manual')), 'time': p.get('time')}
            for p in d.get('positions', [])]
    print(json.dumps({'positions': rows}, ensure_ascii=False, indent=1))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    ad = sub.add_parser('add')
    ad.add_argument('--project', required=True)
    ad.add_argument('--token', required=True)
    ad.add_argument('--labels', required=True)
    ad.add_argument('--cost-u', required=True)
    ad.add_argument('--qty', default='0')
    ad.add_argument('--mint', default='')
    ad.add_argument('--note', default='')
    ad.set_defaults(fn=cmd_add)
    ls = sub.add_parser('list')
    ls.set_defaults(fn=cmd_list)
    a = ap.parse_args()
    a.fn(a)


if __name__ == '__main__':
    main()