import sys as _s; _s.path.append('$AIDBOT_HOME/pylib')
# 【唯一付费自动买入口】2026-11-03 03:21 用户原话「是都要等60%才对 全部改统一规则 适配全部项目」（合并 02:17 / 02:36 / 02:39 覆盖条）：
#   任何付费项目，不论模板（jager/AGI 克隆、jagent、boss、mao、burnmix、boltbook、newtpl 识别的新模板…），销售/开池进度 ≥60%（合约读数，非猜测）
#   且安全闸门全过 → 全天自动买；不限单笔；每号每项目累计 ≤30U（含 gas，跨天累计）；每号每天所有自动付费合计 ≤100U。
#   读不出进度（无上限/无开池份数，或已开池的二级市场买入）→ 不自动买，只出简报。普通付费「过闸即买」的独立路径已作废。
# 闸门（一条不放宽）：源码验证（含字节码指纹）、mint/代币不可升级（代理须可解析且实现已验证）、项目方不能提走资金（newtpl 特权提币=reject）、
#   国库 ≤30%、储备 ≥65% 或有退款、Agent ID 铸造钱包 ≥100、单地址铸造事件 ≤30%、audit/riskcheck 无高危（jager=audit_ok，其他=audit_report pass）、
#   税 ≤10%（newtpl 代币检查）、未开池、部署者 24h 批量发盘 <3、每号 preview 干净才 execute、精确授权成交后归零、钱包留 ≥0.003 BNB、
#   结果不确定不重发、任何读失败 = unknown（本轮不买，60 秒内重读）。
# 执行器按模板分派：burnmix_buy.py（BurnAgentMixed）/ fair_buy.py（Jager/AGI：quotaOf+costOf+mint(5 参)）/ bnb_fair_buy.py（mintWithBnb(uint256,address,uint256)）/
#   generic_buy.py（其余：单参数份数入口，BNB 或 USDT）；都生成不了安全 calldata → 跳过并写原因。
# BNB 计价盘 BNB 不够时按 2026-11-01 02:45 授权用该号 USDT 换够（满额 + 0.0045 BNB 余量），换不够只提醒补。
# usage: python3 autofill.py [--dry] [--only 0x合约]
import sys, os, json, time, subprocess, re
D = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, D)
from rpcpool import batch, rpc as _rpc
import codeutil as CU
import quota as Q
DRY = '--dry' in sys.argv
ONLY = (sys.argv[sys.argv.index('--only') + 1].lower() if '--only' in sys.argv else None)
DAILY_CAP, PROJECT_CAP, GAS_MARGIN = Q.DAILY_CAP, Q.PROJECT_CAP, 0.97
import fcntl; _LK = open(f'{D}/autofill.lock', 'w')
if not DRY: fcntl.flock(_LK, fcntl.LOCK_EX)  # 快速进度/newtpl/巡逻都会触发本脚本：加锁防并发重复买（dry 不加锁，不挡真实触发）


def load(p, d):
    try: return json.load(open(p))
    except Exception: return d


FM = {}
for _i in range(4):  # fairmint.json 正在被巡逻重写时可能短暂为空：重试，仍空就只看 newtpl 候选（不当作「没有 ≥60% 的盘」写结论）
    FM = load(f'{D}/discover_out/fairmint.json', {})
    if FM: break
    time.sleep(5)
FOUND = load(f'{D}/fairmint/found.json', {})
AOK = load(f'{D}/fairmint/audit_ok.json', {}); REP = load(f'{D}/fairmint/audit_report.json', {})
NT = (load(f'{D}/discover_out/newtpl.json', {}) or {}).get('contracts', {})
LED = f'{D}/autofill.json'; led = load(LED, {})
day = time.strftime('%Y-%m-%d', time.gmtime(time.time() + 8 * 3600))
ACCS = json.load(open('$AIDBOT_HOME/baw/accounts/accounts.json')); LABELS = [x['label'] for x in ACCS]
U = '0x55d398326f99059ff775485246999027b3197955'; WBNB = '0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c'
R = '0x10ed43c718714eb63d5aa57b78b54704e256024e'
r = batch([('eth_call', [{'to': R, 'data': '0xd06ca61f' + hex(10**18)[2:].rjust(64, '0') + '40'.rjust(64, '0') + '2'.rjust(64, '0') + WBNB[2:].rjust(64, '0') + U[2:].rjust(64, '0')}, 'latest'])])[0]
BNBU = int(r[-64:], 16) / 1e18
_, DAY_USED = Q.daily_used()
out = {'time': day, 'dry': DRY, 'fairmint_json_ok': bool(FM), 'bnb_u': round(BNBU, 1), 'rule': '≥60% 且闸门全过；每号每项目 ≤30U、每号每天 ≤100U；全天', 'daily_used_u': {k: round(v, 2) for k, v in DAY_USED.items()},
       'bought': [], 'skipped': [], 'brief_only': [], 'remind': [], 'gates': {}}
RECHK = f'{D}/discover_out/autofill_recheck.json'; recheck = load(RECHK, {}); _rc_seen = set()


def topup_bnb(need_bnb, labels):
    """BNB 不够的号用 USDT 换 BNB（2026-11-01 02:45 授权，只限自动打满）。"""
    A = [x for x in ACCS if x['label'] in labels]
    Rb = batch([c for x in A for c in (('eth_getBalance', [x['bsc'], 'latest']), ('eth_call', [{'to': U, 'data': '0x70a08231' + x['bsc'][2:].lower().rjust(64, '0')}, 'latest']))])
    res = []
    for i, x in enumerate(A):
        bnb = int(Rb[2*i], 16) / 1e18; usdt = int(Rb[2*i+1], 16) / 1e18
        if bnb >= need_bnb: continue
        amt = round((need_bnb - bnb) * BNBU * 1.05 + 0.3, 2)
        if usdt < amt: res.append((x['label'], 'usdt_insufficient', round(usdt, 2), amt)); continue
        env = dict(os.environ, HOME=x['home'], npm_config_cache='/tmp/npmcache')
        rr = subprocess.run(['$AIDBOT_HOME/baw/node_modules/.bin/baw', 'market-order', 'swap', '--binanceChainId', '56', '--fromTokenQty', str(amt), '--fromToken', U, '--toToken', '0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee', '--json'], env=env, capture_output=True, text=True, timeout=120)
        res.append((x['label'], 'swap', amt, rr.stdout.strip()[-160:]))
    if any(t[1] == 'swap' for t in res): time.sleep(20)
    return res


def mark_unknown(c, why):
    """返回 True = 本盘停止（真实运行）；dry 时记下继续评估其余闸门，便于一次看全。"""
    c.setdefault('fails', []).append('unknown：' + why[:160])
    e = recheck.get(c['a']) or {'first_cst': time.strftime('%H:%M:%S', time.gmtime(time.time() + 8 * 3600)), 'n': 0}
    e.update(name=c['name'], open_pct=c['open_pct'], reason=why[:300], at=time.time(), n=e.get('n', 0) + 1); recheck[c['a']] = e; _rc_seen.add(c['a'])
    out.setdefault('unknown', []).append((c['name'], c['a'], c['open_pct'], why[:300] + '｜unknown：本轮不买，60 秒内重读'))
    return not DRY


def skip(c, why):
    """返回 True = 本盘停止（真实运行）；dry 时记下继续评估其余闸门。"""
    c.setdefault('fails', []).append(why); out['skipped'].append((c['name'], c['a'], c['open_pct'], why)); return not DRY


# ---------- 候选：fairmint（found.json 模板盘）+ newtpl 识别的新模板 ----------
cands = {}
for row in FM.get('watch_unopened', []):
    a = row['contract'].lower(); v = FOUND.get(a, {})
    if v.get('opened'): continue
    c = {'a': a, 'name': row.get('name') or v.get('name') or a[:10], 'open_pct': row.get('open_pct') or 0, 'template': row.get('template') or v.get('template'), 'src': 'fairmint', 'row': row, 'v': v}
    if row.get('open_pct_guess') or row.get('progress_suspect') or (row.get('open_pct') or 0) > 100:
        if (row.get('open_pct') or 0) >= 60: out['brief_only'].append((c['name'], a, c['open_pct'], '进度读不出（开池份数是猜的/超过 100% 仍未开池），不自动买，只出简报'))
        continue
    if c['open_pct'] >= 60: cands[a] = c
for a, rr in NT.items():
    pg = (rr.get('params') or {}).get('progress')
    if a in cands or rr.get('category') not in ('paid_mint', 'fair_launch'): continue
    if not pg:
        if rr.get('conclusion') == 'pass': out['brief_only'].append((rr.get('name'), a, None, 'newtpl 识别的付费盘读不出已售/上限，不自动买，只出简报'))
        continue
    if pg.get('opened'): continue
    if (pg.get('pct') or 0) >= 60:
        cands[a] = {'a': a, 'name': rr.get('name') or a[:10], 'open_pct': pg['pct'], 'template': 'newtpl:' + str(rr.get('category')), 'src': 'newtpl', 'row': {}, 'v': {}, 'nt': rr}
if ONLY: cands = {k: v for k, v in cands.items() if k == ONLY}


def detect_executor(c):
    """('burnmix'|'fair_buy'|'bnb_fair_buy'|'generic'|None, 附加信息, 原因)。"""
    a = c['a']
    try: BM = json.loads(subprocess.run(['python3', f'{D}/burnmix_buy.py', a, '--probe'], capture_output=True, text=True, timeout=60).stdout.strip().splitlines()[-1])
    except Exception: BM = {}
    if BM.get('burnmix'): return 'burnmix', BM, ''
    code = (_rpc('eth_getCode', [a, 'latest']) or '').lower()
    impl = None
    if CU.IMPL_SLOT:
        try:
            s_ = _rpc('eth_getStorageAt', [a, CU.IMPL_SLOT, 'latest'])
            if s_ and int(s_, 16): impl = '0x' + s_[-40:]; code = (_rpc('eth_getCode', [impl, 'latest']) or '').lower()
        except Exception: pass
    has = lambda sig: CU.sel(sig) in code
    if has('quotaOf(uint256)') and has('costOf(uint256)') and has('mintedCount(uint256)') and (has('mint(uint256,address,uint256,uint256,uint256)') or has('mintWithBnb(uint256,address,uint256,uint256,uint256)')):
        return 'fair_buy', {}, ''
    if has('quotaOf(uint256)') and has('costOf(uint256)') and has('mintedCount(uint256)') and has('mintWithBnb(uint256,address,uint256)'):
        return 'bnb_fair_buy', {}, ''
    try: GP = json.loads(subprocess.run(['python3', f'{D}/generic_buy.py', a, '--probe'], capture_output=True, text=True, timeout=150).stdout.strip().splitlines()[-1])
    except Exception as ex: GP = {'supported': None, 'why': f'generic probe 异常 {str(ex)[:100]}'}
    if GP.get('supported'): return 'generic', GP, ''
    return (None if GP.get('supported') is False else 'unknown'), GP, GP.get('why') or '无可用执行器'


def retry3(fn):
    rr = None
    for k in range(3):
        rr = fn()
        if rr is not None and (not isinstance(rr, tuple) or rr[0] is not None): return rr
        time.sleep(1.5 * 2 ** k)
    return rr


for a, c in sorted(cands.items(), key=lambda kv: -kv[1]['open_pct']):
    g = out['gates'].setdefault(a, {'name': c['name'], 'open_pct': c['open_pct'], 'template': c['template']})
    if (led.get('filled') or {}).get(a) and not DRY: g['result'] = '已买过（filled）'; continue
    v = c['v']
    # ① 执行器
    ex, info, why = detect_executor(c); g['executor'] = ex or why
    if ex == 'unknown': mark_unknown(c, '执行器探测读失败：' + why); continue
    if not ex: skip(c, f"{c['template']} 模板生成不了安全 calldata：{why}"); g['result'] = 'skip'; continue
    tok = (AOK.get(a) or {}).get('token') or (REP.get(a) or {}).get('token') or v.get('token')
    if ex == 'generic':
        if info.get('opened') is True or info.get('minting_open') is False:
            if skip(c, '已开池/已停止铸造，不自动买（二级市场不在本规则内）'): continue
        if info.get('open_pct') is None: out['brief_only'].append((c['name'], a, None, '读不出已售/上限，不自动买，只出简报')); continue
        c['open_pct'] = g['open_pct'] = info['open_pct']
        if info['open_pct'] < 60: g['result'] = f"实时进度 {info['open_pct']}% <60%"; continue
        if info['open_pct'] > 100:
            if skip(c, '进度 >100% 仍未开池，开池条件读错，不自动买'): continue
    # ② 可升级（mint + 代币）
    upg = []
    for x in [a, tok]:
        if not x: continue
        upg.append((x,) + retry3(lambda: CU.upgradeable(_rpc, x)))
    bad = [f"{x[0][:6]}…{x[0][-4:]} {x[2]}" for x in upg if x[1] is True]
    g['upgradeable'] = 'bad' if bad else ('unknown' if any(x[1] is None for x in upg) else 'ok')
    if bad:
        if skip(c, '可升级合约：' + '；'.join(bad)): continue
    unk = [f"{x[0][:6]}…{x[0][-4:]} 读取失败：{x[2]}" for x in upg if x[1] is None]
    if unk:
        if mark_unknown(c, '可升级检查读不到：' + '；'.join(unk)): continue
    # ③ 部署者 24h 批量发盘
    dep = v.get('deployer')
    if dep:
        import calendar as _cal
        def _t(x):
            try: return _cal.timegm(time.strptime(x.get('first_seen_cst'), '%Y-%m-%d %H:%M')) - 8 * 3600
            except Exception: return 0
        sib = {x.get('name') for x in FOUND.values() if x.get('deployer') == dep and time.time() - _t(x) < 86400}
        g['deployer_24h_names'] = len(sib)
        if len(sib) >= 3:
            if skip(c, f'部署者 {dep[:6]}…{dep[-4:]} 24 小时内发了 {len(sib)} 个不同名字的盘，疑似批量发盘'): continue
    # ④ Agent ID 铸造钱包 ≥100、单地址 ≤30%
    try:
        sys.path.insert(0, f'{D}/fairmint'); import minters as _MN
        mr = _MN.ai_minters_retry(a, v.get('deploy_block'), budget=30, tries=3)
    except Exception as ex_: mr = {'status': 'unknown', 'reason': f'异常 {str(ex_)[:150]}'}
    if mr.get('status') != 'ok':
        if mark_unknown(c, f"Agent ID 铸造钱包数读不到：{mr.get('reason')}"): continue
    g['ai_minters'] = mr.get('ai_minters'); g['minters'] = mr.get('minters'); g['top_share'] = mr.get('top_share')
    if mr.get('status') == 'ok' and mr['ai_minters'] < 100:
        if skip(c, f"Agent ID 铸造钱包不足 100（{mr['ai_minters']} 个，共 {mr.get('minters')} 个地址）"): continue
    if mr.get('status') == 'ok' and mr.get('top_share') and mr.get('mint_events', 0) >= 20 and mr['top_share'] > 0.3 and mr.get('minters', 0) >= 2:
        if skip(c, f"单个地址占 {mr['top_share']*100:.0f}% 的铸造事件，疑似自灌进度"): continue
    # ⑤ 审计（jager=audit_ok；其他模板=通用审计 audit_report pass，没有就当场跑一次）
    if ex in ('fair_buy', 'bnb_fair_buy') and (c['template'] in ('jager', None)):
        ok = AOK.get(a)
        if not (ok and ok.get('ok')): skip(c, '未通过/未做 60% 审计（audit_ok）'); g['audit'] = 'missing/fail'; continue
        g['audit'] = 'audit_ok pass'
    else:
        rp = REP.get(a)
        if not rp or rp.get('status') == 'unknown':
            try:
                rr_ = subprocess.run(['bash', '-c', f'cd {D}/fairmint && RPCPOOL_COOL=/tmp/rpcpool_cool_audit_prio.json timeout 170 python3 autoaudit.py --priority --report {a}'], capture_output=True, text=True, timeout=180)
                rp = (json.loads(rr_.stdout.strip().splitlines()[-1]) or {}).get(a)
            except Exception: rp = None
        if not rp or rp.get('status') == 'unknown':
            if mark_unknown(c, '通用审计未完成（读失败）：' + str((rp or {}).get('note'))[:150]): continue
        rp = rp or {}; g['audit'] = str(rp.get('status')) + '：' + str(rp.get('note'))[:160]
        if rp and rp.get('status') != 'unknown' and not rp.get('ok'):
            if skip(c, '通用审计不通过：' + str(rp.get('note'))[:200]): continue
        tok = tok or rp.get('token')
    # ⑥ 源码验证（含字节码指纹）
    vs = {}
    for k in range(3):
        try: vs = json.loads(subprocess.run(['python3', f'{D}/verify_source.py', a], capture_output=True, text=True, timeout=60).stdout.splitlines()[0])
        except Exception as ex_: vs = {'verified': None, 'source': f'异常 {str(ex_)[:80]}'}
        if vs.get('verified') is not None: break
        time.sleep(2 * 2 ** k)
    g['verified'] = vs.get('verified'); g['verify_source'] = vs.get('source')
    if vs.get('verified') is None:
        if mark_unknown(c, f"源码验证查询失败：{vs.get('source')}"): continue
    if not vs.get('verified'):
        if skip(c, '源码未验证'): continue
    if vs.get('proxy_impl') and str(vs.get('impl_verified')) != 'True':
        if skip(c, '代理实现未验证/不可解析'): continue
    # ⑦ 资金安全：国库 ≤30%、储备 ≥65% 或退款、项目方不能提走资金、税 ≤10%
    if ex == 'burnmix':
        v = dict(v, pending_bnb_bnb=info.get('pending_liq_bnb'), total_paid_bnb=info.get('paid_bnb'))
    if ex == 'generic':
        ntv = info.get('newtpl') or {}
        hard = [x for x in ntv.get('risk_flags') or [] if re.search(r'提走|可升级|增发|税>10|LP 打给|< 应退|退款≠|卡住退款|>30%', x)]
        g['newtpl'] = f"{ntv.get('category')}/{ntv.get('conclusion')}" + (f"；风险 {ntv.get('risk_flags')}" if ntv.get('risk_flags') else '')
        if ntv.get('conclusion') == 'reject' or hard:
            if skip(c, '源码分析不通过：' + '；'.join(hard or ntv.get('risk_flags') or ['reject'])): continue
        if ntv.get('status') != 'analyzed':
            if mark_unknown(c, '源码分析未完成：' + str(ntv.get('why'))): continue
        tb = info.get('treasury_bps'); g['treasury_bps'] = tb
        if tb is None:
            if skip(c, '国库抽成比例读不出（无 TREASURY_BPS/LP_BPS，源码也没写死比例），无法确认 ≤30%'): continue
        if tb is not None and tb > 3000:
            if skip(c, f'国库抽成 {tb/100:.0f}% >30%'): continue
        rr_ = info.get('reserve_ratio'); g['reserve_ratio'] = rr_; g['has_refund'] = info.get('has_refund')
        if not info.get('has_refund') and (rr_ is None or rr_ < 0.65):
            if skip(c, f"没有退款函数，且合约实留储备 {info.get('held')}/{info.get('raised')}（{rr_}）未达 65%"): continue
    else:
        if c['row'].get('reserve_missing'):
            if skip(c, '募资没留在合约做开池储备（pendingBnb=0）'): continue
        pb, tp = v.get('pending_bnb_bnb'), v.get('total_paid_bnb')
        reserve_ok = bool(pb and tp and pb >= 0.65 * tp); g['reserve_ratio'] = round(pb / tp, 3) if pb and tp else None; g['has_refund'] = v.get('has_refund')
        if not v.get('has_refund') and not reserve_ok:
            if skip(c, f"没有退款函数，且开池储备未实测达标（pendingBnb={pb}，已募资={tp} BNB）"): continue
    # ⑧ 额度：每号 min(100U 日剩余, 30U 项目累计剩余)
    pu = Q.project_used(a)
    bud = {l: round(max(0.0, min(DAILY_CAP - DAY_USED.get(l, 0), PROJECT_CAP - pu.get(l, 0))), 2) for l in LABELS}
    g['project_used_u'] = pu; g['budget_u'] = bud
    accs = [l for l in LABELS if bud[l] >= 1]
    full = [l for l in LABELS if l not in accs]
    if full: out['remind'].append(f"{c['name']}：{'/'.join(full)} 已到每号每项目 30U 或当日 100U 上限，未买")
    if not accs: g['result'] = '额度用完'; continue
    # ⑨ 执行（每号 preview 干净才 execute，执行器内实现；dry 只预演）
    if ex == 'generic':
        cmd = ['python3', f'{D}/generic_buy.py', a, '--budget', json.dumps({l: bud[l] for l in accs}, ensure_ascii=False)] + (['--dry'] if DRY else [])
        per_u = None
    else:
        tu = v.get('cost_1share_token_units'); per_u = tu if tu else (v.get('cost_1share_bnb') or 0) * BNBU
        if not per_u:
            if skip(c, '读不出单价'): continue
        # 份数按全体可买号里最小预算算（统一份数 → 执行器一次并发），不超 10 份
        nsh = min(10, int(min(bud[l] for l in accs) * GAS_MARGIN // per_u))
        if nsh < 1:
            if skip(c, f'单份 {per_u:.2f}U 超过可用预算'): continue
        g['shares_each'] = nsh; g['est_u_each'] = round(per_u * nsh, 2)
        if not DRY and not tu and v.get('cost_1share_bnb'): out.setdefault('swaps', []).extend(topup_bnb(v['cost_1share_bnb'] * nsh + 0.0045, accs))
        if ex == 'burnmix':
            cmd = ['python3', f'{D}/burnmix_buy.py', a, '--max-bnb', str(round((v.get('cost_1share_bnb') or 0.002) * nsh * 1.15 + 0.001, 4))] + (['--dry'] if DRY else []) + accs
        elif ex == 'bnb_fair_buy':
            cmd = ['python3', f'{D}/bnb_fair_buy.py', a, tok or a, str(nsh), '--max', str(round((v.get('cost_1share_bnb') or 0) * 1.1, 6))] + (['--dry'] if DRY else []) + accs
        else:
            cmd = ['python3', f'{D}/fair_buy.py', a, tok or a, str(nsh), '--max', str(round(per_u * 1.1, 4))] + (['--dry'] if DRY else []) + accs
    g['gates_failed'] = c.get('fails') or []
    g['would_buy'] = not c.get('fails')
    if c.get('fails') and not DRY: continue  # 双保险：真实运行有任何闸门没过绝不执行
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=600).stdout
    lines = res.splitlines(); g['result'] = 'dry 预演' if DRY else '已执行'
    rec = {'name': c['name'], 'contract': a, 'executor': ex, 'open_pct': c['open_pct'], 'per_share_u': round(per_u, 3) if per_u else None, 'result': lines[-12:]}
    # 每号实际计入额度：generic 按每号结果；其他执行器按满额保守估算
    per_acc_u = {}
    if ex == 'generic':
        try:
            sm = json.loads(lines[-1])
            for x in sm.get('results', []):
                if x.get('state') in ('ok', 'unknown', 'reverted', 'dry_ok', 'dry_ok_approve_only') or str(x.get('approve', '')) in ('ok', 'unknown'):
                    per_acc_u[x['label']] = round(float(x.get('cost_u') or 0) + (0.05 if x.get('state') not in ('dry_ok', 'dry_ok_approve_only') else 0), 4)  # 含 gas 粗估 0.05U
            rec['per_account'] = [{k: x.get(k) for k in ('label', 'state', 'shares', 'cost_u', 'currency', 'wallet_remaining', 'approve', 'approve_reset', 'tx_url', 'detail')} for x in sm.get('results', [])]
            if any(x.get('state') in ('ok', 'reverted', 'unknown') for x in sm.get('results', [])) and sm.get('token'): rec['token'] = sm['token']
        except Exception as ex_: rec['parse_err'] = str(ex_)[:100]
    else:
        per_acc_u = {l: round(per_u * g['shares_each'], 4) for l in accs}
    rec['per_account_u'] = per_acc_u
    out['bought'].append(rec)
    if any(k in l for l in lines for k in ('insufficient', 'low_bnb')): out['remind'].append(f"{c['name']} 有账号余额不够，需要补 USDT/BNB")
    if not DRY:
        real = {l: u_ for l, u_ in per_acc_u.items() if u_ > 0} if ex == 'generic' else per_acc_u
        dl = led.setdefault(day, {'spent_u': 0, 'buys': []}); dl['spent_u'] = round(dl['spent_u'] + sum(real.values()), 2)
        dl['buys'].append({'name': c['name'], 'contract': a, 'executor': ex, 'est_u': round(sum(real.values()), 2), 'accounts': list(real), 'per_account_u': real,
                           'time': time.strftime('%H:%M', time.gmtime(time.time() + 8 * 3600))})
        led.setdefault('filled', {})[a] = True
        for l, u_ in real.items(): DAY_USED[l] = DAY_USED.get(l, 0) + u_
        json.dump(led, open(LED + '.tmp', 'w'), ensure_ascii=False, indent=1); os.replace(LED + '.tmp', LED)
        # 成交的写进 auto_buy.json positions（possell 按统一卖出规则管理）
        okl = [x['label'] for x in rec.get('per_account', []) if x.get('state') == 'ok'] if ex == 'generic' else []
        if okl and rec.get('token'):
            with open(f'{D}/auto_buy.json.lock', 'w') as _lk2:
                fcntl.flock(_lk2, fcntl.LOCK_EX); ab = load(f'{D}/auto_buy.json', {})
                cu = {x['label']: x.get('cost_u') for x in rec['per_account'] if x.get('state') == 'ok'}
                ab.setdefault('positions', []).append({'project': c['name'], 'mint': a, 'token': rec['token'], 'accounts': okl, 'cost_u_each': max(cu.values()),
                                                       'cost_u_by_account': cu, 'auto': True, 'via': 'autofill/generic', 'opened': False,
                                                       'time': time.strftime('%Y-%m-%d %H:%M', time.gmtime(time.time() + 8 * 3600))})
                json.dump(ab, open(f'{D}/auto_buy.json.tmp', 'w'), ensure_ascii=False, indent=1); os.replace(f'{D}/auto_buy.json.tmp', f'{D}/auto_buy.json')
_proc = set(cands)
for _a in list(recheck):
    if _a not in _rc_seen and (_a in _proc or time.time() - recheck[_a].get('at', 0) > 3600): recheck.pop(_a)
if not DRY: json.dump(recheck, open(RECHK + '.tmp', 'w'), ensure_ascii=False); os.replace(RECHK + '.tmp', RECHK)
out['recheck_pending'] = list(recheck)
print(json.dumps(out, ensure_ascii=False))
