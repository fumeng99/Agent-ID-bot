#!/usr/bin/env python3
"""学  v2.6.x（AG_MIN）：70% 打满前，确认真的有很多 Agent ID 钱包在打，防项目方自己灌进度。
数 fair mint 合约 Minted(address indexed minter,...) 事件里的不同铸造地址（增量缓存），再抽 24 个查是否持有 .ai 身份，
ai_minters = 不同铸造地址数 × .ai 比例。读不到返回 None（调用方一律当作不达标）。
用法：python3 minters.py <fair mint 合约>
"""
import os, sys, json, time, random
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fairmint as F
D = os.path.dirname(os.path.abspath(__file__)); CACHE = os.path.join(D, 'minters_cache.json'); STEP = 10000
REG = '0x' + F.REG


def ai_minters(c, deploy_block=None, budget=40):
    c = c.lower(); F.DEADLINE = max(getattr(F, 'DEADLINE', 0), time.time() + budget + 10)
    cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
    e = cache.get(c) or {'m': [], 'to': (deploy_block or 0) - 1, 'ai': {}}
    m = set(e['m']); head = int(F.rpc('eth_blockNumber', []), 16); t0 = time.time()
    if e['to'] < 0:
        _f = (json.load(open(os.path.join(D, 'found.json'))).get(c) or {})
        dep = _f.get('deploy_block') or ((_f.get('first_block_seen') or 0) - 5000 or None)  # 2026-11-01 修：found.json 没有 deploy_block 字段时，用首次发现区块往前 5000 块兜底，否则门槛永远读不到
        if not dep: return None
        e['to'] = dep - 1
    f = e['to'] + 1
    while f <= head:
        if time.time() - t0 > budget: return None
        t = min(f + STEP - 1, head)
        d = F._post(F.QN, {'jsonrpc': '2.0', 'id': 1, 'method': 'eth_getLogs', 'params': [{'address': c, 'topics': [F.TOPICS], 'fromBlock': hex(f), 'toBlock': hex(t)}]}, 30)
        if 'result' not in d: return None
        for l in d['result']:
            if len(l['topics']) > 1: m.add('0x' + l['topics'][1][-40:])
        e['to'] = t; f = t + 1
    # 抽样查 .ai 身份（查过的缓存）
    todo = [x for x in random.sample(sorted(m), min(24, len(m))) if x not in e['ai']]
    if todo:
        r = F.bat([('eth_call', [{'to': REG, 'data': '0x70a08231' + x[2:].rjust(64, '0')}, 'latest']) for x in todo])
        for x, b in zip(todo, r):
            if isinstance(b, str) and len(b) >= 66: e['ai'][x] = int(b[:66], 16) > 0
    smp = [v for k, v in e['ai'].items() if k in m]
    e['m'] = sorted(m); cache[c] = e; json.dump(cache, open(CACHE, 'w'))
    if len(smp) < min(10, len(m)): return None
    ratio = sum(smp) / len(smp) if smp else 0
    return {'minters': len(m), 'ai_ratio': round(ratio, 2), 'ai_minters': int(len(m) * ratio)}


if __name__ == '__main__':
    print(json.dumps(ai_minters(sys.argv[1]), ensure_ascii=False))
