#!/usr/bin/env python3
"""只读：从巡逻各输出里抓新合约地址，识别「报价型 mint」（FLOKI 模板：StaleQuote 0xb915daf5 / mint 0xae3dfa28 / mintWithInviter 0x760b22a2），
读进度与单价，新发现或进度变化 ≥10% 输出 alerts（每盘首次必报）。状态 quote_watch_state.json。"""
import sys, os, re, json, glob, time
sys.path.insert(0, '$AIDBOT_HOME/bsc-mint-skill'); import chain
LM = '$AIDBOT_DATA'; ST = '$AIDBOT_HOME/quote-racer/quote_watch_state.json'
st = json.load(open(ST)) if os.path.exists(ST) else {'checked': {}, 'hits': {}}
SIGS = ('b915daf5', 'ae3dfa28', '760b22a2')
files = glob.glob(LM + '/discover_out/*.json') + glob.glob(LM + '/discover_out/*.jsonl') + glob.glob(LM + '/chainscan/*.json') + glob.glob(LM + '/fairmint/*.json')
addrs = set()
now = time.time()
for f in files:
    try:
        if now - os.path.getmtime(f) > 3 * 3600: continue
        addrs |= {a.lower() for a in re.findall(r'0x[0-9a-fA-F]{40}', open(f, errors='ignore').read()[-400000:])}
    except Exception: pass
new = [a for a in addrs if a not in st['checked'] and a not in st['hits']][:300]
def code_of(a):
    c = chain.rpc('eth_getCode', [a, 'latest'])
    if len(c) < 400 and '360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc' in c:
        impl = '0x' + chain.rpc('eth_getStorageAt', [a, '0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc', 'latest'])[-40:]
        c = chain.rpc('eth_getCode', [impl, 'latest'])
    return c
for a in new:
    try:
        c = code_of(a)
        st['checked'][a] = int(now)
        if len(c) > 200 and sum(s in c for s in SIGS) >= 2: st['hits'][a] = {'first': int(now), 'last_pct': -100}
    except Exception: pass
alerts = []
# 2026-11-08 22:44 用户「要」+「按照这次来」：源码已验证的报价型盘发现即自动六号开抢；每号每项目 ≤0.03 BNB（=FLOKI 这次每号 10 份×0.003），
# 且受每号每项目 30U、每号每天 100U 额度约束；未验证的只提醒（须用户「破例 + 项目」）。
sys.path.insert(0, LM); import quota as Q, subprocess
AUTO_BNB = 0.03; LBLS = ['主', '001', '002', '海贼王', '蜡笔小新', '高']
def bnbu():
    W = '0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c'; USDT = '0x55d398326f99059ff775485246999027b3197955'
    ok, v = chain.call('0x10ed43c718714eb63d5aa57b78b54704e256024e', '0xd06ca61f' + chain.enc_uint(10**18) + chain.enc_uint(64) + chain.enc_uint(2) + W[2:].rjust(64, '0') + USDT[2:].rjust(64, '0'))
    return int(v[-64:], 16) / 1e18 if ok else None
def verified(a):
    try:
        vs = json.loads(subprocess.run(['python3', LM + '/verify_source.py', a], capture_output=True, text=True, timeout=60).stdout.splitlines()[0])
        return bool(vs.get('verified')) and not (vs.get('proxy_impl') and str(vs.get('impl_verified')) != 'True'), vs.get('source')
    except Exception as ex: return None, str(ex)[:80]
def auto_launch(a, nm, pr):
    if not pr: return '单价读不出，未自动抢'
    v, src = verified(a)
    if v is None: return f'源码验证查询失败（{src}），未自动抢，回「打 {nm}」可手动'
    if not v: return f'源码未验证，未自动抢；要打请回「破例 打 {nm}」'
    pu = bnbu()
    if not pu: return 'BNB 价格读不出，未自动抢'
    bud = Q.budget(a); qm = {}
    for l in LBLS:
        cap_bnb = min(AUTO_BNB, bud.get(l, 0) * 0.97 / pu)
        qm[l] = int(cap_bnb / (pr / 1e18) + 1e-9)
    if not any(qm.values()): return '六号额度已满（每项目 30U / 每天 100U），未自动抢'
    labs = [l for l in LBLS if qm[l] > 0]; main = '主' if qm.get('主') else labs[0]
    cmd = ['python3', '$AIDBOT_HOME/quote-racer/racer.py', '--to', a, '--price', str(pr / 1e18), '--qtymap', json.dumps(qm, ensure_ascii=False), '--bnbu', str(pu), '--auto', '--secs', '600',
           '--ok', '2026-11-08 22:44 用户预授权「要」「按照这次来」：已验证报价型盘自动抢，每号 ≤0.03 BNB'] + labs
    subprocess.Popen(cmd, stdout=open('$AIDBOT_HOME/quote-racer/auto_launch.log', 'a'), stderr=subprocess.STDOUT, start_new_session=True)
    return '已自动六号开抢（每号份数 ' + '、'.join(f'{l} {qm[l]}' for l in LBLS) + '），成交另报'
def rd(a, sig):
    ok, v = chain.call(a, chain.sel(sig));
    return int(v[:66], 16) if ok and v and v != '0x' else None
for a, h in st['hits'].items():
    tm = rd(a, 'totalMinted()'); cap = rd(a, 'maxSupply()') or rd(a, 'MAX_SUPPLY()') or rd(a, 'maxSupply()') or rd(a, 'cap()') or 10000
    pr = rd(a, 'BASE_PRICE()') or rd(a, 'PRICE()') or rd(a, 'price()') or rd(a, 'mintPrice()'); op = rd(a, 'opened()')
    try: nm = bytes.fromhex(chain.call(a, chain.sel('name()'))[1][130:]).rstrip(b'\0').decode(errors='ignore') or '未命名'
    except Exception: nm = '未命名'
    if tm is None: continue
    pct = round(100 * tm / cap, 1) if cap else None
    done = (cap and tm >= cap) or op == 1
    if done: h['done'] = True; continue
    note = ''
    if now - h.get('auto_tried', 0) > 620:  # 未开售时 racer 10 分钟超时，下轮再拉起；额度与合约 quota 兜底不会多买
        h['auto_tried'] = int(now); note = auto_launch(a, nm, pr)
        if note == h.get('auto_note') and not note.startswith('已自动'): note = ''  # 同样的「未自动抢」原因只报一次
        else: h['auto_note'] = note
    if note or h.get('last_pct', -100) == -100 or (pct is not None and pct - h['last_pct'] >= 10):
        alerts.append(f"{nm}｜{a[:6]}…{a[-4:]}｜报价型 mint（六号并发抢跑可用）｜进度 {pct}%（{tm}/{cap}）｜单价 {pr/1e18 if pr else '读不到'} BNB｜{note or ('回复「打 ' + nm + '」即六号开抢')}｜https://bscscan.com/address/{a}")
        h['last_pct'] = pct
# 自动抢的成交/回滚结果：读 race_*.log 新增行，进 alerts 单发
for lf in glob.glob('$AIDBOT_HOME/quote-racer/race_*.log'):
    off = st.setdefault('log_off', {}).get(lf, os.path.getsize(lf) if lf not in st['log_off'] else 0)
    with open(lf) as fh:
        fh.seek(off); new_lines = fh.read(); st['log_off'][lf] = fh.tell()
    for ln in new_lines.splitlines():
        try: j = json.loads(ln)
        except Exception: continue
        if j.get('state') in ('ok', 'reverted', 'unknown'):
            alerts.append(f"报价型自动抢｜{lf.split('race_')[1][:10]}…｜{j['acc']}｜{ {'ok':'成交','reverted':'链上回滚','unknown':'结果不确定（不重发）'}[j['state']] } {j.get('qty')} 份｜https://bscscan.com/tx/{j.get('tx')}")
st['checked'] = dict(list(st['checked'].items())[-20000:])
json.dump(st, open(ST, 'w'))
print(json.dumps({'scanned': len(new), 'hits': len(st['hits']), 'alerts': alerts}, ensure_ascii=False))
