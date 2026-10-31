#!/usr/bin/env python3
import sys as _s; _s.path.append('$AIDBOT_HOME/pylib')  # 沙箱重置会清掉 pip 包，pycryptodome 装在持久目录
"""Jager 类 Agent ID 分档 fair mint（付费）发现器。
原理：Jager/AGI 克隆合约共用事件 Minted(address,uint256,uint256,uint256,uint256)
topic0=0x19f5f791…，按 topic0 全链 getLogs（不限地址），新出现的合约读 costOf/opened/totalMinted 等。
输出 fairmint/found.json（累积）并打印本轮新发现。只读，不交易。
用法：python3 fairmint.py [--minutes 30] [--hours 48]
"""
import json, os, sys, time, urllib.request
import sys as _s; _s.path.insert(0,'$AIDBOT_DATA/vendor')
from Crypto.Hash import keccak
D = os.path.dirname(os.path.abspath(__file__)); ST = f'{D}/state.json'; OUT = f'{D}/found.json'
TOPICS = ['0x19f5f791ee407773427bf7b970bbbc3375065c32edd1ab142e23a84f94b0719b']  # Minted(address,uint256,uint256,uint256,uint256)
OPENED = '0x1235c671a6b39ea1918628853eceb400b8413f8cdb8ca6110e250065affa20d3'
REG = '60b9262456de011f7d15a54cdd8c48baaa8b022f'
QN = 'https://fluent-lively-diagram.bsc.quiknode.pro/b791d0a1526852ab53f99b98dc50390986bda4b7'  # agentai.meme 前端公开节点，允许无地址 getLogs
RPCS = [QN, 'https://bsc.blockrazor.xyz', 'https://bsc-rpc.publicnode.com', 'https://bsc-dataseed.bnbchain.org', 'https://bsc-dataseed1.defibit.io',
        'https://bsc-dataseed2.ninicoin.io', 'https://1rpc.io/bnb', 'https://bsc.drpc.org']
RANGE = int(os.environ.get('FM_RANGE', '3000'))
def rpc(m, p):
    err = None
    for i in range(3):
        for r in RPCS:
            try:
                q = json.dumps({"jsonrpc": "2.0", "id": 1, "method": m, "params": p}).encode()
                d = json.load(urllib.request.urlopen(urllib.request.Request(r, q, {'content-type': 'application/json', 'User-Agent': 'Mozilla/5.0'}), timeout=15))
                if 'result' in d: return d['result']
                err = d.get('error')
                if m == 'eth_call': return None  # revert：函数不存在，不重试
            except Exception as e: err = e
        time.sleep(1.5 * (i + 1))
    raise RuntimeError(err)
def sel(s): h = keccak.new(digest_bits=256); h.update(s.encode()); return '0x' + h.hexdigest()[:8]
def call(a, data):
    try: return rpc('eth_call', [{'to': a, 'data': data}, 'latest'])
    except Exception: return None
def u(a, sig, arg=''):
    r = call(a, sel(sig) + arg)
    return int(r[:66], 16) if r and len(r) >= 66 else None
def addr(a, sig):
    r = call(a, sel(sig)); return '0x' + r[26:66] if r and len(r) >= 66 else None
def info(a):
    one = '%064x' % 1
    i = {'contract': a, 'cost_1share_bnb': None, 'opened': None}
    q = u(a, 'bnbQuote(uint256)', one); c = u(a, 'costOf(uint256)', one)
    i['cost_1share_bnb'] = (q or c) / 1e18 if (q or c) else None  # bnbQuote 优先；有些克隆用 USD1/FIST 等代币计价，costOf 是代币数量
    if q and c and c != q: i['cost_1share_token_units'] = c / 1e18
    o = u(a, 'opened()'); i['opened'] = bool(o) if o is not None else None
    for k, s in [('total_minted', 'totalMinted()'), ('total_shares', 'TOTAL_SHARES()'), ('total_shares2', 'totalShares()'),
                 ('total_paid_wei', 'totalPaid()'), ('open_at_shares', 'OPEN_AT()'), ('open_bnb_wei', 'OPEN_BNB()'),
                 ('max_per_tx', 'MAX_PER_TX()'), ('paused', 'paused()'), ('renounced', 'renounced()')]:
        v = u(a, s)
        if v is not None: i[k] = v
    i['token'] = addr(a, 'token()'); i['pair'] = addr(a, 'pair()')
    if i['token']:
        for k, sg in (('name', 'name()'), ('symbol', 'symbol()')):
            r = call(i['token'], sel(sg))
            try: i[k] = bytes.fromhex(r[130:130 + 2 * int(r[66:130], 16)]).decode(errors='ignore') if r and len(r) > 130 else None
            except Exception: i[k] = None
    code = rpc('eth_getCode', [a, 'latest']) or ''
    i['agentid_registry_in_code'] = REG in code.lower()
    tm, ts = i.get('total_minted'), i.get('total_shares') or i.get('total_shares2')
    if tm is not None and ts: i['progress_pct'] = round(tm * 100 / ts, 2)
    if i.get('open_at_shares') and tm is not None: i['open_progress_pct'] = round(min(tm * 100 / i['open_at_shares'], 999), 2)
    if i.get('open_bnb_wei') and i.get('total_paid_wei'): i['open_bnb_progress_pct'] = round(i['total_paid_wei'] * 100 / i['open_bnb_wei'], 2)
    for f in ('total_paid_wei', 'open_bnb_wei'):
        if f in i: i[f.replace('_wei', '_bnb')] = i.pop(f) / 1e18
    return i
def main():
    mins = 30; hours = 0
    if '--minutes' in sys.argv: mins = int(sys.argv[sys.argv.index('--minutes') + 1])
    if '--hours' in sys.argv: hours = float(sys.argv[sys.argv.index('--hours') + 1])
    st = json.load(open(ST)) if os.path.exists(ST) else {}
    found = json.load(open(OUT)) if os.path.exists(OUT) else {}
    head = int(rpc('eth_blockNumber', []), 16)
    bt = 0.45
    try:
        b1 = rpc('eth_getBlockByNumber', [hex(head), False]); b0 = rpc('eth_getBlockByNumber', [hex(head - 2000), False])
        bt = max(0.2, (int(b1['timestamp'], 16) - int(b0['timestamp'], 16)) / 2000)
    except Exception: pass
    back = int((hours * 3600 if hours else mins * 60) / bt)
    start = head - back if hours else max(st.get('last', 0) + 1, head - back)
    seen_now, errors = {}, 0
    f = start
    while f <= head:
        t = min(f + RANGE - 1, head)
        try:
            logs = rpc('eth_getLogs', [{'fromBlock': hex(f), 'toBlock': hex(t), 'topics': [TOPICS + [OPENED]]}])
            for l in logs:
                a = l['address'].lower(); e = seen_now.setdefault(a, {'mints': 0, 'opened_evt': False, 'first_block': int(l['blockNumber'], 16)})
                if l['topics'][0] == OPENED: e['opened_evt'] = True
                else: e['mints'] += 1
            f = t + 1; sys.stderr.write(f'logs {t} n={len(seen_now)}\n')
        except Exception:
            errors += 1
            if errors > 8: break
            f = t + 1
    new = []
    for a, e in seen_now.items():
        if a not in found:
            i = info(a); i.update({'first_seen_cst': time.strftime('%Y-%m-%d %H:%M', time.gmtime(time.time() + 8 * 3600)),
                                   'first_block_seen': e['first_block'], 'bscscan': f'https://bscscan.com/address/{a}', 'mints_in_scan': e['mints']})
            found[a] = i; new.append(i)
        else:
            found[a].update({k: v for k, v in info(a).items() if v is not None})
            found[a]['mints_in_scan'] = e['mints']
    # 2026-10-31 20:05 用户要求：未开池（1 万份以内）的付费 fair mint 每轮都盯，不管这轮有没有新 mint
    now = time.time(); prev_open = {a: v.get('opened') for a, v in found.items()}
    stale = [a for a, v in found.items() if not v.get('opened') and a not in seen_now]
    stale = sorted(stale, key=lambda a: -(found[a].get('first_block_seen') or 0))[:15]
    for a in stale:
        try: found[a].update({k: v for k, v in info(a).items() if v is not None})
        except Exception: pass
    watch, near, just_opened = [], [], []
    for a in set(list(seen_now) + stale):
        v = found[a]
        op = max(v.get('open_progress_pct') or 0, v.get('open_bnb_progress_pct') or 0)
        last = v.get('last_open_pct'); d = round(op - last, 2) if last is not None else 0
        v['last_open_pct'] = op
        row = {'contract': a, 'name': v.get('name'), 'cost_1share_bnb': v.get('cost_1share_bnb'), 'open_pct': op, 'delta_pct': d, 'total_minted': v.get('total_minted'), 'open_at_shares': v.get('open_at_shares')}
        if v.get('opened'):
            if prev_open.get(a) is False or (prev_open.get(a) is None and a in seen_now and last is not None): just_opened.append(row)
            continue
        watch.append(row)
        if op >= 60 or d >= 15: near.append(row)  # 60% 按开池份数(如前 1 万份)算，用户 20:06 确认
    st['last'] = head
    json.dump(st, open(ST, 'w')); json.dump(found, open(OUT, 'w'), ensure_ascii=False, indent=1)
    print(json.dumps({'head': head, 'blocks': head - start + 1, 'log_errors': errors, 'active_contracts': len(seen_now),
                      'new': new, 'watch_unopened': sorted(watch, key=lambda r: -r['open_pct']), 'near_open': near, 'just_opened': just_opened, 'active': {a: {k: found[a].get(k) for k in ('cost_1share_bnb', 'opened', 'progress_pct', 'open_progress_pct', 'open_bnb_progress_pct', 'mints_in_scan')} for a in seen_now}},
                     ensure_ascii=False, indent=1))
if __name__ == '__main__': main()
