#!/usr/bin/env python3
"""BurnAgentMixed 类「前 N 个 Agent ID 首份免费」自动领（2026-11-03 01:27 用户「为什么这个免费的没自动打成功」）。
漏的原因：mixedfree.py 只试「数量填 0」的写法（mint(0)/claim(0)…），这类合约的免费份是 mint(id, 1, expectedStart, minTokens, deadline) 且 value=0
（合约内部判断 ID 是否还在前 300 名），所以被判「无免费份写法」；而且 mixedfree 领取只在巡逻里跑（约 10 分钟一次），300 份很快被领完。
做法：discoverd 每轮（约 60 秒）拉起本脚本（flock 不重叠）：
  1. 目标：fairmint/found.json 里 48 小时内发现、未开池的合约；字节码含 quoteFor(uint256,uint8)+freeClaimed(uint256)+FREE_SHARES()。
  2. 链上 freeMinted < FREE_SHARES 才继续；规则 B：verify_source.py 已验证（含字节码指纹一致）。
  3. burnmix_buy.py <合约> --free：每号 1 份、quoteFor 报价必须为 0 才发，preview 干净（无扣款/授权/风险）才 execute，被抢立刻重试，unknown 不重发。
只走 0 元路径（用户 2026-10-30 起的 0 元免费 Mint 自动规则），不放宽任何付费闸门。
"""
import json, os, sys, time, subprocess, calendar
D = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, D)
from rpcpool import batch, rpc
from codeutil import sel
ST = os.path.join(D, 'burnfree_state.json'); OUT = os.path.join(D, 'discover_out/burnfree.json')
NEED = [sel('quoteFor(uint256,uint8)'), sel('freeClaimed(uint256)'), sel('FREE_SHARES()')]
NEED = [x[2:] if x.startswith('0x') else x for x in NEED]


def load(p, d):
    try: return json.load(open(p))
    except Exception: return d


def h(s): s = sel(s); return s if s.startswith('0x') else '0x' + s


def main():
    st = load(ST, {}); found = load(os.path.join(D, 'fairmint/found.json'), {}); out = {'time': time.strftime('%m-%d %H:%M', time.gmtime(time.time() + 8 * 3600)), 'checked': [], 'minted': []}
    cand = []
    for a, v in found.items():
        if v.get('opened'): continue
        try: t = calendar.timegm(time.strptime(v.get('first_seen_cst') or '', '%Y-%m-%d %H:%M')) - 8 * 3600
        except Exception: continue
        if time.time() - t > 48 * 3600: continue
        e = st.get(a) or {}
        if e.get('done') or (e.get('not_type') and time.time() - e.get('t', 0) < 6 * 3600): continue
        cand.append(a)
    if not cand: json.dump(out, open(OUT, 'w'), ensure_ascii=False); return
    codes = dict(zip(cand, batch([('eth_getCode', [c, 'latest']) for c in cand], tolerant=True, chunk=40)))
    for a in cand:
        code = (codes.get(a) or '').lower()
        if not code or len(code) < 10: continue
        if not all(x in code for x in NEED): st[a] = {'not_type': True, 't': time.time()}; continue
        try:
            fs = int(rpc('eth_call', [{'to': a, 'data': h('FREE_SHARES()')}, 'latest']), 16)
            fm = int(rpc('eth_call', [{'to': a, 'data': h('freeMinted()')}, 'latest']), 16)
        except Exception as ex: out['checked'].append((a, 'read_err', str(ex)[:80])); continue
        if fm >= fs: st[a] = {'done': True, 'why': f'free_gone {fm}/{fs}', 't': time.time()}; out['checked'].append((a, 'free_gone', fm, fs)); continue
        try: vs = json.loads(subprocess.run(['python3', os.path.join(D, 'verify_source.py'), a], capture_output=True, text=True, timeout=60).stdout.splitlines()[0])
        except Exception: vs = {'verified': None}
        if vs.get('verified') is not True: out['checked'].append((a, 'not_verified_or_unknown', vs.get('verified'))); continue
        r = subprocess.run(['python3', os.path.join(D, 'burnmix_buy.py'), a, '--free', '--tries', '25'], capture_output=True, text=True, timeout=600).stdout.strip().splitlines()
        res = json.loads(r[-1]) if r and r[-1].startswith('[') else r[-3:]
        out['minted'].append({'contract': a, 'name': found[a].get('name'), 'free_left': fs - fm, 'result': res})
        if isinstance(res, list) and all(x[1] in ('free_already', 'free_gone', 'opened_stop', 'no_agent_id') or (len(x) > 2 and x[2] == 'ok') for x in res):
            st[a] = {'done': True, 't': time.time()}
    json.dump(st, open(ST, 'w'), ensure_ascii=False); json.dump(out, open(OUT, 'w'), ensure_ascii=False)
    if out['minted']: print(json.dumps(out, ensure_ascii=False))


main()
