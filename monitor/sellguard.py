#!/usr/bin/env python3
"""卖出前的可卖性预演闸（2026-11-23 实机标定）。

背景：AGC（0x63de…4ba6）报价 0.53 USDT，但真正预演卖出时币安服务端连续两种拒单：
  1) 未授权时：351805 / This token can-t be sold or can only be traded for smaller amounts
  2) 授权后：  slippage is not enough（minOut 压到 0 也一样）
这两条都说明该币在当前路由下兑不出来。旧 agc_sweep.py 只看路由报价 >= 0.3U 就起卖单，
碰到这类币每轮白烧 gas 重试，且把「未授权导致的 revert」误判成「貔貅」。

本模块顺序：
  a) 先查 allowance(token -> router)：不足 → need_approve（不下结论、不缓存）
  b) 再按滑点阶梯 8% → 25% → 40% 各预演一次 swap；任一通过 → sellable
  c) 全部失败且错误命中「can-t be sold / smaller amounts / slippage is not enough」
     → unsellable，写入 discover_out/unsellable.json（默认 7 天有效）
  d) 其他错误（网络、限流、余额不足）→ unknown，不缓存，下轮再试

命令行：python3 sellguard.py <token> [account-label] [--amount WEI]
"""

import json
import os
import re
import subprocess
import sys
import time
import urllib.request

D = os.path.dirname(os.path.abspath(__file__))
ROUTER = '0x10ED43C718714eb63d5aA57B78B54704E256024E'
WBNB = '0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c'
USDT = '0x55d398326f99059fF775485246999027B3197955'
CACHE = os.path.join(D, 'discover_out/unsellable.json')
TTL = int(os.environ.get('SELLGUARD_TTL', str(7 * 86400)))
AIDBOT_HOME = os.environ.get('AIDBOT_HOME') or os.path.expanduser('~/.agent-id-bot')
BAW = os.path.join(AIDBOT_HOME, 'baw/node_modules/.bin/baw')
RPCS = ['https://bsc-rpc.publicnode.com', 'https://bsc-dataseed.binance.org']
SLIPS = [8, 25, 40]

DEAD_PAT = re.compile(r'(can.{0,3}t be sold|can not be sold|smaller amounts|honeypot|slippage is not enough)', re.I)


def _sel(sig):
    from Crypto.Hash import keccak
    k = keccak.new(digest_bits=256)
    k.update(sig.encode())
    return k.hexdigest()[:8]


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


def _call(to, data):
    return _rpc('eth_call', [{'to': to, 'data': data}, 'latest'])


def _allowance(token, owner, spender):
    try:
        r = _call(token, '0xdd62ed3e' + _addr(owner) + _addr(spender))
        return int(r, 16) if r and r != '0x' else 0
    except Exception:
        return None


def _quote_out(token, amount_wei):
    try:
        d = ('0x' + _sel('getAmountsOut(uint256,address[])') + _u(amount_wei) + _u(64) + _u(3)
             + _addr(token) + _addr(WBNB) + _addr(USDT))
        r = _call(ROUTER, d)
        return int(r[-64:], 16) if r and r != '0x' else 0
    except Exception:
        return 0


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


def _accounts():
    try:
        return json.load(open(os.path.join(AIDBOT_HOME, 'baw/accounts/accounts.json')))
    except Exception:
        return []


def _preview_swap(token, amount_wei, min_out, home, sender):
    sig = 'swapExactTokensForTokens(uint256,uint256,address[],address,uint256)'
    data = ('0x' + _sel(sig) + _u(amount_wei) + _u(min_out) + _u(160) + _addr(sender)
            + _u(int(time.time()) + 900) + _u(3) + _addr(token) + _addr(WBNB) + _addr(USDT))
    env = dict(os.environ, HOME=home)
    p = subprocess.run([BAW, 'contract-call', 'preview', '--binanceChainId', '56',
                        '--from', sender, '--to', ROUTER, '--value', '0',
                        '--inputData', data, '--json'],
                       capture_output=True, text=True, env=env, timeout=120)
    out = (p.stdout or '') + (p.stderr or '')
    try:
        return json.loads(out)
    except Exception:
        return {'_raw': out[:400]}


def check(token, amount_wei, account):
    home = account.get('home') or ''
    sender = account.get('bsc') or ''
    if not home or not sender or amount_wei <= 0:
        return 'unknown', '账户信息或数量不全'
    al = _allowance(token, sender, ROUTER)
    if al is None:
        return 'unknown', 'allowance 读取失败'
    if al < amount_wei:
        return 'need_approve', 'allowance %d < %d' % (al, amount_wei)
    q = _quote_out(token, amount_wei)
    if q <= 0:
        return 'unknown', '路由无报价'
    last = ''
    for slip in SLIPS:
        r = _preview_swap(token, amount_wei, int(q * (100 - slip) / 100), home, sender)
        sim = ((r.get('data') or {}).get('simulationResult') or {})
        if r.get('success') is not False and sim.get('simulationCode') == '000000000':
            return 'sellable', '预演通过（滑点 %d%%）' % slip
        last = json.dumps(r.get('error') or r, ensure_ascii=False)
        if not DEAD_PAT.search(last):
            return 'unknown', last[:180]
    mark_unsellable(token, last)
    return 'unsellable', last[:180]


def can_sell(token, amount_wei, account):
    why = unsellable(token)
    if why:
        return False, '缓存判死: ' + why
    st, reason = check(token, amount_wei, account)
    return st == 'sellable', '[%s] %s' % (st, reason)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if not args:
        sys.exit('usage: sellguard.py <token> [account-label] [--amount WEI]')
    token = args[0]
    label = args[1] if len(args) > 1 else None
    amount = 0
    if '--amount' in sys.argv:
        amount = int(sys.argv[sys.argv.index('--amount') + 1])
    accs = [a for a in _accounts() if (label is None or a.get('label') == label)]
    if not accs:
        sys.exit('no account (label=%s)' % label)
    res = [{'label': a.get('label'), 'result': can_sell(token, amount, a)} for a in accs]
    print(json.dumps({'token': token, 'cached_unsellable': unsellable(token), 'results': res},
                     ensure_ascii=False))


if __name__ == '__main__':
    main()