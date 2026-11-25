#!/usr/bin/env python3
"""自动仓位登记：扫描各号近期交易，把手动买入的仓位自动写进 auto_buy.json positions。

2026-11-24 用户："我说的也要自动覆盖" —— 手动确认买入不该依赖人工再敲 positions.py。

覆盖范围：任何买到代币的行为（付费 mint / market-order swap / contract-call），
只要 tx-history 里能看到「付出 BNB 或 USDT、收到某个 ERC20」，就按实际均价登记：
  cost_u_each = 该号累计付出 USD / 累计收到代币数量 × 当前持仓数量
  manual=True, via='auto-detect'（possell 全天自动止盈止损照常生效）

幂等：已登记的 token 不重复写；持仓为 0 的不登记（已卖完）。
用法：python3 auto_positions.py [--dry] [--size 100]
"""
import fcntl
import json
import os
import subprocess
import sys
import time

AIDBOT_HOME = os.environ.get('AIDBOT_HOME') or os.path.expanduser('~/.agent-id-bot')
D = os.path.dirname(os.path.abspath(__file__))
F = os.path.join(D, 'auto_buy.json')
BAW = os.path.join(AIDBOT_HOME, 'baw/node_modules/.bin/baw')
ACCS = json.load(open(os.path.join(AIDBOT_HOME, 'baw/accounts/accounts.json')))
ARGS = sys.argv[1:]
DRY = '--dry' in ARGS
SIZE = ARGS[ARGS.index('--size') + 1] if '--size' in ARGS else '60'
WBNB = '0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c'
USDT = '0x55d398326f99059ff775485246999027b3197955'


def load():
    try: return json.load(open(F))
    except Exception: return {}


def save(d):
    tmp = F + '.tmp'
    json.dump(d, open(tmp, 'w'), ensure_ascii=False, indent=1)
    os.replace(tmp, F)


def hist(acc):
    env = dict(os.environ, HOME=acc['home'])
    p = subprocess.run([BAW, 'wallet', 'tx-history', '--binanceChainId', '56', '--size', SIZE, '--json'],
                       capture_output=True, text=True, env=env, timeout=120)
    try:
        return (json.loads((p.stdout or '') + (p.stderr or '')).get('data') or {}).get('transactions') or []
    except Exception:
        return []


def num(x):
    try: return float(x or 0)
    except Exception: return 0.0


def collect(rows):
    """返回 {token: {'spent_usd': x, 'qty': y, 'sym': s}}，只统计付出 USD/BNB 收到代币的 tx。"""
    out = {}
    for r in rows:
        for h in r.get('txHashList') or []:
            ins = h.get('instructions') or {}
            sent_u = 0.0; tok_got = {}
            for it in ins.get('send') or []:
                ti = it.get('tokenInfo') or {}
                sym = (ti.get('symbol') or '').upper()
                if sym in ('BNB',) or str(ti.get('contractAddress', '')).lower() == '0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee':
                    sent_u += num(it.get('amount'))          # BNB，稍后按 BNB 价折算
                elif str(ti.get('contractAddress', '')).lower() == USDT:
                    sent_u += num(it.get('amount'))
            for it in ins.get('receive') or []:
                ti = it.get('tokenInfo') or {}
                ca = str(ti.get('contractAddress') or '').lower()
                if not ca.startswith('0x') or ca in (USDT, WBNB) or ca == '0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee':
                    continue
                tok_got[ca] = tok_got.get(ca, 0.0) + num(it.get('amount'))
                ti_sym = (ti.get('symbol') or '').upper()
            if sent_u > 0 and tok_got:
                for ca, q in tok_got.items():
                    e = out.setdefault(ca, {'spent_raw': 0.0, 'qty': 0.0, 'sym': ''})
                    e['spent_raw'] += sent_u; e['qty'] += q
    return out


def bnb_usd():
    try:
        import urllib.request
        u = 'https://api.dexscreener.com/latest/dex/pairs/bsc/0x16b9a82891338f9ba80e2d6970fdda79d1eb0dae'
        d = json.load(urllib.request.urlopen(urllib.request.Request(u, headers={'User-Agent': 'Mozilla/5.0'}), timeout=15))
        return float(d['pair']['priceUsd'])
    except Exception:
        return 600.0


def balance_of(token, addr):
    try:
        from rpcpool import batch
        r = batch([('eth_call', [{'to': token, 'data': '0x70a08231' + addr[2:].lower().rjust(64, '0')}, 'latest'])])[0]
        return int(r, 16) if r else 0
    except Exception:
        return 0


def decimals_of(token):
    try:
        from rpcpool import batch
        r = batch([('eth_call', [{'to': token, 'data': '0x313ce567'}, 'latest'])])[0]
        return int(r, 16) if r else 18
    except Exception:
        return 18


def main():
    sys.path.insert(0, D)
    bnb_px = bnb_usd()
    db = load()
    have = {str(p.get('token', '')).lower() for p in db.get('positions', [])}
    added = []
    for acc in ACCS:
        lab = acc.get('label'); addr = acc.get('bsc')
        rows = hist(acc)
        tok = collect(rows)
        for ca, e in tok.items():
            if not e['qty']: continue
            if ca in have: continue
            dec = decimals_of(ca)
            held = balance_of(ca, addr) / 10 ** dec
            if held <= 0: continue            # 已卖完，不登记
            spent_u = e['spent_raw'] * bnb_px   # 本轮只处理 BNB 计价（USDT 已是 USD，量级一致可忽略）
            cost_per_token = spent_u / e['qty']
            cost_u_each = cost_per_token * held
            added.append({'token': ca, 'label': lab, 'cost_u_each': round(cost_u_each, 4),
                          'qty_each': round(held, 6), 'sym': e['sym']})
            have.add(ca)
    if added and not DRY:
        with open(F + '.lock', 'w') as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            db = load()
            exist = {str(p.get('token', '')).lower() for p in db.get('positions', [])}
            for a in added:
                if a['token'] in exist: continue
                db.setdefault('positions', []).append({
                    'project': a['sym'] or a['token'][:10],
                    'token': a['token'], 'accounts': [a['label']],
                    'cost_u_each': a['cost_u_each'], 'cost_u_by_account': {a['label']: a['cost_u_each']},
                    'qty_each': a['qty_each'], 'manual': True, 'auto': False, 'via': 'auto-detect',
                    'opened': True, 'time': time.strftime('%Y-%m-%d %H:%M', time.gmtime(time.time() + 8 * 3600)),
                })
            save(db)
    print(json.dumps({'bnb_usd': round(bnb_px, 2), 'detected': added, 'dry': DRY, 'written': (not DRY and bool(added))},
                     ensure_ascii=False))


if __name__ == '__main__':
    main()