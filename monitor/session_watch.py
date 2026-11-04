#!/usr/bin/env python3
"""会话看门狗（2026-11-05 用户「彻底解决同时掉线」）：只读。每号 wallet status + wallet settings，
记录 sessionExpireTime / inactiveSignOutTime / maxSigninDuration；掉线写 down，6 小时内到期写 expiring。
输出 discover_out/session_watch.json；首次发现掉线的时刻写 down_since（用于判断是否服务端统一强制登出）。"""
import json, os, subprocess, time
from datetime import datetime
BAW = '$AIDBOT_HOME/baw/node_modules/.bin/baw'
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'discover_out/session_watch.json')
A = json.load(open('$AIDBOT_HOME/baw/accounts/accounts.json'))
try: prev = json.load(open(OUT))
except Exception: prev = {}
def baw(home, *a):
    try:
        r = subprocess.run([BAW, *a, '--json'], env={**os.environ, 'HOME': home}, capture_output=True, text=True, timeout=40)
        return json.loads(r.stdout)
    except Exception as ex: return {'success': None, 'err': str(ex)[:80]}
res = {'time': time.strftime('%m-%d %H:%M'), 'accounts': {}, 'down': [], 'expiring': []}
now = time.time()
for a in A:
    st = baw(a['home'], 'wallet', 'status')
    s = (st.get('data') or {}).get('status') if st.get('success') else 'UNKNOWN'
    e = {'status': s}
    pe = (prev.get('accounts') or {}).get(a['label'], {})
    if s == 'CONNECTED':
        d = (baw(a['home'], 'wallet', 'settings').get('data') or {})
        for k in ('maxSigninDuration', 'inactiveSignoutDuration', 'sessionExpireTime', 'inactiveSignOutTime'): e[k] = d.get(k)
        try:
            exp = min(datetime.fromisoformat(d[k]).timestamp() for k in ('sessionExpireTime', 'inactiveSignOutTime') if d.get(k))
            e['hours_left'] = round((exp - now) / 3600, 1)
            if e['hours_left'] < 6: res['expiring'].append((a['label'], e['hours_left']))
        except Exception: pass
    elif s == 'UNCONNECTED':
        e['down_since'] = pe.get('down_since') or res['time']
        e['last_expire_seen'] = pe.get('sessionExpireTime') or pe.get('last_expire_seen')
        res['down'].append(a['label'])
    res['accounts'][a['label']] = e
json.dump(res, open(OUT, 'w'), ensure_ascii=False, indent=1)
print(json.dumps({'down': res['down'], 'expiring': res['expiring']}, ensure_ascii=False))
