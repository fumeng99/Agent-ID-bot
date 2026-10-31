#!/usr/bin/env python3
"""链上发现 · 第二探测器：免费登记 / 免费领取（不铸币、只记名，没有 Transfer(from=0) 事件）。
scan.py 只看 Transfer(from=0)，像 aiboss.fit 的 BossFreeRegister（register() value=0，只发 Registered 事件）会完全漏掉。

原理（只读，不签名、不交易）：
  1. 取自上次以来（最多最近 35 分钟）的区块，按时限均匀抽样（每块一次 eth_getLogs，不带 topic；BSC 每块约 200 条日志/200KB，
     全量扫 35 分钟约 900MB 跑不完，所以按 REG_MAX_BLOCKS 抽样，每段 3 块、按间隔权重估算真实笔数）。复用 scan.rpc 的节点轮换/冷却。
  2. 按发事件合约统计 30 分钟窗口内不同交易数（估算值 = 抽到的交易 × 间隔权重）。发过 Transfer/Approval/Swap/Sync 等常见事件的
     （代币、交易对，scan.py 已覆盖）、SKIP 里的、scan.py 已检查过的、本脚本检查过的（regstate.json）一律跳过。
  3. 估算 ≥6 笔（且至少抽到 2 笔）的合约，按笔数取前 REG_TOP（默认 30）个，抽样 ≤6 笔交易 eth_getTransactionByHash：
     看 value、选择器、目标合约、from 是否持有 .ai（登记合约 balanceOf>0）。
     目标合约码含登记合约（has_registry，含代理/最小代理）=> agentid=code；抽样 from ≥60% 持 .ai => agentid=holders。
  4. 命中即候选：value 全为 0 => kind=free_register（free_like=true，交给 precheck/mint_many 预演）；否则 kind=paid_register 只报。
输出：stdout JSON（discover_all.sh 存到 discover_out/regscan.json），并把新候选并入 chainscan/candidates.json（格式同 scan.py，source=register_event）。
用法: python3 regscan.py [--minutes 35]
      python3 regscan.py --from <块> --to <块> --dry     # 历史回放：不读写 state、不写 candidates.json
环境变量：REG_DEADLINE（总时限秒，默认 95）、REG_MAX_BLOCKS（每轮最多抽多少块，默认 400）、REG_TOP（每轮最多检查几个合约，默认 30）。
"""
import json, os, sys, time, math, fcntl
from concurrent.futures import ThreadPoolExecutor
T0 = time.time(); DEADLINE = T0 + float(os.environ.get('REG_DEADLINE', '95'))
os.environ.setdefault('SCAN_DEADLINE', os.environ.get('REG_DEADLINE', '95'))
D = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, D)
import scan as S  # 复用 rpc / 节点冷却 / has_registry / holds_ai / creation_block / 原子写
SD = S.SD; STATE = os.path.join(SD, 'regstate.json'); OUT = S.OUT
MAX_BLOCKS = int(os.environ.get('REG_MAX_BLOCKS', '400')); TOP = int(os.environ.get('REG_TOP', '30'))
WINDOW = 30 * 60; MIN_TX = 6; THREADS = 4; PRE = int(os.environ.get('REG_PRE', '120'))
# 常见事件：发过这些的合约是代币/交易对/WBNB/NFT，归 scan.py（Transfer）管或与登记无关
COMMON = {
    S.T_TRANSFER,
    '0x8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3b925',  # Approval
    '0x1c411e9a96e071241c2f21f7726b17ae89e3cab4c78be50e062b03a9fffbbad1',  # Sync
    '0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822',  # Swap v2
    '0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67',  # Swap v3
    '0x19b47279256b2a23a1665c810c8d55a1758940ee09377d4f8d26497a3577dc83',  # Swap pancake v3
    '0xe1fffcc4923d04b559f4d29a8bfc6cda04eb5b0d3c460751c2402c5c5cc9109c',  # Deposit (WBNB)
    '0x7fcf532c15f0a6db0bd6d0e038bea71d30d808c7d98cb3bf7268a95bf5081b65',  # Withdrawal (WBNB)
    '0xc3d58168c5ae7397731d063d5bbf3d657854427343f4c083240f7aacaa2d0f62',  # TransferSingle
    '0x4a39dc06d4c0dbc64b70af90fd698a233a518aa5d07e595d983b8c0526c8f7fb',  # TransferBatch
    '0x17307eab39ab6107e8899845ad3d59bd9653f200f220920489ca2b5937696c31',  # ApprovalForAll
    '0x4c209b5fc8ad50758f13e2e1088ba56a560dff690a1c6fef26394f4c03821c4f',  # Mint (v2 pair)
    '0xdccd412f0b1252819cb1fd330b93224ca42612892bb3f4f789976e6d81936496',  # Burn (v2 pair)
}
SKIP = set(S.SKIP) | {'0x' + S.REGISTRY}


def lock_merge_candidates(cands):
    """与 scan.py 并行运行时防止互相覆盖 candidates.json：加文件锁后读-并-写。"""
    with open(OUT + '.lock', 'w') as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        old = S._load_json(OUT, [])
        have = {(c.get('contract') or c.get('token') or '').lower() for c in old}
        merged = old + [c for c in cands if c['contract'] not in have]
        S._save_json(OUT, merged[-200:], ensure_ascii=False, indent=1)


def main():
    argv = sys.argv; dry = '--dry' in argv
    mins = int(argv[argv.index('--minutes') + 1]) if '--minutes' in argv else 35
    st = {} if dry else S._load_json(STATE, {})
    head = int(S.rpc('eth_blockNumber', []), 16)
    hb = S.rpc('eth_getBlockByNumber', [hex(head), False]); pb = S.rpc('eth_getBlockByNumber', [hex(head - 1000), False])
    bt = max(0.2, (int(hb['timestamp'], 16) - int(pb['timestamp'], 16)) / 1000)
    if '--from' in argv:
        start = int(argv[argv.index('--from') + 1]); end = int(argv[argv.index('--to') + 1]) if '--to' in argv else head
    else:
        start = max(st.get('last', 0) + 1, head - int(mins * 60 / bt)); end = head
    end_ts = int(S.rpc('eth_getBlockByNumber', [hex(end), False])['timestamp'], 16)
    n = end - start + 1
    C = 3  # 每次取连续 3 块（0.3–0.5 秒/次），比逐块请求快
    nchunks = max(1, min(math.ceil(n / C), MAX_BLOCKS // C)); step = max(C, math.ceil(n / nchunks))
    off = int(time.time()) % max(1, step - C + 1)  # 每轮换偏移，长期覆盖到所有块
    chunks = [(f, min(f + C - 1, end)) for f in range(start + off, end + 1, step)]
    weight = step / C  # 抽样权重：估算真实笔数 = 抽到的笔数 × weight
    sys.stderr.write(f'regscan {start}->{end} ({n} 块) 抽样 {len(chunks)} 段×{C} 块 step={step} bt={bt}\n')
    log_end = T0 + (DEADLINE - T0) * 0.4

    def getlogs(r):
        if time.time() > log_end: return None
        try: return S.rpc('eth_getLogs', [{'fromBlock': hex(r[0]), 'toBlock': hex(r[1])}])
        except Exception: return 'err'
    with ThreadPoolExecutor(THREADS) as ex: res = list(ex.map(getlogs, chunks))
    log_end += 10  # 报错段换节点补一次
    res = [(getlogs(r) or 'err') if x == 'err' else x for r, x in zip(chunks, res)]
    blocks = chunks
    win = st.get('win', {})       # addr -> {tx: [ts, weight]}
    common = set(st.get('common', []))
    errors = 0; done_blocks = 0; last = start - 1
    for (b, b2), logs in zip(blocks, res):
        if logs is None: break    # 时限到，剩下的留给下一轮
        last = max(last, min(b + step - 1, end)); done_blocks += 1
        if logs == 'err': errors += 1; continue
        for l in logs:
            ts = end_ts - int((end - int(l['blockNumber'], 16)) * bt)
            a = l['address'].lower()
            if a in SKIP: continue
            t0 = l['topics'][0] if l.get('topics') else ''
            if t0 in COMMON: common.add(a); continue
            win.setdefault(a, {}).setdefault(l['transactionHash'], [ts, weight])
    if done_blocks == len(blocks): last = end
    sys.stderr.write(f'logs done {time.time()-T0:.1f}s chunks={done_blocks}/{len(blocks)} addrs={len(win)} err={errors}\n')
    for a in list(win):
        if a in common: del win[a]; continue
        win[a] = {h: v for h, v in win[a].items() if v[0] >= end_ts - WINDOW}
        if not win[a]: del win[a]

    checked = st.get('checked', {})
    scan_checked = S._load_json(S.STATE, {}).get('checked', {}) if os.path.exists(S.STATE) else {}
    ranked = []
    for a, txs in win.items():
        est = sum(v[1] for v in txs.values())
        if len(txs) >= 2 and est >= MIN_TX and a not in checked and a not in scan_checked: ranked.append((est, a))
    ranked.sort(reverse=True)
    # 预筛（批量、便宜）：前 PRE 个合约各取 3 笔交易，一次 batch 拿回；同一钱包反复调用的（机器人/keeper/预言机）直接排除
    from rpcpool import batch
    pre = ranked[:PRE]; pending = len(ranked) - len(pre)
    hsl = {a: list(win[a])[-3:] for _, a in pre}
    flat = [(a, h) for _, a in pre for h in hsl[a]]
    txmap = {}
    if flat and DEADLINE - time.time() > 15:
        try:
            got = batch([('eth_getTransactionByHash', [h]) for _, h in flat], tolerant=True)
            for (a, h), t in zip(flat, got):
                if t: txmap.setdefault(a, []).append(t)
        except Exception as e: sys.stderr.write(f'WARN 预筛 batch 失败 {e}\n')
    deep = []
    for est, a in pre:
        txs = txmap.get(a, [])
        if len(txs) < 2: pending += 1; continue
        froms = [t['from'].lower() for t in txs]
        if len(set(froms)) < len(froms): checked[a] = 'repeat_sender'; continue
        deep.append((est, a))
    # 批量查抽样 from 是否持 .ai（登记合约 balanceOf>0），一次 batch 覆盖全部深查对象
    allw = sorted({t['from'].lower() for _, a in deep for t in txmap[a]})
    hold = {}
    if allw and DEADLINE - time.time() > 12:
        try:
            got = batch([('eth_call', [{'to': '0x' + S.REGISTRY, 'data': '0x70a08231' + w[2:].rjust(64, '0')}, 'latest']) for w in allw], tolerant=True)
            hold = {w: bool(r and r != '0x' and int(r[:66], 16) > 0) for w, r in zip(allw, got)}
        except Exception as e: sys.stderr.write(f'WARN .ai 批量查询失败 {e}\n')
    # 批量取合约码：码里直接含登记合约 => code；码很短（代理/最小代理）的才走完整 has_registry 追实现
    codes = {}
    if deep and DEADLINE - time.time() > 12:
        try:
            got = batch([('eth_getCode', [a, 'latest']) for _, a in deep], tolerant=True)
            codes = {a: (c or '0x').lower() for (_, a), c in zip(deep, got)}
        except Exception as e: sys.stderr.write(f'WARN getCode 批量失败 {e}\n')
    def reg_check(a):
        c = codes.get(a)
        if c is None: return S.has_registry(a)
        if S.REGISTRY in c: return True
        return S.has_registry(a) if len(c) < 2000 else False
    def ai_ratio(a):
        fs = list(dict.fromkeys(t['from'].lower() for t in txmap[a]))
        return sum(hold.get(w, False) for w in fs) / len(fs)
    if dbg := os.environ.get('REG_DEBUG', '').lower():
        pos = [i for i, (_, a) in enumerate(ranked) if a == dbg]
        sys.stderr.write(f'DEBUG {dbg} 窗口内笔数={len(win.get(dbg, {}))} 排名={pos} 在深查={any(a == dbg for _, a in deep)}\n')
    # 持 .ai 比例高的先深查（最可能是 Agent ID 专属），其余按估算笔数
    deep.sort(key=lambda x: (ai_ratio(x[1]) < 0.6, -x[0]))
    sys.stderr.write(f'预筛 {len(pre)} 个 -> 深查候选 {len(deep)} 个（.ai≥60%: {sum(ai_ratio(a) >= 0.6 for _, a in deep)}） {time.time()-T0:.1f}s\n')
    cands = []; looked = 0
    for est, a in deep:
        if looked >= TOP or DEADLINE - time.time() < 8: pending += 1; continue
        looked += 1
        txs = list(txmap[a])
        if ai_ratio(a) >= 0.6:  # 补到 6 笔再确认一次
            for h in list(win[a])[-6:]:
                if len(txs) >= 6: break
                if any(t['hash'] == h for t in txs): continue
                try: txs.append(S.rpc('eth_getTransactionByHash', [h]))
                except Exception: pass
        froms = list(dict.fromkeys(t['from'].lower() for t in txs))
        targets = {}
        for t in txs: k = (t.get('to') or '').lower(); targets[k] = targets.get(k, 0) + 1
        target = max(targets, key=targets.get)
        values = [int(t['value'], 16) for t in txs]
        sels = {}
        for t in txs: s_ = t['input'][:10]; sels[s_] = sels.get(s_, 0) + 1
        sel = max(sels, key=sels.get)
        ai_n = sum(hold[w] if w in hold else S.holds_ai(w) for w in froms)
        reg = reg_check(a) or (target and target != a and S.has_registry(target))
        agentid = 'code' if reg else ('holders' if ai_n / len(froms) >= 0.6 else '')
        checked[a] = 'agentid' if agentid else 'no'
        sys.stderr.write(f'chk {a} est={est:.0f} froms={len(froms)} ai={ai_n} reg={bool(reg)} -> {agentid or "no"} {time.time()-T0:.1f}s\n')
        if not agentid: continue
        tgt = target or a
        sel_txs = [t for t in txs if t['input'][:10] == sel and (t.get('to') or '').lower() == tgt]
        no_args = bool(sel_txs) and all(len(t['input']) == 10 for t in sel_txs)
        free = max(values) == 0
        flags = []
        if DEADLINE - time.time() > 25:
            cb = S.creation_block(tgt, head)  # 约 20 次历史 getCode，较慢
            if cb is None: flags.append('老合约(>2天)')
        else: cb = 0  # 时间不够未查部署块，巡逻第 4 步照常核对部署时间
        if sel in S.BAD_SEL: flags.append('选择器是转账/授权类，不可照抄')
        if not no_args: flags.append('调用带参数，需 replay.py 核对（可能含地址/签名）')
        if tgt != a: flags.append(f'调用目标 {tgt} 与发事件合约不同，需核对')
        if not free: flags.append('付费登记(value>0)，按付费规则只报')
        cands.append({
            'contract': tgt, 'token': a, 'mint_target': tgt, 'event_contract': a,
            'name': S.tstr(tgt, '0x06fdde03'), 'symbol': '',
            'source': 'register_event', 'kind': 'free_register' if free else 'paid_register',
            'selector': sel, 'selector_name': S.FREE_SELECTORS.get(sel, ''), 'calldata': sel if no_args else '',
            'value': 0 if free else max(values), 'tx_values_wei': values, 'free_like': free,
            'txs_30m_est': round(est), 'wallets_30m': len(froms), 'agentid': agentid, 'ai_holders': f'{ai_n}/{len(froms)}',
            'sample_txs': [t['hash'] for t in txs[:3]], 'created_block': cb, 'risk_flags': flags, 'auto_ok': not flags,
            'bscscan': f'https://bscscan.com/address/{tgt}',
            'found_at_cst': time.strftime('%Y-%m-%d %H:%M', time.gmtime(end_ts + 8 * 3600))})
    if not dry:
        S._save_json(STATE, {'last': last, 'win': win, 'common': list(common)[-5000:],
                             'checked': dict(list(checked.items())[-5000:])})
        if cands: lock_merge_candidates(cands)
    print(json.dumps({'head': head, 'from': start, 'to': end, 'scanned_to': last, 'blocks_left': end - last, 'sampled_blocks': done_blocks,
                      'step': step, 'weight': round(weight, 2), 'log_errors': errors, 'contracts_in_window': len(win), 'checked_this_round': looked,
                      'unchecked_left': pending, 'elapsed_s': round(time.time() - T0, 1), 'dry': dry, 'new_candidates': cands},
                     ensure_ascii=False, indent=1))


if __name__ == '__main__': main()
