#!/usr/bin/env python3
"""付费 mint 前的「开池价 vs 成本」闸（2026-11-24 用户授权）。

背景：AGENTMINT（playagentcards）实测——5,000 份 27 分钟售罄，开池价只有成本的 0.44 倍，
六号打满 0.1035 BNB，卖出只回来 16.76 USDT。这类「押金/配池型」付费 mint 的结构性问题是：
金库先切 20%、邀请再切 5%，池子里 BNB 少于代币配比，开盘必然低于成本。

规则：开池价（二级池子现价）低于成本的 1.1 倍 → 不自动付费 mint。
  1) 池子已存在且有储备 → 用真实池价 / 成本单价 算倍率，< 1.1 拒绝
  2) 池子还没建（纯打新、无二级）→ 默认拒绝（PREBUY_ALLOW_PREOPEN=1 可放开）
  3) 成本读不出来 → 拒绝（fail-closed，钱的事不猜）

成本单价来源：优先入参 cost_bnb（每颗代币成本），否则用 mint 合约的 quote(1) 读 (cost, tokens)。
命令行：python3 prebuy_guard.py <mint合约> [--cost-bnb 每颗成本]
"""
import json
import os
import sys
import urllib.request

MIN_RATIO = float(os.environ.get('PREBUY_MIN_RATIO', '1.1'))
ALLOW_PREOPEN = os.environ.get('PREBUY_ALLOW_PREOPEN', '0') == '1'
RPCS = ['https://bsc-dataseed.binance.org', 'https://bsc-dataseed1.defibit.io', 'https://rpc.ankr.com/bsc']
WBNB = '0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c'


def _sel(sig):
    from Crypto.Hash import keccak
    k = keccak.new(digest_bits=256); k.update(sig.encode()); return k.hexdigest()[:8]


def _rpc(method, params):
    last = None
    for url in RPCS:
        for _ in range(2):
            try:
                req = urllib.request.Request(url, data=json.dumps(
                    {'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}).encode(),
                    headers={'Content-Type': 'application/json'})
                r = json.load(urllib.request.urlopen(req, timeout=15)).get('result')
                if r is not None: return r
            except Exception as e:
                last = e
    return None


def _call(to, data):
    return _rpc('eth_call', [{'to': to, 'data': data}, 'latest'])


def _addr_ret(to, sig):
    r = _call(to, '0x' + _sel(sig))
    if not r or r == '0x' or int(r, 16) == 0: return None
    return '0x' + r[-40:]


def _uint_ret(to, sig):
    r = _call(to, '0x' + _sel(sig))
    return int(r, 16) if r and r != '0x' else None


def _pair_price(pair):
    """返回 (池价 BNB/代币, 代币储备, BNB储备)；读不到返回 (None, 0, 0)。"""
    t0 = _addr_ret(pair, 'token0()')
    res = _call(pair, '0x' + _sel('getReserves()'))
    if not t0 or not res or res == '0x': return None, 0.0, 0.0
    w = [int(res[2 + 64 * i:66 + 64 * i], 16) for i in range(3)]
    tok, bnb = (w[0], w[1]) if t0.lower() != WBNB else (w[1], w[0])
    if tok == 0 or bnb == 0: return None, tok / 1e18, bnb / 1e18
    return (bnb / tok), tok / 1e18, bnb / 1e18


def cost_per_token_bnb(mint, token=None):
    """从 mint 合约读一份的成本与代币数；优先 quote(1)。"""
    r = _call(mint, '0x' + _sel('quote(uint256)') + hex(1)[2:].rjust(64, '0'))
    if r and r != '0x' and len(r) >= 2 + 128:
        cost = int(r[2:66], 16); tokens = int(r[66:130], 16)
        if cost and tokens: return cost / tokens, 'quote'   # 两边都是 wei，直接相除得 BNB/颗
    r = _call(mint, '0x' + _sel('costOf(uint256)') + hex(1)[2:].rjust(64, '0'))
    if r and r != '0x' and token:
        cost = int(r, 16)
        dec = _uint_ret(token, 'decimals()') or 18
        per = _uint_ret(mint, 'tokensPerShare()') or _uint_ret(mint, 'poolTokensPerFillShare()')
        if cost and per: return (cost / 1e18) / (per / 10 ** dec), 'costOf+perShare'
    return None, 'unknown'


def guard(mint, token=None, cost_bnb=None):
    """返回 (ok, reason)。ok=False 时调用方必须跳过这笔付费 mint。"""
    token = token or _addr_ret(mint, 'token()') or _addr_ret(mint, 'agc()')
    pair = _addr_ret(mint, 'pair()')
    cost = cost_bnb or cost_per_token_bnb(mint, token)[0]
    if not cost:
        return False, 'cost_unknown：读不出单颗成本，钱的事不猜（fail-closed）'
    if not pair:
        if ALLOW_PREOPEN:
            return True, 'preopen_allowed(无池子，成本 %.3e BNB/颗)' % cost
        return False, 'no_pool：还没有二级池子，开池价无法验证 ≥ %.1f×成本 → 不打新' % MIN_RATIO
    px, tok_res, bnb_res = _pair_price(pair)
    if not px:
        if ALLOW_PREOPEN:
            return True, 'preopen_allowed(池子无储备)'
        return False, 'pool_empty：池子没储备，开池价无法验证'
    ratio = px / cost
    msg = '池价 %.3e / 成本 %.3e = %.2f×（阈值 %.1f×，池子 %.0f 枚 / %.4f BNB）' % (px, cost, ratio, MIN_RATIO, tok_res, bnb_res)
    return (ratio >= MIN_RATIO), msg


def main():
    if len(sys.argv) < 2:
        sys.exit('usage: prebuy_guard.py <mint合约> [--token 0x..] [--cost-bnb 值]')
    mint = sys.argv[1]
    token = None; cost = None
    if '--token' in sys.argv: token = sys.argv[sys.argv.index('--token') + 1]
    if '--cost-bnb' in sys.argv: cost = float(sys.argv[sys.argv.index('--cost-bnb') + 1])
    ok, why = guard(mint, token, cost)
    print(json.dumps({'mint': mint, 'allow': ok, 'reason': why, 'min_ratio': MIN_RATIO}, ensure_ascii=False))


if __name__ == '__main__':
    main()