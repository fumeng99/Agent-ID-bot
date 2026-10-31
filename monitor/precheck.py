#!/usr/bin/env python3
"""免费 mint 候选预检（只读 eth_call，不签名不交易）。
读 discover_out/chainscan.json 的候选，对每个候选用五个号各做一次 eth_call 模拟 mint，
解码 revert 原因（含自定义错误，如 SoldOut()），输出排序后的列表：
  mintable 有号能铸 → 本轮优先 preview/execute
  soldout  已售罄   → 写入 soldout.json，之后不再试
  paid     要付费（WrongPayment 等）→ 不是免费 mint，按付费规则处理
  revert   其他原因 → 附错误名，交给人工/下轮
用法：python3 precheck.py  （discover_all.sh 之后跑；输出 discover_out/precheck.json）
"""
import json, os, re, sys, time, urllib.request
D = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(D, 'chainscan'))
from scan import rpc

ACC = json.load(open('$AIDBOT_HOME/baw/accounts/accounts.json'))
SOLD = os.path.join(D, 'soldout.json')
ERRCACHE = os.path.join(D, 'error_sig_cache.json')
KNOWN = {'0x52df9fe5': 'SoldOut()', '0x08c379a0': 'Error(string)', '0x4e487b71': 'Panic(uint256)'}
DEADLINE = time.time() + float(os.environ.get('PRECHECK_BUDGET', '80'))

def load(p, d):
    try: return json.load(open(p))
    except Exception: return d

errc = load(ERRCACHE, {}); sold = load(SOLD, {})

def errname(sel):
    if sel in KNOWN: return KNOWN[sel]
    if sel in errc: return errc[sel]
    name = ''
    try:
        r = urllib.request.urlopen(f'https://api.openchain.xyz/signature-database/v1/lookup?function={sel}', timeout=6)
        hits = json.load(r)['result']['function'].get(sel) or []
        name = hits[0]['name'] if hits else ''
    except Exception: pass
    errc[sel] = name or sel
    return errc[sel]

def call(frm, to, data):
    try:
        rpc('eth_call', [{'from': frm, 'to': to, 'data': data}, 'latest']); return 'ok', ''
    except Exception as e:
        s = str(e); m = re.search(r"0x[0-9a-fA-F]{8,}", s.split('data')[-1]) or re.search(r"0x[0-9a-fA-F]{8,}", s)
        if not m: return 'err', s[:120]
        h = m.group(0).lower(); sel = h[:10]
        if sel == '0x08c379a0' and len(h) > 138:
            try:
                L = int(h[74:138], 16); return 'revert', bytes.fromhex(h[138:138 + 2 * L]).decode(errors='ignore')
            except Exception: pass
        return 'revert', errname(sel)

def items():
    d = load(os.path.join(D, 'discover_out/chainscan.json'), {})
    out = []
    def walk(o):
        if isinstance(o, dict):
            if o.get('mint_target') and o.get('free_like'): out.append(o); return
            for v in o.values(): walk(v)
        elif isinstance(o, list):
            for v in o: walk(v)
    walk(d); return out

res = []
for c in items():
    t = c['mint_target'].lower()
    if t in sold: continue
    if time.time() > DEADLINE: res.append({'name': c.get('name'), 'target': t, 'status': 'not_checked'}); continue
    sel = c.get('selector', '')
    data = sel + ('1'.rjust(64, '0') if c.get('selector_name', '').endswith('(uint256)') else '')
    per = {}
    for a in ACC:
        st, why = call(a['bsc'], t, data); per[a.get('label') or a.get('name') or a['bsc'][:8]] = why if st != 'ok' else 'ok'
        if 'SoldOut' in why or 'sold out' in why.lower(): break
    oks = [k for k, v in per.items() if v == 'ok']
    reasons = sorted({v for v in per.values() if v != 'ok'})
    paid = any(k in r for r in reasons for k in ('WrongPayment', 'InsufficientPayment', 'IncorrectPayment', 'InvalidPayment', 'insufficient'))
    status = 'mintable' if oks else ('soldout' if any('SoldOut' in r or 'sold out' in r.lower() for r in reasons) else ('paid' if paid else 'revert'))
    if status == 'soldout': sold[t] = {'name': c.get('name'), 'at': time.strftime('%Y-%m-%d %H:%M')}
    res.append({'name': c.get('name'), 'target': t, 'selector': sel, 'status': status, 'ok_accounts': oks,
                'reasons': reasons, 'bscscan': c.get('bscscan')})

order = {'mintable': 0, 'not_checked': 1, 'revert': 2, 'paid': 3, 'soldout': 4}
res.sort(key=lambda r: order.get(r['status'], 9))
json.dump(sold, open(SOLD, 'w'), ensure_ascii=False, indent=1)
json.dump(errc, open(ERRCACHE, 'w'), ensure_ascii=False, indent=1)
print(json.dumps(res, ensure_ascii=False, indent=1))
