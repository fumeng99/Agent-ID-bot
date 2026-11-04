#!/usr/bin/env python3
"""2026-11-05 用户原话「这种需要告诉我 让我判断」（BBF 被 newtpl 静默 reject 后）：
newtpl 识别出、有热度（30 分钟 ≥30 钱包）、但不会被自动执行的项目（付费/未知/review/reject），每个合约只报一次给用户判断。
只读。输出 JSON：{"items":[...]}，巡逻读到就单独发「待你判断」简报。"""
import json, os, time
D = '$AIDBOT_DATA'
EV = f'{D}/discover_out/newtpl_events.jsonl'; ST = f'{D}/discover_out/newtpl_reported.json'
HEAT = 30
st = json.load(open(ST)) if os.path.exists(ST) else {}
_f = f'{D}/discover_out/newtpl.json'
if not os.path.exists(_f): _f = f'{D}/newtpl_state.json'
_r = json.load(open(_f)) or {}
res = _r.get('contracts', _r) if isinstance(_r, dict) else {}
items = []
for line in open(EV) if os.path.exists(EV) else []:
    try: e = json.loads(line)
    except Exception: continue
    a = e.get('contract'); n = e.get('heat_wallets_30m') or 0
    if not a or a in st or n < HEAT: continue
    r = res.get(a) or {}
    cat, concl = r.get('category', e.get('category')), r.get('conclusion', e.get('conclusion'))
    if cat == 'refund_deposit' and r.get('refundmine_ok'): continue      # 自动挖
    if cat == 'free_mint' and concl == 'pass': continue                    # 免费按原规则自动铸
    items.append({'contract': a, 'name': e.get('name'), 'category': cat, 'conclusion': concl, 'heat_wallets_30m': n,
                  'progress': (r.get('params') or {}).get('progress'), 'pay_in_erc20': (r.get('params') or {}).get('pay_in_erc20'),
                  'risk_flags': r.get('risk_flags', e.get('risk_flags')), 'safe_flags': r.get('safe_flags', e.get('safe_flags')),
                  'website': r.get('website') or e.get('website'), 'bscscan': e.get('bscscan'), 'first_seen_cst': e.get('time_cst')})
    st[a] = time.time()
if '--dry' not in os.sys.argv: json.dump(st, open(ST, 'w'))
print(json.dumps({'items': items}, ensure_ascii=False, indent=1))
