#!/usr/bin/env python3
"""Jager 模板 fair mint 自动只读审计（2026-11-01 安小将教训：从 0 冲到开池只要 4 分钟，等 Agent 手工审计来不及）。
只读，不交易。只给 template=jager 的盘写 audit_ok.json（autofill 只支持 Jager）。
通过条件（与人工审计同口径，一条没放宽）：
  1. mint 合约与代币合约源码均已验证（verify_source.py，Sourcify/BscScan，含字节码指纹一致）
  2. mint 合约、代币合约都不可升级（codeutil.upgradeable）
  3. 国库抽成 treasury_bps ≤ 3000（≤30%，2026-11-03 01:17 用户「那就改成30%」）
  4. 代币 minter() == mint 合约
  5. 币安 query-token-audit：hasResult 且 isSupported，风险不是 HIGH
  6. riskcheck.py 不判为貔貅（honeypot != True）、不可升级
储备实测、Agent ID 铸造钱包 ≥100、部署者批量发盘、额度等闸门仍由 autofill.py 在买入时再查。

2026-11-02「读失败 ≠ 不合格」（BNBbaby 0xbb7e…2637 多轮因 RPC 限流没审完；BscAgentID 审计超时没有结论也不再重审）：
  - 每项检查结果分三类：通过 / 不合格（bad，链上明确读到不满足）/ 读失败（unknown：超时、429、节点错误、查询失败）。
  - 每项读失败都先换节点重试（3 次，退避）；--priority（进度 ≥60% 的盘）重试 5 次、更长超时。
  - 有 bad → 写 audit_ok.json ok=false（和以前一样）。
  - 没有 bad 但有 unknown → 不写 audit_ok.json（不进「不通过」缓存），写 audit_pending.json，fastprogress 常驻 60 秒内重审。
  - 全部通过 → ok=true。
  - 国库抽成：found.json 没有就链上直接读 TREASURY_BPS()；节点失败=unknown；函数不存在（revert）=无法确认抽成=不合格（规则不放宽）。
  - 审计用独立的 RPC 冷却表（RPCPOOL_COOL=/tmp/rpcpool_cool_audit.json），不被巡逻其它步骤拉黑的节点拖累（单独限流预算）。
非 Jager 模板（如 jagent 的 BNBbaby）：用 --report 跑同样的通用检查，结论只写 fairmint/audit_report.json（供 60% 提醒简报），
  不写 audit_ok.json（autofill 本就不支持这些模板，不会因此自动买）。
用法：python3 autoaudit.py [--priority] [--report] <mint合约> [<mint合约>...]
"""
import json, os, subprocess, sys, time, uuid, urllib.request, fcntl
os.environ.setdefault('RPCPOOL_COOL', '/tmp/rpcpool_cool_audit.json')
D = os.path.dirname(os.path.abspath(__file__)); L = os.path.dirname(D)
sys.path.append('$AIDBOT_HOME/pylib'); sys.path.insert(0, L)
import codeutil as CU
from rpcpool import rpc, call_strict
AOK = f'{D}/audit_ok.json'; FOUND = f'{D}/found.json'; PEND = f'{D}/audit_pending.json'; REP = f'{D}/audit_report.json'
PRIO = '--priority' in sys.argv; REPORT = '--report' in sys.argv
TRIES = 5 if PRIO else 3


def load(p, d):
    try: return json.load(open(p))
    except Exception: return d


def save_locked(path, key, val, cond=lambda old: True):
    with open(path + '.lock', 'w') as lk:
        fcntl.flock(lk, fcntl.LOCK_EX); a = load(path, {})
        if cond(a.get(key)):
            if val is None: a.pop(key, None)
            else: a[key] = val
            json.dump(a, open(path + '.tmp', 'w'), ensure_ascii=False, indent=1); os.replace(path + '.tmp', path)


def backoff(i): time.sleep(min(8, 1.5 * 2 ** i))


def strict(to, sig):
    """('ok', hex) / ('revert', err) / ('unknown', err)，节点失败重试 TRIES 次。"""
    data = '0x' + CU.kec(sig.encode())[:4].hex(); r = None
    for i in range(TRIES):
        r = call_strict(to, data, timeout=10 if PRIO else 8)
        if r[0] != 'unknown': return r
        backoff(i)
    return r


def binance_audit(tok):
    """返回 (data, None) 或 (None, 错误)。网络失败 = unknown。"""
    last = None
    for i in range(TRIES):
        body = json.dumps({'binanceChainId': '56', 'contractAddress': tok, 'requestId': str(uuid.uuid4())}).encode()
        h = {'Content-Type': 'application/json', 'source': 'agent', 'Accept-Encoding': 'identity', 'User-Agent': 'binance-web3/1.4 (Skill)'}
        try:
            r = json.load(urllib.request.urlopen(urllib.request.Request('https://web3.binance.com/bapi/defi/v1/public/wallet-direct/security/token/audit', body, h), timeout=15))
            return (r.get('data') or {}), None
        except Exception as e: last = str(e)[:120]; backoff(i)
    return None, last


def verified(a):
    """True / False / None(查询失败，已重试)。"""
    v = None
    for i in range(TRIES):
        try:
            r = subprocess.run(['python3', f'{L}/verify_source.py', a], capture_output=True, text=True, timeout=60)
            v = json.loads(r.stdout.strip().splitlines()[-1]).get('verified')
        except Exception: v = None
        if v is not None: return v
        backoff(i)
    return None


def upgradeable(a):
    r = (None, '')
    for i in range(TRIES):
        r = CU.upgradeable(rpc, a)
        if r[0] is not None: return r
        backoff(i)
    return r


def src_treasury_bps(a):
    """2026-11-03 用户指出 Burn Agent 有比例只是没 TREASURY_BPS() getter：从已验证源码读进池比例（如 lp = price * 70 / 100），国库 = 其余。
    返回 (bps 或 None, 说明)。读不到/多个不同比例 → None（照旧不合格或未知）。"""
    import re
    try:
        d = json.loads(urllib.request.urlopen(f'https://sourcify.dev/server/v2/contract/56/{a}?fields=sources', timeout=25).read())
    except Exception as ex: return None, f'源码拉取失败 {str(ex)[:40]}'
    pcts = set()
    for k, v in (d.get('sources') or {}).items():
        if '@openzeppelin' in k or '/utils/' in k: continue
        for m_ in re.finditer(r'\b(?:lp|liq\w*|toPool|poolAmt|reserve\w*)\s*=\s*[^;]*?\*\s*(\d+)\s*/\s*(100|1000|10000|10_000)\b', v['content'], re.I):
            pcts.add(int(m_.group(1)) * 10000 // int(m_.group(2).replace('_', '')))
    if len(pcts) == 1:
        lp = pcts.pop(); return 10000 - lp, f'源码进池 {lp/100:.0f}%'
    return None, f'源码比例 {sorted(pcts)}' if pcts else '源码未找到进池比例'


def audit(m):
    m = m.lower(); f = (load(FOUND, {}).get(m) or {}); bad, unk = [], []; minter_ok = False
    tpl = f.get('template')
    if tpl != 'jager' and not REPORT: return {'ok': False, 'note': f"模板 {tpl}，不是 Jager，自动审计不覆盖，需人工（可用 --report 出通用审计）"}
    tok = (f.get('token') or '').lower()
    if not tok:
        st, r = strict(m, 'token()')
        if st == 'ok' and r and len(r) >= 66 and int(r, 16): tok = '0x' + r[-40:]
        elif st == 'unknown': unk.append(f'代币地址读取失败（{r[:60]}）')
        else:
            st2, r2 = strict(m, 'totalSupply()')
            if st2 == 'ok' and r2 and len(r2) >= 66: tok = m  # mint 合约本身就是代币（如 Burn Agent）
            else: bad.append('合约没有 token()，读不到代币地址')
    for a, nm in ((m, 'mint'), (tok, '代币')):
        if not a: continue
        v = verified(a)
        if v is False: bad.append(f'{nm}源码未验证')
        elif v is None: unk.append(f'{nm}源码验证查询失败')
        u, why = upgradeable(a)
        if u is True: bad.append(f'{nm}可升级（{why}）')
        elif u is None: unk.append(f'{nm}可升级检查读取失败（{why[:60]}）')
    tb = f.get('treasury_bps')
    if tpl == 'jager' or tb is not None or REPORT:
        if tb is None:
            st, r = strict(m, 'TREASURY_BPS()')
            if st == 'ok' and r and len(r) >= 66: tb = int(r[:66], 16)
            elif st == 'unknown': unk.append(f'国库抽成 TREASURY_BPS() 读取失败（{r[:60]}）')
            else:
                tb, why = src_treasury_bps(m)
                if tb is None and tpl == 'jager': bad.append(f'合约没有 TREASURY_BPS()，{why}，无法确认国库抽成 ≤30%')
        if tb is not None and tb > 3000: bad.append(f'国库抽成 {tb} bps 超过 30%')
    if tok and tok == m:
        minter_ok = True  # 代币就是 mint 合约本身，增发只在本合约内（源码已验证、不可升级另查）
    if tok:
        st, r = strict(tok, 'minter()') if tok != m else ('skip', '')
        if st == 'ok' and r and len(r) >= 66:
            mn = '0x' + r[-40:]
            if mn.lower() != m: bad.append(f'代币 minter {mn[:8]}… 不是 mint 合约')
            else: minter_ok = True
        elif st == 'skip': pass
        elif st == 'unknown': unk.append(f'代币 minter() 读取失败（{r[:60]}）')
        else:
            if tpl == 'jager': bad.append('代币没有 minter()，无法确认增发权只在 mint 合约')
            else: minter_ok = None
        ba, err = binance_audit(tok); lvl = None
        if ba is None: unk.append(f'币安审计接口请求失败（{err[:60]}）')
        elif not ba.get('hasResult') or not ba.get('isSupported'): bad.append('币安审计无结果')
        else:
            lvl = ba.get('riskLevelEnum') or f"riskLevel {ba.get('riskLevel')}"
            if ba.get('riskLevelEnum') == 'HIGH' or (ba.get('riskLevel') or 0) >= 4: bad.append(f'币安审计高风险（{lvl}）')
        rc = None
        for i in range(TRIES):
            try:
                rc = json.loads(subprocess.run(['python3', f'{L}/riskcheck.py', tok, '--mint', m], capture_output=True, text=True, timeout=90).stdout.strip().splitlines()[-1]); break
            except Exception: backoff(i)
        if rc is None: unk.append('风险速查 riskcheck 读取失败')
        else:
            if rc.get('honeypot') is True: bad.append('疑似貔貅')
            if rc.get('upgradeable_proxy') or (rc.get('mint_contract') or {}).get('upgradeable_proxy'): bad.append('合约可升级')
            if rc.get('read_errors'): unk.append('风险速查部分读取失败：' + '；'.join(rc['read_errors'])[:120])
    else: lvl = None
    if bad: status, note = 'fail', '自动审计不通过：' + '；'.join(bad) + (('（另有读取失败：' + '；'.join(unk) + '）') if unk else '')
    elif unk: status, note = 'unknown', '审计未完成（读取失败，不算不通过，60 秒内重审）：' + '；'.join(unk)
    else:
        tbs = f'国库 {tb/100:.0f}%' if tb is not None else '合约无 TREASURY_BPS()（非 Jager 模板，资金去向需看源码）'
        ms = 'minter=mint 合约' if minter_ok else '代币无 minter()（非 Jager 模板，增发权需看源码）'
        status, note = 'pass', f'{"通用审计" if tpl != "jager" else "自动审计"}通过：mint/代币源码已验证、不可升级、{tbs}、{ms}、币安审计 {lvl}'
    return {'ok': status == 'pass', 'status': status, 'token': tok, 'note': note, 'auto': True, 'bad': bad, 'unknown': unk, 'template': tpl}


def main():
    res = {}
    now = lambda: time.strftime('%Y-%m-%d %H:%M CST', time.gmtime(time.time() + 8 * 3600))
    for m in [x.lower() for x in sys.argv[1:] if x.startswith('0x')]:
        _e = load(AOK, {}).get(m)
        if not REPORT and _e and not (_e.get('auto') and not _e.get('ok')): res[m] = 'exists'; continue  # 只允许重审之前「自动不通过」的；人工结论和通过结论不动
        r = audit(m); r['time'] = now(); r['priority'] = PRIO
        if REPORT or r.get('template') != 'jager':
            save_locked(REP, m, r)  # 非 Jager：只写通用审计报告，不进 audit_ok（autofill 不支持这些模板）
        elif r['status'] == 'unknown':
            pe = load(PEND, {}).get(m) or {}
            save_locked(PEND, m, {'note': r['note'], 'unknown': r['unknown'], 'time': r['time'], 't': time.time(), 'n': pe.get('n', 0) + 1})
        else:
            w = {k: r[k] for k in ('ok', 'token', 'note', 'auto', 'time')}
            save_locked(AOK, m, w, cond=lambda old: old is None or (old.get('auto') and not old.get('ok')))
            save_locked(PEND, m, None)
        res[m] = r
    print(json.dumps(res, ensure_ascii=False))


if __name__ == '__main__': main()
