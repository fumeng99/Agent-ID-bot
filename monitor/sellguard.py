#!/usr/bin/env python3
"""卖出前的可卖性闸（2026-11-23 第二次标定，改成以币安聚合器报价为准）。

第一次实现用 Pancake V2 router 的 swapExactTokensForTokens 做预演，对带税/分档池的代币会误判：
AGC（0x63de…4ba6）在 V2 路由上预演报 can-t-be-sold / slippage-not-enough，但走币安聚合器
（market-order swap）实际成交成功（0.5232 USDT 到账）。所以：

    预演路由 != 实际卖出路由；判死必须以 market-order quote 为准。

判定：
  quote 成功且 toCoinAmount > 0                -> sellable
  quote 失败且命中 can-t-be-sold / smaller / 351805 -> unsellable，写缓存（默认 7 天）
  其他（网络、限流、余额不足）                  -> unknown，不缓存

命令行：python3 sellguard.py <token> [account-label] [--amount TOKENS]
"""

import json
import os
import re
import subprocess
import sys
import time
import urllib.request

D = os.path.dirname(os.path.abspath(__file__))
AIDBOT_HOME = os.environ.get('AIDBOT_HOME') or os.path.expanduser('~/.agent-id-bot')
BAW = os.path.join(AIDBOT_HOME, 'baw/node_modules/.bin/baw')
USDT = '0x55d398326f99059fF775485246999027B3197955'
CACHE = os.path.join(D, 'discover_out/unsellable.json')
TTL = int(os.environ.get('SELLGUARD_TTL', str(7 * 86400)))
RPCS = ['https://bsc-rpc.publicnode.com', 'https://bsc-dataseed.binance.org']

DEAD_PAT = re.compile(r'(can.{0,3}t be sold|can not be sold|smaller amounts|honeypot|351805)', re.I)


def _u(n):
    return hex(int(n))[2:].rjust(64, '0')


def _addr(a):
    return a[2:].lower().rjust(64, '0')


def _rpc(method, params):
    last = None
    for url in RPCS:
        for _ in range(2):
            try:
                req = urllib.request.Request(url, data=json.dumps(
                    {'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}).encode(),
                    headers={'Content-Type': 'application/json'})
                return json.load(urllib.request.urlopen(req, timeout=20)).get('result')
            except Exception as e:
                last = e
                time.sleep(1)
    raise last


def _decimals(token):
    try:
        r = _rpc('eth_call', [{'to': token, 'data': '0x313ce567'}, 'latest'])
        return int(r, 16) if r and r != '0x' else 18
    except Exception:
        return 18


def _load_cache():
    try:
        return json.load(open(CACHE))
    except Exception:
        return {}


def _save_cache(c):
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    tmp = CACHE + '.tmp'
    json.dump(c, open(tmp, 'w'), ensure_ascii=False, indent=1)
    os.replace(tmp, CACHE)


def unsellable(token):
    e = _load_cache().get(token.lower())
    if not e:
        return None
    if time.time() - e.get('at', 0) > TTL:
        return None
    return e.get('reason') or 'unsellable'


def mark_unsellable(token, reason):
    c = _load_cache()
    c[token.lower()] = {'reason': str(reason)[:200], 'at': int(time.time())}
    _save_cache(c)


def clear(token):
    c = _load_cache()
    if token.lower() in c:
        c.pop(token.lower())
        _save_cache(c)


def _accounts():
    try:
        return json.load(open(os.path.join(AIDBOT_HOME, 'baw/accounts/accounts.json')))
    except Exception:
        return []


def _quote(token, amount_tokens, home):
    qty = ('%.6f' % amount_tokens).rstrip('0').rstrip('.') or '0'
    env = dict(os.environ, HOME=home)
    p = subprocess.run([BAW, 'market-order', 'quote', '--binanceChainId', '56',
                        '--fromTokenQty', qty, '--fromToken', token, '--toToken', USDT,
                        '--slippage', 'auto', '--json'],
                       capture_output=True, text=True, env=env, timeout=90)
    out = (p.stdout or '') + (p.stderr or '')
    try:
        return json.loads(out)
    except Exception:
        return {'_raw': out[:300]}


def check(token, amount_wei, account):
    home = account.get('home') or ''
    if not home or amount_wei <= 0:
        return 'unknown', '账户信息或数量不全'
    dec = _decimals(token)
    r = _quote(token, amount_wei / (10 ** dec), home)
    if r.get('success') is False:
        msg = json.dumps(r.get('error') or r, ensure_ascii=False)
        if DEAD_PAT.search(msg):
            mark_unsellable(token, msg)
            return 'unsellable', msg[:180]
        return 'unknown', msg[:180]
    amt = ((r.get('data') or {}).get('toCoinAmount') or '0')
    try:
        out = float(amt)
    except Exception:
        out = 0.0
    if out > 0:
        return 'sellable', '聚合器报价 %.6f USDT' % out
    return 'unknown', '报价为 0：' + str(amt)[:60]


def can_sell(token, amount_wei, account):
    why = unsellable(token)
    if why:
        return False, '缓存判死: ' + why
    st, reason = check(token, amount_wei, account)
    return st == 'sellable', '[%s] %s' % (st, reason)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if '--clear' in sys.argv and args:
        clear(args[0])
        print('cleared', args[0])
        return
    if not args:
        sys.exit('usage: sellguard.py <token> [account-label] [--amount TOKENS] [--clear]')
    token = args[0]
    label = args[1] if len(args) > 1 else None
    amount = 0
    if '--amount' in sys.argv:
        amount = int(float(sys.argv[sys.argv.index('--amount') + 1]) * 10 ** _decimals(token))
    accs = [a for a in _accounts() if (label is None or a.get('label') == label)]
    if not accs:
        sys.exit('no account (label=%s)' % label)
    res = [{'label': a.get('label'), 'result': can_sell(token, amount, a)} for a in accs]
    print(json.dumps({'token': token, 'cached_unsellable': unsellable(token), 'results': res},
                     ensure_ascii=False))


if __name__ == '__main__':
    main()