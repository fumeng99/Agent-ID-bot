#!/usr/bin/env python3
"""未验证但很热的免费盘（2026-11-01 20:47 用户采纳）。只读：查源码验证、读 tx value，不签名、不交易、不铸造。
来源：chainscan/candidates.json（free_like=true 或 样本 tx value 全为 0）且 wallets_30m ≥ 50，
verify_source.py 结论 verified=false（含字节码指纹匹配仍不中；verified=null 查询失败的不报，下轮重查）。
每个项目只提一次（hotfree_alerted.json）；巡逻放「⚠️ 需处理」单独一段，问「要破例铸请回复：破例铸 + 项目」。
用户回复前绝不铸造（规则 B）。
每轮最多报 MAXN=5 个（按 30 分钟钱包数降序，其余下轮再报）；只报 found_at_cst 在 FRESH_MIN=90 分钟内的候选
（首次运行 2026-11-01 20:5x 已把当时的存量记为 baseline，不补报）。验证结果优先用 verify_cache.json（30 分钟内），其余每轮最多新查 10 个。
用法：python3 hotfree_unverified.py [--min 50] [--dry]（--dry 不记已提醒）
"""
import json, os, subprocess, sys, time
MAXN = 5; FRESH_MIN = 90
D = os.path.dirname(os.path.abspath(__file__)); ST = os.path.join(D, 'hotfree_alerted.json')
MIN = int(sys.argv[sys.argv.index('--min') + 1]) if '--min' in sys.argv else 50; DRY = '--dry' in sys.argv


def load(p, d):
    try: return json.load(open(p))
    except Exception: return d


def short(a): return a[:6] + '…' + a[-4:] if a else ''


cands = load(os.path.join(D, 'chainscan', 'candidates.json'), [])
st = load(ST, {}); out = {'alerts': [], 'lines': [], 'already': 0, 'verify_failed': []}
pick = {}
for c in cands if isinstance(cands, list) else []:
    vals = c.get('tx_values_wei') or []
    free = c.get('free_like') or (vals and max(vals) == 0)
    tgt = (c.get('mint_target') or c.get('token') or '').lower()
    if not free or (c.get('wallets_30m') or 0) < MIN or not tgt: continue
    if tgt in st: out['already'] += 1; continue
    try: age = (time.time() + 8 * 3600 - time.mktime(time.strptime(c.get('found_at_cst', ''), '%Y-%m-%d %H:%M')) + time.timezone) / 60
    except Exception: age = 0
    if age > FRESH_MIN: continue
    if tgt not in pick or (c.get('wallets_30m') or 0) > (pick[tgt].get('wallets_30m') or 0): pick[tgt] = c
if pick:
    pick = dict(sorted(pick.items(), key=lambda kv: -(kv[1].get('wallets_30m') or 0)))
    vc = load(os.path.join(D, 'verify_cache.json'), {}); vs = {}
    for t in pick:
        e = vc.get(t)
        if e and (e.get('verified') or time.time() - e.get('t', 0) < 1800) and e.get('verified') is not None: vs[t] = e
    todo = [t for t in pick if t not in vs][:10]
    if todo:
        try:
            r = subprocess.run(['python3', os.path.join(D, 'verify_source.py')] + todo, capture_output=True, text=True, timeout=90)
            for ln in r.stdout.splitlines():
                try: j = json.loads(ln); vs[j['addr']] = j
                except Exception: pass
        except subprocess.TimeoutExpired: out['verify_timeout'] = True
    for tgt, c in pick.items():
        if len(out['alerts']) >= MAXN: out['more_next_round'] = True; break
        v = vs.get(tgt)
        if not v or v.get('verified') is None: out['verify_failed'].append(tgt); continue
        if v.get('verified'): continue  # 已验证（含字节码指纹匹配）→ 走正常免费流程，不在这里报
        why = '源码未验证' + ('（代理实现合约未验证）' if v.get('proxy_impl') and not v.get('impl_verified') else '') + '，规则 B 跳过'
        vals = c.get('tx_values_wei') or []
        a = {'name': c.get('name') or c.get('symbol') or '未命名', 'symbol': c.get('symbol'), 'contract': tgt, 'token': c.get('token'),
             'wallets_30m': c.get('wallets_30m'), 'sample_tx_value_bnb': (max(vals) / 1e18 if vals else None), 'function': c.get('selector_name'),
             'sample_tx': (c.get('sample_txs') or [None])[0], 'reason': why, 'risk_flags': c.get('risk_flags') or []}
        out['alerts'].append(a)
        out['lines'].append(f"{a['name']}（{a['symbol'] or '-'}）｜30 分钟 {a['wallets_30m']} 个钱包在铸｜样本 tx value {a['sample_tx_value_bnb'] if a['sample_tx_value_bnb'] is not None else '?'} BNB｜{a['function'] or '?'}｜{why}"
                            + (f"｜风险标记 {','.join(a['risk_flags'])}" if a['risk_flags'] else '') + f"｜https://bscscan.com/address/{tgt}")
        if not DRY: st[tgt] = {'name': a['name'], 'alerted_cst': c.get('found_at_cst')}
if out['lines']: out['ask'] = '要破例铸请回复：破例铸 + 项目'
if not DRY: json.dump(st, open(ST + '.tmp', 'w'), ensure_ascii=False, indent=1); os.replace(ST + '.tmp', ST)
print(json.dumps(out, ensure_ascii=False, indent=1))
