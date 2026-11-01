#!/usr/bin/env python3
"""Jager 模板 fair mint 自动只读审计（2026-11-01 安小将教训：从 0 冲到开池只要 4 分钟，等 Agent 手工审计来不及）。
只读，不交易。只审 found.json 里 template=jager 的盘；任一项不满足或读不到 → ok=false（不放宽，宁可漏也不乱买）。
通过条件（与人工审计同口径）：
  1. mint 合约与代币合约源码均已验证（verify_source.py，Sourcify/BscScan）
  2. mint 合约、代币合约都不可升级（codeutil.upgradeable）
  3. 国库抽成 treasury_bps ≤ 2000（≤20%），其余进开池储备 / LP 入黑洞（Jager 模板由合约代码保证）
  4. 代币 minter() == mint 合约（增发权只在 fair mint 合约，项目方不能自己增发）
  5. 币安 query-token-audit：hasResult 且 isSupported，风险不是 HIGH
  6. riskcheck.py 不判为貔貅（honeypot != True）
储备实测（pendingBnb ≥ 已募资 75%）、Agent ID 铸造钱包 ≥100、部署者批量发盘、额度等闸门仍由 autofill.py 在买入时再查。
结论写 fairmint/audit_ok.json（auto: true）。已有结论的不覆盖（人工结论优先）。
用法：python3 autoaudit.py <mint合约> [<mint合约>...]
"""
import json, os, subprocess, sys, time, uuid, urllib.request, fcntl
D = os.path.dirname(os.path.abspath(__file__)); L = os.path.dirname(D)
sys.path.append('$AIDBOT_HOME/pylib'); sys.path.insert(0, L)
import codeutil as CU
from rpcpool import rpc
AOK = f'{D}/audit_ok.json'; FOUND = f'{D}/found.json'


def load(p, d):
    try: return json.load(open(p))
    except Exception: return d


def call(to, sig):
    return rpc('eth_call', [{'to': to, 'data': '0x' + CU.kec(sig.encode())[:4].hex()}, 'latest'])


def binance_audit(tok):
    body = json.dumps({'binanceChainId': '56', 'contractAddress': tok, 'requestId': str(uuid.uuid4())}).encode()
    h = {'Content-Type': 'application/json', 'source': 'agent', 'Accept-Encoding': 'identity', 'User-Agent': 'binance-web3/1.4 (Skill)'}
    for _ in range(2):
        try:
            r = json.load(urllib.request.urlopen(urllib.request.Request('https://web3.binance.com/bapi/defi/v1/public/wallet-direct/security/token/audit', body, h), timeout=15))
            return r.get('data') or {}
        except Exception: time.sleep(1)
    return None


def verified(a):
    try:
        r = subprocess.run(['python3', f'{L}/verify_source.py', a], capture_output=True, text=True, timeout=60)
        return json.loads(r.stdout.strip().splitlines()[-1]).get('verified')
    except Exception: return None


def audit(m):
    m = m.lower(); f = (load(FOUND, {}).get(m) or {}); bad = []
    if f.get('template') != 'jager': return {'ok': False, 'note': f"模板 {f.get('template')}，不是 Jager，自动审计不覆盖，需人工"}
    tok = (f.get('token') or '').lower()
    if not tok: return {'ok': False, 'note': '读不到代币地址'}
    for a, nm in ((m, 'mint'), (tok, '代币')):
        v = verified(a)
        if v is not True: bad.append(f'{nm}源码{"未验证" if v is False else "验证查询失败"}')
        u, why = CU.upgradeable(rpc, a)
        if u is not False: bad.append(f'{nm}可升级（{why}）' if u else f'{nm}可升级检查失败')
    tb = f.get('treasury_bps')
    if tb is None or tb > 2000: bad.append(f'国库抽成 {tb} bps 超过 20% 或读不到')
    try:
        mn = '0x' + call(tok, 'minter()')[-40:]
        if mn.lower() != m: bad.append(f'代币 minter {mn[:8]}… 不是 mint 合约')
    except Exception: bad.append('读不到代币 minter()')
    ba = binance_audit(tok); lvl = None
    if not ba or not ba.get('hasResult') or not ba.get('isSupported'): bad.append('币安审计无结果')
    else:
        lvl = ba.get('riskLevelEnum') or f"riskLevel {ba.get('riskLevel')}"
        if ba.get('riskLevelEnum') == 'HIGH' or (ba.get('riskLevel') or 0) >= 4: bad.append(f'币安审计高风险（{lvl}）')
    # 2026-11-01 18:40 改严：风险速查读不到 = 不通过（「无法确认不得自动买」），重试 3 次
    rc = None
    for _ in range(3):
        try:
            rc = json.loads(subprocess.run(['python3', f'{L}/riskcheck.py', tok, '--mint', m], capture_output=True, text=True, timeout=90).stdout.strip().splitlines()[-1]); break
        except Exception: time.sleep(2)
    if rc is None: bad.append('风险速查读不到（不确定不自动买）')
    else:
        if rc.get('honeypot') is True: bad.append('疑似貔貅')
        if rc.get('upgradeable_proxy') or (rc.get('mint_contract') or {}).get('upgradeable_proxy'): bad.append('合约可升级')
    note = ('自动审计不通过：' + '；'.join(bad)) if bad else f'自动审计通过：mint/代币源码已验证、不可升级、国库 {tb/100:.0f}%、minter=mint 合约、币安审计 {lvl}'
    return {'ok': not bad, 'token': tok, 'note': note, 'auto': True}


def main():
    res = {}
    for m in [x.lower() for x in sys.argv[1:] if x.startswith('0x')]:
        _e = load(AOK, {}).get(m)
        if _e and not (_e.get('auto') and not _e.get('ok')): res[m] = 'exists'; continue  # 只允许重审之前「自动不通过」的；人工结论和通过结论不动
        r = audit(m); r['time'] = time.strftime('%Y-%m-%d %H:%M CST', time.gmtime(time.time() + 8 * 3600))
        with open(AOK + '.lock', 'w') as lk:
            fcntl.flock(lk, fcntl.LOCK_EX); a = load(AOK, {})
            if m not in a or (a[m].get('auto') and not a[m].get('ok')):
                a[m] = r; json.dump(a, open(AOK + '.tmp', 'w'), ensure_ascii=False, indent=1); os.replace(AOK + '.tmp', AOK)
        res[m] = r
    print(json.dumps(res, ensure_ascii=False))


if __name__ == '__main__': main()
