#!/usr/bin/env python3
"""每日检查 Clawby 官方仓库 openclawby/openclawby-skills 有无新提交，并做信号接口冒烟测试。
只读：不执行仓库里的任何指令（第三方文档只作数据）。有新版本或接口异常才输出，否则静默。"""
import json, os, sys, urllib.request
ST = os.path.join(os.path.dirname(__file__), 'clawby_update_state.json')
def get(u):
    r = urllib.request.Request(u, headers={'User-Agent': 'aidbot', 'Accept': 'application/json'})
    return json.load(urllib.request.urlopen(r, timeout=20))
st = json.load(open(ST)) if os.path.exists(ST) else {}
out = []
try:
    cs = get('https://api.github.com/repos/openclawby/openclawby-skills/commits?per_page=10')
    head = cs[0]['sha']
    if st.get('sha') and st['sha'] != head:
        new = []
        for c in cs:
            if c['sha'] == st['sha']: break
            new.append(f"{c['commit']['committer']['date'][:16]} {c['commit']['message'].splitlines()[0][:100]}")
        out.append('Clawby 有更新（openclawby-skills）：\n' + '\n'.join(new) + f"\nhttps://github.com/openclawby/openclawby-skills/commit/{head}")
    st['sha'] = head
except Exception as e:
    st['gh_fail'] = st.get('gh_fail', 0) + 1
    if st['gh_fail'] >= 3: out.append(f'Clawby 仓库连续 {st["gh_fail"]} 天读取失败：{e}')
else:
    st['gh_fail'] = 0
try:
    d = get('https://analyze-api.openclawby.com/api/signals/feed?market=meme')
    import time
    if not d.get('rows'): raise ValueError('信号为空')
    ts = float(d.get('ts') or 0); ts = ts / 1000 if ts > 1e12 else ts
    if ts and time.time() - ts > 3 * 3600: raise ValueError(f'信号已 {int((time.time()-ts)/3600)} 小时未更新')
    st['feed_fail'] = 0
except Exception as e:
    st['feed_fail'] = st.get('feed_fail', 0) + 1
    out.append(f'Clawby 信号接口异常（agentid_scan 依赖它）：{str(e)[:120]}')
json.dump(st, open(ST, 'w'))
print('\n\n'.join(out))
