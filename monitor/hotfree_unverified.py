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
T0 = time.time()
MAXN = 5; FRESH_MIN = 90
D = os.path.dirname(os.path.abspath(__file__)); ST = os.environ.get('HOTFREE_ST') or os.path.join(D, 'hotfree_alerted.json')  # 测试可指向临时文件
MIN = int(sys.argv[sys.argv.index('--min') + 1]) if '--min' in sys.argv else 50; DRY = '--dry' in sys.argv


def load(p, d):
    try: return json.load(open(p))
    except Exception: return d


def short(a): return a[:6] + '…' + a[-4:] if a else ''


cands = load(os.path.join(D, 'chainscan', 'candidates.json'), [])
PF = os.path.join(D, 'hotfree_pending.json'); PEND = {k: v for k, v in load(PF, {}).items() if time.time() - v.get('first', 0) < 6 * 3600}
st = load(ST, {}); out = {'alerts': [], 'lines': [], 'already': 0, 'verify_failed': []}
pick = {}
for c in cands if isinstance(cands, list) else []:
    vals = c.get('tx_values_wei') or []
    free = c.get('free_like') or (vals and max(vals) == 0)
    tgt = (c.get('mint_target') or c.get('token') or '').lower()
    if not free or not tgt: continue
    if c.get('wallets_30m') is None:  # 2026-11-02：钱包数没读到 ≠ 不热，记 unknown 下一轮再看，不默默丢
        out.setdefault('wallets_unknown', []).append(tgt); continue
    if (c.get('wallets_30m') or 0) < MIN: continue
    if tgt in st: out['already'] += 1; continue
    try: age = (time.time() + 8 * 3600 - time.mktime(time.strptime(c.get('found_at_cst', ''), '%Y-%m-%d %H:%M')) + time.timezone) / 60
    except Exception: age = 0
    if age > FRESH_MIN and tgt not in PEND: continue  # 上轮源码验证查询失败的（PEND）超出 90 分钟窗口也继续查，最多 6 小时
    if tgt not in pick or (c.get('wallets_30m') or 0) > (pick[tgt].get('wallets_30m') or 0): pick[tgt] = c
if pick:
    pick = dict(sorted(pick.items(), key=lambda kv: -(kv[1].get('wallets_30m') or 0)))
    vc = load(os.path.join(D, 'verify_cache.json'), {}); vs = {}
    for t in pick:
        e = vc.get(t)
        if e and (e.get('verified') or time.time() - e.get('t', 0) < 1800) and e.get('verified') is not None: vs[t] = e
    todo = [t for t in pick if t not in vs][:10]
    for _i in range(3):  # 2026-11-02：查询失败（verified=None）的换节点重试，最多 3 次、退避 2/4 秒
        if not todo: break
        try:
            r = subprocess.run(['python3', os.path.join(D, 'verify_source.py')] + todo, capture_output=True, text=True, timeout=90)
            for ln in r.stdout.splitlines():
                try: j = json.loads(ln); vs[j['addr']] = j
                except Exception: pass
        except subprocess.TimeoutExpired: out['verify_timeout'] = True
        todo = [t for t in todo if (vs.get(t) or {}).get('verified') is None]
        if todo and _i < 2: time.sleep(2 * 2 ** _i)
    for tgt, c in pick.items():
        if len(out['alerts']) >= MAXN: out['more_next_round'] = True; break
        v = vs.get(tgt)
        if not v or v.get('verified') is None:
            out['verify_failed'].append(tgt); PEND.setdefault(tgt, {'first': time.time(), 'name': c.get('name')})['last'] = time.time(); continue
        PEND.pop(tgt, None)
        if v.get('verified'): continue  # 已验证（含字节码指纹匹配）→ 走正常免费流程，不在这里报
        why = '源码未验证' + ('（代理实现合约未验证）' if v.get('proxy_impl') and not v.get('impl_verified') else '') + '，规则 B 跳过'
        vals = c.get('tx_values_wei') or []
        a = {'name': c.get('name') or c.get('symbol') or '未命名', 'symbol': c.get('symbol'), 'contract': tgt, 'token': c.get('token'),
             'wallets_30m': c.get('wallets_30m'), 'sample_tx_value_bnb': (max(vals) / 1e18 if vals else None), 'function': c.get('selector_name'),
             'sample_tx': (c.get('sample_txs') or [None])[0], 'reason': why, 'risk_flags': c.get('risk_flags') or [], 'found_at_cst': c.get('found_at_cst')}
        out['alerts'].append(a)
    # 2026-11-02 14:33 用户要求：每行显示铸造进度（已铸/总量、%）和近 30 分钟增速；读不到写「进度读不到」，不省略（hotprogress.py，只读）
    PG = {}
    if out['alerts']:
        from concurrent.futures import ThreadPoolExecutor, wait
        import hotprogress as HP
        ex = ThreadPoolExecutor(5); fs = {ex.submit(HP.progress, a['contract']): a['contract'] for a in out['alerts']}
        done, _ = wait(fs, timeout=max(10, min(40, 92 - (time.time() - T0))))
        for f in done:
            try: PG[fs[f]] = f.result()
            except Exception as e: PG[fs[f]] = {'ok': False, 'text': f'进度读不到（{str(e)[:40]}）'}
        ex.shutdown(wait=False, cancel_futures=True)
    for a in out['alerts']:
        pg = PG.get(a['contract']) or {'ok': False, 'text': '进度读不到（读取超时，下轮再读）'}
        a['progress'] = {k: pg.get(k) for k in ('ok', 'minted', 'cap', 'pct', 'rate30', 'minted_src', 'cap_src', 'text')}
        tgt = a['contract']; why = a['reason']
        out['lines'].append(f"{a['name']}（{a['symbol'] or '-'}）｜{pg['text']}｜30 分钟 {a['wallets_30m']} 个钱包在铸｜样本 tx value {a['sample_tx_value_bnb'] if a['sample_tx_value_bnb'] is not None else '?'} BNB｜{a['function'] or '?'}｜{why}"
                            + (f"｜风险标记 {','.join(a['risk_flags'])}" if a['risk_flags'] else '') + f"｜https://bscscan.com/address/{tgt}")
        if not DRY: st[tgt] = {'name': a['name'], 'alerted_cst': a.get('found_at_cst')}
if out['lines']: out['ask'] = '要破例铸请回复：破例铸 + 项目'
if not DRY: json.dump(st, open(ST + '.tmp', 'w'), ensure_ascii=False, indent=1); os.replace(ST + '.tmp', ST)
if not DRY: json.dump(PEND, open(PF + '.tmp', 'w'), ensure_ascii=False); os.replace(PF + '.tmp', PF)
out['verify_pending'] = len(PEND)
print(json.dumps(out, ensure_ascii=False, indent=1))
