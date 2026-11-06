#!/usr/bin/env python3
"""押金全退型挖矿（AGC 模板）自动挖 + 自动领回。用户 2026-11-03 01:56「要」（每天自动挖）+「这种为什么没监控到 自动打」。
模板识别（字节码同时含）：mintFree(uint256,address)、claimFree(uint256[])、quota(address,uint256)、refundOwed()、REFUND_DELAY()、PRICE_PER_SHARE()。
闸门（每次都查，任何一项不过就不挖，只领回）：源码已验证（verify_source，含指纹）；非代理（EIP-1967 实现槽为空）；合约 BNB 余额 ≥ refundOwed（押金全额可退）；
quota.open 且未暂停；每号每天押金 ≤ 0.02 BNB；扣押金和 gas 后钱包仍 ≥ 0.003 BNB；每号 preview 干净（只扣押金）才 execute；unknown 不重发。
领回：claimFree([]) value=0，有到期仓位才发，preview 干净才 execute（只增 BNB 和代币）。领回成功后立刻全卖奖励币（用户 11-03 13:15「领取了立马卖」）。
目标：refundmine_targets.json 手动列表 + fairmint/found.json / chainscan/candidates.json / discover_out/newtpl.json(refundmine_candidates) 中自动识别出的合约。discoverd 每轮约 60 秒拉起（flock）。
usage: refundmine.py [--dry] [合约...]"""
import sys, os, json, time
D = os.path.dirname(os.path.abspath(__file__)); os.chdir(D)
args = sys.argv[1:]; DRY = '--dry' in args
manual = [a.lower() for a in args if a.startswith('0x')]
sys.argv = ['fair_buy.py', '0x' + '0' * 40, '0x' + '0' * 40, '10'] + (['--dry'] if DRY else [])
src = open('fair_buy.py').read(); exec(src.split('from concurrent.futures import ThreadPoolExecutor')[0])
from rpcpool import batch, rpc
import subprocess
ST = 'refundmine_state.json'; OUT = 'discover_out/refundmine.json'; TG = 'refundmine_targets.json'
NEED = [sel(s).replace('0x', '') for s in ('mintFree(uint256,address)', 'claimFree(uint256[])', 'quota(address,uint256)', 'refundOwed()', 'REFUND_DELAY()', 'PRICE_PER_SHARE()')]
CAP = int(0.02e18); KEEP = int(0.003e18); GAS = int(0.0002e18)
IMPL_SLOT = '0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc'
def load(p, d):
    try: return json.load(open(p))
    except Exception: return d
st = load(ST, {}); tg = set(load(TG, [])) | set(manual)
cand = set()
for a in load('fairmint/found.json', {}): cand.add(a.lower())
for c in load('chainscan/candidates.json', []) if isinstance(load('chainscan/candidates.json', []), list) else []:
    for k in ('contract', 'mint_target'):
        if isinstance(c, dict) and c.get(k): cand.add(c[k].lower())
# 2026-11-03 newtpl.py：新模板自动识别出的押金全退型（refund_deposit + 结论 pass + 闸门通过）并入候选；下面仍要求 AGC 接口选择器齐全 + 每次全部闸门
for a in (load('discover_out/newtpl.json', {}) or {}).get('refundmine_candidates', []) or []: cand.add(str(a).lower())
cand = [a for a in cand if a not in tg and not (st.get(a, {}).get('not_type') and time.time() - st[a].get('t', 0) < 12 * 3600)]
if cand:
    codes = dict(zip(cand, batch([('eth_getCode', [c, 'latest']) for c in cand], tolerant=True, chunk=40)))
    for a, code in codes.items():
        if not code or len(code) < 10: continue
        if all(x in code.lower() for x in NEED): tg.add(a)
        else: st[a] = {'not_type': True, 't': time.time()}
json.dump(sorted(tg), open(TG, 'w'))
out = {'time': time.strftime('%m-%d %H:%M', time.gmtime(time.time() + 8 * 3600)), 'targets': sorted(tg), 'actions': [], 'blocked': []}
for M in sorted(tg):
    try:
        vs = json.loads(subprocess.run(['python3', 'verify_source.py', M], capture_output=True, text=True, timeout=60).stdout.splitlines()[0]).get('verified')
        impl = int(rpc('eth_getStorageAt', [M, IMPL_SLOT, 'latest']), 16)
        bal = int(rpc('eth_getBalance', [M, 'latest']), 16)
        owed = ci(M, '0x' + sel('refundOwed()')); price = ci(M, '0x' + sel('PRICE_PER_SHARE()'))
    except Exception as ex: out['blocked'].append((M, 'read_err', str(ex)[:80])); continue
    slack = int(0.05e18) if M.lower() == '0x6288dfe86f22b53eeb6886ae2d78941de7483743' else 0  # 2026-11-05 16:56 用户授权：AGC 合约余额差额 ≤0.05 BNB 仍放行
    gate_ok = vs is True and impl == 0 and bal + slack >= owed and price > 0
    if not gate_ok: out['blocked'].append((M, 'gate', {'verified': vs, 'proxy': impl != 0, 'bal': bal / 1e18, 'owed': owed / 1e18}))
    MAINA = next(a for a in A if a['label'] == '主')
    for a in [MAINA] + [x for x in A if x is not MAINA]:
        addr = a['bsc'].lower(); pad = addr[2:].rjust(64, '0')
        # 领回：claimFree([]) 有到期仓位才发（先 eth_call 看是否 revert/有变化由 preview 决定）
        try:
            pq = call(M, '0x' + sel('pending(address)') + pad); n_free = int(pq[2:66], 16)  # 已到期可领的押金 BNB
        except Exception: n_free = None; pq = ''
        key = f'{M}|{a["label"]}'; e = st.get(key, {})
        if n_free and time.time() >= e.get('next_claim', 0):
            data = '0x' + sel('claimFree(uint256[])') + u(32) + u(0)
            stc = go(a, M, data, value='0')
            if stc[0] == 'dry_ok' and str(stc[1]) == '[]': stc = ('dry_empty', '')
            out['actions'].append((M[:10], a['label'], 'claim', stc[0], str(stc[1])[:90]))
            e['next_claim'] = time.time() + (1800 if stc[0] not in ('ok', 'dry_ok') else 3600)
            # 2026-11-03 13:15 用户原话「领取了立马卖」：领回确认（ok）后立刻把该号奖励币全部卖成 USDT（fast_sell：先 quote 预演、滑点 ≤8%、失败不下单），押金 BNB 不动
            if stc[0] == 'ok' and not DRY:
                try:
                    tok = '0x' + call(M, '0x' + sel('agc()'))[-40:]
                    lf = open('discover_out/agc_sell.log', 'a'); lf.write(time.strftime('%m-%d %H:%M:%S ', time.gmtime(time.time() + 8 * 3600)) + f'claim ok {a["label"]} -> sell {tok}\n'); lf.flush()
                    time.sleep(6)
                    subprocess.Popen(['python3', 'sell_retry.py', tok, '100', '--slip', '8', a['label']], stdout=lf, stderr=lf, cwd=D)
                    out['actions'].append((M[:10], a['label'], 'sell_spawned', tok))
                except Exception as ex: out['blocked'].append((M[:10], a['label'], 'sell_err', str(ex)[:80]))
        # 2026-11-05 17:10 用户原话「要 能领取了就立马卖」：B 档 mintPool 已释放奖励（pending 第 3 槽 rewardVested）≥10 万 AGC 就 claimReward([]) 领出，确认 ok 立刻全卖成 USDT（sell_retry：quote 预演、滑点 ≤8%、失败 5 秒重试 3 次）；同一轮已领 A 的号下一轮再领，避免两笔卖单抢同一余额
        try:
            vest = int(pq[130:194], 16)
        except Exception: vest = 0
        if vest >= 100000 * 10**18 and time.time() >= e.get('next_rclaim', 0) and not any(x[1] == a['label'] and x[2] == 'claim' for x in out['actions']):
            str_ = go(a, M, '0x' + sel('claimReward(uint256[])') + u(32) + u(0), value='0')
            out['actions'].append((M[:10], a['label'], 'claimReward', vest // 10**18, str_[0], str(str_[1])[:90]))
            e['next_rclaim'] = time.time() + (600 if str_[0] not in ('ok', 'dry_ok') else 1800)
            if str_[0] == 'ok' and not DRY:
                try:
                    tok = '0x' + call(M, '0x' + sel('agc()'))[-40:]
                    lf = open('discover_out/agc_sell.log', 'a'); lf.write(time.strftime('%m-%d %H:%M:%S ', time.gmtime(time.time() + 8 * 3600)) + f'claimReward ok {a["label"]} {vest // 10**18} AGC -> sell {tok}\n'); lf.flush()
                    time.sleep(6)
                    subprocess.Popen(['python3', 'sell_retry.py', tok, '100', '--slip', '8', a['label']], stdout=lf, stderr=lf, cwd=D)
                    out['actions'].append((M[:10], a['label'], 'sell_spawned_B', tok))
                except Exception as ex: out['blocked'].append((M[:10], a['label'], 'sell_err', str(ex)[:80]))
        # 2026-11-06 16:52 用户原话「要」（回答「19 号自动取回 LP 并卖成 U 吗」）：B 档锁定 14 天到期（pending 第 4 槽 lpWithdrawable>0）就 withdrawLP([]) 取回 LP（合约拆成 AGC+BNB 并结清剩余奖励），value=0、preview 干净才执行；取回的 AGC 立刻全卖成 USDT（sell_retry，滑点 ≤8%），BNB 留在钱包。只限该合约。
        try: lpw = int(pq[194:258], 16)
        except Exception: lpw = 0
        if lpw > 0 and time.time() >= e.get('next_lpw', 0) and not any(x[1] == a['label'] and x[2] in ('claim', 'claimReward') for x in out['actions']):
            str_ = go(a, M, '0x' + sel('withdrawLP(uint256[])') + u(32) + u(0), value='0')
            out['actions'].append((M[:10], a['label'], 'withdrawLP', lpw, str_[0], str(str_[1])[:90]))
            e['next_lpw'] = time.time() + (600 if str_[0] not in ('ok', 'dry_ok') else 300)
            if str_[0] == 'ok' and not DRY:
                try:
                    tok = '0x' + call(M, '0x' + sel('agc()'))[-40:]
                    lf = open('discover_out/agc_sell.log', 'a'); lf.write(time.strftime('%m-%d %H:%M:%S ', time.gmtime(time.time() + 8 * 3600)) + f'withdrawLP ok {a["label"]} {lpw} 份 -> sell {tok}\n'); lf.flush()
                    time.sleep(6)
                    subprocess.Popen(['python3', 'sell_retry.py', tok, '100', '--slip', '8', a['label']], stdout=lf, stderr=lf, cwd=D)
                    out['actions'].append((M[:10], a['label'], 'sell_spawned_LP', tok))
                except Exception as ex: out['blocked'].append((M[:10], a['label'], 'sell_err', str(ex)[:80]))
        st[key] = e
        # 挖：今日额度
        if gate_ok:
            try:
                q = call(M, '0x' + sel('quota(address,uint256)') + pad + u(0))
                w = [int(q[2 + 64 * i:66 + 64 * i], 16) for i in range(17)]
                aLeft, opened, paused = w[3], w[15], w[16]
                n = aLeft  # 2026-11-05 02:37 用户「后续每天打满 a和b」：A 档按当日额度打满，取消 0.02 BNB/号/天上限
                b = int(rpc('eth_getBalance', [a['bsc'], 'latest']), 16)
                if opened and not paused and n > 0 and b - n * price - GAS >= KEEP:
                    ref = '0' * 64 if a is MAINA else MAINA['bsc'][2:].lower().rjust(64, '0')
                    stm = go(a, M, '0x' + sel('mintFree(uint256,address)') + u(n) + ref, value=str(n * price))
                    out['actions'].append((M[:10], a['label'], 'mint', n, n * price / 1e18, stm[0], str(stm[1])[:90]))
                elif n > 0 and b - n * price - GAS < KEEP:
                    out['blocked'].append((M[:10], a['label'], 'low_bnb', b / 1e18))
            except Exception as ex: out['blocked'].append((M[:10], a['label'], 'mint_err', str(ex)[:80]))
            # B 档（mintPool，不退押金）：2026-11-05 02:37 用户「后续每天打满 a和b」。只限 AGC 合约；奖励按现价报价 ≥2 倍成本才打（较窄读法，防阶段减半后亏）
            if M == '0x6288dfe86f22b53eeb6886ae2d78941de7483743':
                try:
                    bLeft = w[6]; bRew = w[9]
                    b = int(rpc('eth_getBalance', [a['bsc'], 'latest']), 16)
                    nb = min(bLeft, max(0, (b - GAS * 10 - KEEP) // price))
                    if opened and not paused and nb > 0:
                        RT = '0x10ED43C718714eb63d5aA57B78B54704E256024E'; WB = 'bb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c'; TK = '63dea5acdbe1f27c968907d08610a5f0a0284ba6'
                        gao = '0x' + sel('getAmountsOut(uint256,address[])')
                        rv = int(call(RT, gao + u(bRew * nb) + u(64) + u(2) + TK.rjust(64, '0') + WB.rjust(64, '0'))[-64:], 16)
                        if rv < 2 * nb * price:
                            out['blocked'].append((M[:10], a['label'], 'b_low_reward', rv / 1e18, nb * price / 1e18))
                        else:
                            mo = int(call(RT, gao + u(nb * price // 2) + u(64) + u(2) + WB.rjust(64, '0') + TK.rjust(64, '0'))[-64:], 16) * 90 // 100
                            ref = '0' * 64 if a is MAINA else MAINA['bsc'][2:].lower().rjust(64, '0')
                            stb = go(a, M, '0x' + sel('mintPool(uint256,address,uint256)') + u(nb) + ref + u(mo), value=str(nb * price))
                            out['actions'].append((M[:10], a['label'], 'mintPool_B', nb, nb * price / 1e18, stb[0], str(stb[1])[:90]))
                            if nb < bLeft: out['blocked'].append((M[:10], a['label'], 'b_low_bnb', b / 1e18, bLeft - nb))
                    elif bLeft > 0 and nb < bLeft:
                        out['blocked'].append((M[:10], a['label'], 'b_low_bnb', b / 1e18, bLeft))
                except Exception as ex: out['blocked'].append((M[:10], a['label'], 'b_err', str(ex)[:80]))
        st[key] = e
json.dump(st, open(ST, 'w')); json.dump(out, open(OUT, 'w'), ensure_ascii=False)
if out['actions']: print(json.dumps(out, ensure_ascii=False))
# 2026-11-05 漏洞修复：余额不够打满 A/B（或 preview 报余额不足）时立刻补 BNB 到 30U（用户 11-01 19:34 授权），下一轮（约 60 秒）自动补打；同时记日志并让巡逻在「⚠️需处理」提醒
_low = [x for x in out['blocked'] if x[2] in ('low_bnb', 'b_low_bnb')] + [x for x in out['actions'] if 'Insufficient native' in str(x)]
if _low and not DRY:
    try:
        open('discover_out/agc_lowbnb.log', 'a').write(time.strftime('%m-%d %H:%M ', time.gmtime(time.time() + 8 * 3600)) + json.dumps(_low, ensure_ascii=False) + '\n')
        subprocess.Popen(['bash', '-c', 'flock -n /tmp/gastopup.lock timeout 200 python3 gas_topup.py >> discover_out/gas_topup.log 2>&1'], cwd=D, start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception: pass
# 2026-11-06 用户「彻底解决卡单」：本轮没有新起卖单时跑扫尾，链上还有 AGC（≥0.3U）就补卖，保证不留残余
if not any('sell_spawned' in str(x[2]) for x in out['actions']):
    subprocess.Popen(['bash', '-c', 'timeout 90 python3 agc_sweep.py >> discover_out/agc_sweep.log 2>&1'], cwd=D, start_new_session=True)
