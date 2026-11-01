#!/usr/bin/env python3
"""热度/铸造事件发现（2026-11-01 学  v2.6.5 第 3、5、6 层）。只读：只做 getLogs / eth_call / getCode，不签名、不交易。

为什么：币安无忧、Bitcoin Frogs 都是开池后十几个小时才被我们发现——它们不发 Jager 的 Minted 事件，fairmint.py 按 topic0 扫不到。
做法：
 ① 铸造事件（第 6 层）：全链 getLogs「从零地址转出」的 Transfer（ERC20/721）和 TransferSingle（ERC1155），
    按代币合约统计 30 分钟内不同收币地址数；≥6 个即候选。
 ② 只看新合约：48 小时前就有合约码的老币跳过（LP、借贷凭证、老 NFT 这类）。
 ③ 判定 Agent ID 专属：抽 3 笔铸造交易，取被调用的合约（tx.to）和代币本身，合约码（代理追到底）含 Agent ID 登记合约 0x60B9…022F → registry_in_code；
    否则抽 8 个收币地址查是否持有 .ai 身份（登记合约 balanceOf），≥60% → holders_ai（后端签名型，第 5 层）。
 ④ 热度（第 3 层的近似）：同时记 30 分钟内不同调用者数（tx.from 抽样）与每笔 BNB 金额，≥12 个收币地址的标 hot。
 ⑤ 被调用合约像 fair mint（含 Minted 事件或 costOf）且不在 found.json → 加进 found.json（source=heatscan），
    之后 fairmint.py / fastprogress.py / autofill.py 会照常读进度、审计、按 70% 规则处理。
输出（stdout JSON）：new_agent（本轮新判定为 Agent ID 的项目）、added_fairmint、scanned_blocks、candidates_checked、errors。
状态：fairmint/heat_state.json。
"""
import json, os, sys, time, fcntl
os.environ.setdefault('FM_DEADLINE', '95')
D = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, D); sys.path.insert(0, os.path.dirname(D))
import fairmint as F
import codeutil as CU

ST = os.path.join(D, 'heat_state.json'); FOUND = os.path.join(D, 'found.json')
TRANSFER = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'
UPG_TOPICS = ['0xbc7cd75a20ee27fd9adebab32041f755214dbc6bffa90cc0225b39da2e5c2d3b', '0x1cf3b03a6cf19fa2baba4df148e9dcabedea7f8a5c07840e207e5c089be95d3e']  # Upgraded(address) / BeaconUpgraded(address)
T1155 = '0xc3d58168c5ae7397731d063d5bbf3d657854427343f4c083240f7aacaa2d0f62'
ZERO = '0x' + '0' * 64
WINDOW_S = 30 * 60; MIN_RECIP = 6; HOT_RECIP = 12; CHUNK = 400; MAX_CHECK = 25
REG = '0x' + CU.REG


def now_cst(): return time.strftime('%Y-%m-%d %H:%M', time.gmtime(time.time() + 8 * 3600))


def main():
    st = json.load(open(ST)) if os.path.exists(ST) else {}
    head = int(F.rpc('eth_blockNumber', []), 16)
    bt = 0.45
    try:
        b1 = F.rpc('eth_getBlockByNumber', [hex(head), False]); b0 = F.rpc('eth_getBlockByNumber', [hex(head - 400), False])
        bt = max(0.2, (int(b1['timestamp'], 16) - int(b0['timestamp'], 16)) / 400)
    except Exception: pass
    win_blocks = int(WINDOW_S / bt)
    start = max(st.get('last', 0) + 1, head - win_blocks)
    toks = st.setdefault('tokens', {}); checked = st.setdefault('checked', {}); errors = []
    f = start; scanned_to = start - 1; upg_hits = {}
    while f <= head and time.time() < F.DEADLINE - 45:
        t = min(f + CHUNK - 1, head)
        try:
            logs = F._post(F.QN, {'jsonrpc': '2.0', 'id': 1, 'method': 'eth_getLogs', 'params': [{'fromBlock': hex(f), 'toBlock': hex(t), 'topics': [TRANSFER, ZERO]}]}, 30)
            logs2 = F._post(F.QN, {'jsonrpc': '2.0', 'id': 2, 'method': 'eth_getLogs', 'params': [{'fromBlock': hex(f), 'toBlock': hex(t), 'topics': [T1155, None, ZERO]}]}, 30)
            logs3 = F._post(F.QN, {'jsonrpc': '2.0', 'id': 3, 'method': 'eth_getLogs', 'params': [{'fromBlock': hex(f), 'toBlock': hex(t), 'topics': [UPG_TOPICS]}]}, 30)
            for l in logs3.get('result') or []: upg_hits.setdefault(l['address'].lower(), int(l['blockNumber'], 16))
            if 'result' not in logs or 'result' not in logs2: raise RuntimeError(str(logs.get('error') or logs2.get('error'))[:120])
            for l in logs['result'] + logs2['result']:
                tp = l['topics']; a = l['address'].lower()
                to = tp[2] if tp[0] == TRANSFER and len(tp) >= 3 else (tp[3] if len(tp) >= 4 else None)
                if not to: continue
                e = toks.setdefault(a, {'r': {}, 'tx': [], 'b0': int(l['blockNumber'], 16)})
                e['r']['0x' + to[-40:]] = int(l['blockNumber'], 16)
                if len(e['tx']) < 6 and l['transactionHash'] not in e['tx']: e['tx'].append(l['transactionHash'])
            scanned_to = t; f = t + 1
        except Exception as ex:
            errors.append(f'getLogs {f}-{t}: {str(ex)[:120]}'); break
    st['last'] = scanned_to
    # 只保留 30 分钟窗口
    lo = head - win_blocks
    for a in list(toks):
        r = {k: b for k, b in toks[a]['r'].items() if b >= lo}
        if not r: toks.pop(a); continue
        toks[a]['r'] = r
    for a in [a for a, x in checked.items() if time.time() - x.get('at', 0) > (7 * 86400 if x.get('v') in ('old', 'agent') else 1800)]: checked.pop(a)
    found = json.load(open(FOUND)) if os.path.exists(FOUND) else {}
    try: seen = {(x.get('contract') or '').lower() for x in json.load(open(os.path.join(os.path.dirname(D), 'seen.json')))} | {(x.get('token') or '').lower() for x in json.load(open(os.path.join(os.path.dirname(D), 'seen.json')))}
    except Exception: seen = set()
    cands = sorted([(len(e['r']), a) for a, e in toks.items() if len(e['r']) >= MIN_RECIP and a not in checked], reverse=True)[:MAX_CHECK]
    # ② 48 小时前就存在的老合约跳过（QuickNode 支持历史状态）
    old_blk = hex(max(1, head - int(48 * 3600 / bt)))
    codes_old = F.bat([('eth_getCode', [a, old_blk]) for _, a in cands]) if cands else []
    new_agent, added = [], []
    for (n, a), co in zip(cands, codes_old):
        if time.time() > F.DEADLINE - 12: break
        if co and len(co) > 2: checked[a] = {'v': 'old', 'at': time.time()}; continue
        e = toks[a]
        txs = [x for x in F.bat([('eth_getTransactionByHash', [h]) for h in e['tx'][:3]]) if x]
        callees = list(dict.fromkeys([x['to'].lower() for x in txs if x.get('to')]))
        callers = len({x['from'].lower() for x in txs})
        vals = sorted({round(int(x.get('value', '0x0'), 16) / 1e18, 6) for x in txs})
        why, fm_addr = None, None
        for c in list(dict.fromkeys(callees + [a]))[:4]:
            try: fc = CU.full_code(F.rpc, F.call, c, None)
            except Exception: continue
            if CU.REG in fc:
                why = why or 'registry_in_code'
                if F.TOPICS[0][2:] in fc or ('63' + CU.sel('costOf(uint256)')) in fc: fm_addr = c
                elif any(v > 0 for v in vals) and c in callees and not fm_addr: fm_addr = c  # 付费铸造的被调用合约（非 Jager 模板）也交给 fairmint 读进度
        if not why:  # 第 5 层：后端签名型——收币地址六成以上持有 .ai 身份
            rs = list(e['r'])[:8]
            bals = F.bat([('eth_call', [{'to': REG, 'data': '0x70a08231' + r[2:].rjust(64, '0')}, 'latest']) for r in rs])
            ok = [b for b in bals if isinstance(b, str) and len(b) >= 66]
            if len(ok) >= 5 and sum(1 for b in ok if int(b[:66], 16) > 0) >= 0.6 * len(ok): why = 'holders_ai'
        if not why: checked[a] = {'v': 'not_agent', 'at': time.time()}; continue
        checked[a] = {'v': 'agent', 'at': time.time()}
        nm = F._str(F.call(a, F.sel('name()'))); sym = F._str(F.call(a, F.sel('symbol()')))
        row = {'token': a, 'name': nm or '未命名', 'symbol': sym, 'reason': why, 'recipients_30m': n, 'hot': n >= HOT_RECIP,
               'callees': callees, 'sample_callers': callers, 'sample_value_bnb': vals, 'free': vals == [0.0],
               'fairmint_contract': fm_addr, 'in_found': bool(fm_addr and fm_addr in found), 'in_seen': a in seen or any(c in seen for c in callees),
               'bscscan': f'https://bscscan.com/token/{a}', 'first_seen_cst': now_cst()}
        new_agent.append(row)
        if fm_addr and fm_addr not in found: added.append(fm_addr)
    # ⑤ 新 fair mint 合约并入 found.json（与 fairmint.py 共用锁，拿不到锁就留给下一轮）
    if added:
        lk = open(os.path.join(D, '.fairmint.lock'), 'w')
        try:
            for _ in range(10):
                try: fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB); break
                except OSError: time.sleep(2)
            else: raise OSError('lock busy')
            found = json.load(open(FOUND)) if os.path.exists(FOUND) else {}
            infos = F.info_many([x for x in added if x not in found])
            for x, i in infos.items():
                i.update({'first_seen_cst': now_cst(), 'first_block_seen': head, 'bscscan': f'https://bscscan.com/address/{x}', 'source': 'heatscan', 'mints_in_scan': 0})
                found[x] = i
            json.dump(found, open(FOUND + '.tmp', 'w'), ensure_ascii=False, indent=1); os.replace(FOUND + '.tmp', FOUND)
        except Exception as ex:
            errors.append(f'found.json 未写入（下轮重试）：{str(ex)[:100]}')
            for r in new_agent:
                if r['fairmint_contract'] in added: checked.pop(r['token'], None)
            added = []
    # 学 ：已追踪的合约发生代理升级 → 撤销审计通过（autofill 不再打满），并报出来
    upgraded = []
    if upg_hits:
        AOKP = os.path.join(D, 'audit_ok.json'); aok = json.load(open(AOKP)) if os.path.exists(AOKP) else {}
        tracked = set(found) | set(aok) | {(x.get('token') or '').lower() for x in aok.values() if isinstance(x, dict)}
        for x, b in upg_hits.items():
            if x not in tracked: continue
            upgraded.append({'contract': x, 'block': b, 'name': (found.get(x) or {}).get('name'), 'bscscan': f'https://bscscan.com/address/{x}'})
            for k, v in aok.items():
                if isinstance(v, dict) and (k == x or (v.get('token') or '').lower() == x) and v.get('ok'):
                    v['ok'] = False; v['note'] = f'合约在区块 {b} 发生代理升级，审计作废（{now_cst()}）'
        if upgraded: json.dump(aok, open(AOKP, 'w'), ensure_ascii=False, indent=1)
    json.dump(st, open(ST + '.tmp', 'w')); os.replace(ST + '.tmp', ST)
    print(json.dumps({'time_cst': now_cst(), 'head': head, 'scanned_blocks': scanned_to - start + 1, 'window_tokens': len(toks),
                      'candidates_checked': len(cands), 'new_agent': new_agent, 'added_fairmint': added, 'upgraded_tracked': upgraded, 'errors': errors}, ensure_ascii=False, indent=1))


if __name__ == '__main__': main()
