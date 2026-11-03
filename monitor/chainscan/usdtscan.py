#!/usr/bin/env python3
"""链上发现 · 第三探测器：USDT 付款的 Agent ID 销售盘（2026-11-03 用户「要」：FLY flyaiagent.xyz 漏发现后补）。
FLYSale 这类盘：buy() 的 BNB value=0（regscan 会误当免费）、付款走 USDT transferFrom、代币从合约余额转出（没有 Transfer(from=0)）、
不是 Jager/AGI 模板（fairmint 读不出进度），三个探测器都漏。

原理（只读，不签名、不交易）：
  1. 最近 --minutes（默认 20）分钟按间隔抽样 SAMPLE 个块，每块 eth_getLogs(address=USDT, topic0=Transfer)。
  2. 按 USDT 收款地址（topic2）统计不同付款钱包数；估算（×抽样权重）≥ MIN_WALLETS 且抽到 ≥2 个不同钱包的进入初筛。
  3. 初筛：必须是合约（有代码）、不是交易对（无 token0()）、不在 SKIP/已检查名单。
  4. 深查：合约码含 Agent ID 登记合约（含代理/最小代理）=> agentid=code；或抽样付款钱包 ≥60% 持 .ai => agentid=holders。
  5. 命中 => kind=paid_usdt_sale 候选（free_like=false），按统一付费规则只出简报，不自动买；并入 candidates.json。
用法: python3 usdtscan.py [--minutes 20] [--dry]
      python3 usdtscan.py --from <块> --to <块> --dry     # 历史回放
"""
import json, os, sys, time, math
from concurrent.futures import ThreadPoolExecutor
T0 = time.time(); DEADLINE = T0 + float(os.environ.get('USDT_DEADLINE', '95'))
os.environ.setdefault('SCAN_DEADLINE', os.environ.get('USDT_DEADLINE', '95'))
D = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, D)
import scan as S
from regscan import lock_merge_candidates
STATE = os.path.join(S.SD, 'usdtstate.json')
USDT = '0x55d398326f99059ff775485246999027b3197955'
SAMPLE = int(os.environ.get('USDT_SAMPLE', '90')); MIN_WALLETS = 5; TOP = 25; THREADS = 6


def logs_at(b):
    try:
        return S.rpc('eth_getLogs', [{'fromBlock': hex(b), 'toBlock': hex(b), 'address': USDT, 'topics': [S.T_TRANSFER]}])
    except Exception:
        return None


def is_pair(a):
    return S._safe_call(a, '0x0dfe1681') not in (None, '0x')  # token0()


def main():
    argv = sys.argv; dry = '--dry' in argv
    mins = int(argv[argv.index('--minutes') + 1]) if '--minutes' in argv else 20
    st = {} if dry else S._load_json(STATE, {})
    checked = st.get('checked', {})
    head = int(S.rpc('eth_blockNumber', []), 16)
    if '--from' in argv:
        start, end = int(argv[argv.index('--from') + 1]), int(argv[argv.index('--to') + 1])
    else:
        hb = S.rpc('eth_getBlockByNumber', [hex(head), False]); pb = S.rpc('eth_getBlockByNumber', [hex(head - 1000), False])
        bt = max(0.2, (int(hb['timestamp'], 16) - int(pb['timestamp'], 16)) / 1000)
        end = head; start = max(0, head - int(mins * 60 / bt))
    n = end - start + 1; k = min(SAMPLE, n); step = n / k
    off = int(time.time()) % max(1, int(step))
    blocks = sorted({min(end, start + int(i * step) + off) for i in range(k)}); weight = n / len(blocks)
    sys.stderr.write(f'usdtscan {start}->{end} ({n} 块) 抽 {len(blocks)} 块 权重 {weight:.1f}\n')
    recv = {}; errors = 0; done = 0
    with ThreadPoolExecutor(THREADS) as ex:
        for b, ls in zip(blocks, ex.map(logs_at, blocks)):
            if ls is None: errors += 1; continue
            done += 1
            for l in ls:
                t = l.get('topics') or []
                if len(t) < 3: continue
                frm = '0x' + t[1][-40:]; to = '0x' + t[2][-40:]
                if to in S.SKIP or frm == to: continue
                recv.setdefault(to, {}).setdefault(frm, l['transactionHash'])
    ranked = sorted(((len(w) * weight, a) for a, w in recv.items() if len(w) >= 2 and len(w) * weight >= MIN_WALLETS and a not in checked), reverse=True)
    sys.stderr.write(f'logs {done}/{len(blocks)} err={errors} 收款地址 {len(recv)} 初筛 {len(ranked)} {time.time()-T0:.1f}s\n')
    # 批量预筛：每个收款地址抽 5 个付款钱包，一次 batch 查是否持 .ai（便宜），≥60% 才深查；CEX/机器人地址在这一步就淘汰
    sys.path.insert(0, os.path.dirname(D)); from rpcpool import batch
    pre = ranked[:300]
    wl = {a: list(recv[a])[:5] for _, a in pre}
    allw = sorted({w for ws in wl.values() for w in ws})
    hold = {}
    try:
        rs = batch([('eth_call', [{'to': '0x' + S.REGISTRY, 'data': '0x70a08231' + w[2:].rjust(64, '0')}, 'latest']) for w in allw],
                   tolerant=True, parallel=6, deadline=DEADLINE - 25)
        hold = {w: bool(r and r != '0x' and int(r[:66], 16) > 0) for w, r in zip(allw, rs)}
    except Exception as e: sys.stderr.write(f'WARN .ai batch 失败 {e}\n')
    deep = []
    for est, a in pre:
        ws = wl[a]; n_ai = sum(hold.get(w, False) for w in ws)
        if not hold: deep.append((est, a, n_ai, ws)); continue
        if n_ai / len(ws) >= 0.6: deep.append((est, a, n_ai, ws))
        else: checked[a] = 'no_ai'
    sys.stderr.write(f'.ai 预筛 {len(pre)} -> {len(deep)} {time.time()-T0:.1f}s\n')
    cands = []; looked = 0
    for est, a, ai_n, froms in deep:
        if looked >= TOP or time.time() > DEADLINE - 10: break
        c = S.code(a)
        if c in ('0x', ''): checked[a] = 'eoa'; continue
        looked += 1
        if is_pair(a): checked[a] = 'pair'; continue
        reg = S.has_registry(a)
        agentid = 'code' if reg else ('holders' if ai_n / len(froms) >= 0.6 else '')
        checked[a] = 'agentid' if agentid else 'no'
        sys.stderr.write(f'chk {a} est={est:.0f} froms={len(froms)} ai={ai_n} reg={reg} -> {agentid or "no"}\n')
        if not agentid: continue
        flags = ['USDT 付款盘，按统一付费规则只出简报（读进度/国库/储备后再判断）']
        if S.impl_of(a): flags.append('代理合约，需追实现')
        cands.append({
            'contract': a, 'token': a, 'mint_target': a, 'event_contract': a,
            'name': S.tstr(a, '0x06fdde03'), 'symbol': '', 'source': 'usdt_sale', 'kind': 'paid_usdt_sale',
            'pay_token': 'USDT', 'value': 0, 'free_like': False, 'auto_ok': False,
            'wallets_est': round(est), 'wallets_sampled': len(recv[a]), 'agentid': agentid, 'ai_holders': f'{ai_n}/{len(froms)}',
            'sample_txs': list(recv[a].values())[:3], 'risk_flags': flags,
            'bscscan': f'https://bscscan.com/address/{a}',
            'found_at_cst': time.strftime('%Y-%m-%d %H:%M', time.gmtime(time.time() + 8 * 3600))})
    if not dry:
        S._save_json(STATE, {'checked': dict(list(checked.items())[-5000:])})
        if cands: lock_merge_candidates(cands)
    print(json.dumps({'head': head, 'from': start, 'to': end, 'sampled_blocks': done, 'log_errors': errors,
                      'receivers': len(recv), 'prefilter': len(ranked), 'checked_this_round': looked,
                      'elapsed_s': round(time.time() - T0, 1), 'dry': dry, 'new_candidates': cands}, ensure_ascii=False, indent=1))


if __name__ == '__main__': main()
