import sys as _s; _s.path.append('$AIDBOT_HOME/pylib')  # 沙箱重置会清掉 pip 包，pycryptodome 装在持久目录
# 从  v2.3.2–v2.3.4 学来的通用防护（2026-10-31 19:32 用户「立马学习」）。其他脚本 import 使用。
import time, json, os, subprocess
from Crypto.Hash import keccak
FLAP_BLOCK = {'0xe2ce6ab80874fa9fa2aae65d277dd6b8e65c9de0', '0x90497450f2a706f1951b5bdda52b4e5d16f34c06',
              '0x18aa6c93f1887a243fdc348c9a77f5136555174a', '0x8b4329947e34b6d56d71a3385cac122bade7d78d',
              '0x5dd913731c12ad8df3e574859fde45412bf4aad9'}
def flap_guard(to, data, sim=None):
    """禁止跟 Flap 合约互动/授权给 Flap（Flap 官网曾被 DNS 劫持）。返回原因或空串。"""
    if str(to).lower() in FLAP_BLOCK: return '禁止跟 Flap 合约互动'
    d = str(data or '').lower()
    if d[:10] in ('0x095ea7b3', '0x39509351') and '0x' + d[34:74] in FLAP_BLOCK: return '禁止授权给 Flap'
    if any(str(a.get('spender', '')).lower() in FLAP_BLOCK for a in ((sim or {}).get('allowanceChanges') or [])): return '会授权给 Flap'
    return ''
def fresh_deadline(data, secs=900):
    """照抄别人交易时，把像时间戳的参数（±30 天内）换成 now+15 分钟，避免旧交易过期失败。"""
    now = int(time.time()); ws = [data[10 + i:74 + i] for i in range(0, len(data) - 10, 64)]; ch = False; out = []
    for x in ws:
        v = int(x, 16)
        if now - 30 * 86400 < v < now + 30 * 86400: out.append(hex(now + secs)[2:].rjust(64, '0')); ch = True
        else: out.append(x)
    return data[:10] + ''.join(out) if ch else data
def agent_id(name):
    """Agent ID 编号 = keccak256(名称去 .ai)，hex 64 位。mint(agentTokenId) 类项目用自己的编号。"""
    n = name.lower().removesuffix('.ai'); return keccak.new(digest_bits=256, data=n.encode()).hexdigest()
def progress_suspect(progress, minters=0, sold=0, hot3m=0):
    """进度读到 <3% 却已很多人在打（≥50 代理人 / ≥1000 份 / 3 分钟 ≥15 钱包）→ 判定读错，改用库存算法或按热度。"""
    return 0 < progress < 0.03 and (minters >= 50 or sold >= 1000 or hot3m >= 15)
def lock_check(rpc, token, wallet):
    """买到的币能不能转：模拟转 1 单位到黑洞。被挡返回警告（白名单锁/貔貅），否则空串。"""
    try:
        rpc('eth_call', [{'from': wallet, 'to': token, 'data': '0xa9059cbb' + '000000000000000000000000000000000000000000000000000000000000dead'.rjust(64, '0') + '1'.rjust(64, '0')}, 'latest'])
        return ''
    except Exception as e:
        return f'这个币现在转不出去（{str(e)[:40]}），可能要等开放交易，也可能是貔貅'
