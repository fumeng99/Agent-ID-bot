"""共用：账号、RPC、baw 预演/执行、干净判定。路径全部来自 AIDBOT_HOME / AIDBOT_DATA / config。"""
import json, os, subprocess, sys, urllib.request
here = os.path.dirname(os.path.abspath(__file__))
H = os.environ.get('AIDBOT_HOME', os.path.expanduser('~/.agent-id-bot'))
DATA = os.environ.get('AIDBOT_DATA', os.path.join(H, 'data')); os.makedirs(DATA, exist_ok=True)
BAW = os.path.join(H, 'baw', 'node_modules', '.bin', 'baw')
RPC = os.environ.get('AIDBOT_RPC', 'https://bsc-rpc.publicnode.com')
def accounts(flag='--auto'):
    out = subprocess.run([sys.executable, os.path.join(here, 'accounts.py'), flag], capture_output=True, text=True).stdout
    return [dict(zip(('dir', 'label', 'address'), l.split(' ', 2))) for l in out.splitlines() if l.strip()]
def env(a): return dict(os.environ, HOME=os.path.join(H, 'baw', 'accounts', a['dir']))
def rpc(m, p):
    q = json.dumps({"jsonrpc": "2.0", "id": 1, "method": m, "params": p}).encode()
    return json.load(urllib.request.urlopen(urllib.request.Request(RPC, q, {'content-type': 'application/json', 'User-Agent': 'Mozilla/5.0'}), timeout=20)).get('result')
def call(to, data): return int(rpc('eth_call', [{'to': to, 'data': data}, 'latest']), 16)
def bal721(c, addr): return call(c, '0x70a08231' + addr[2:].lower().rjust(64, '0'))
def baw(a, *args):
    r = subprocess.run([BAW, *args], env=env(a), capture_output=True, text=True, timeout=120)
    try: return json.loads(r.stdout)
    except Exception: return {"err": (r.stdout + r.stderr)[:300]}
def clean(d):
    """value=0 免费 mint 的干净标准：模拟成功、无授权/权限变化、无风险标记、只收不付。"""
    s = d['simulationResult']; rk = d.get('risks', {}); bc = s['balanceChanges']
    return (s['simulationCode'] == "000000000" and not s['allowanceChanges'] and not s['authorityChanges']
            and not rk.get('riskDetails') and not rk.get('riskBehaviors') and not rk.get('addresses')
            and len(bc) > 0 and all(b['change'] > 0 for b in bc))
def free_mint(a, to, data, gas='800000', dry=False):
    """返回 (状态, 信息)：skip / unclean / clean(dry) / exec"""
    p = baw(a, 'contract-call', 'preview', '--binanceChainId', '56', '--from', a['address'], '--to', to, '--value', '0', '--inputData', data, '--gasLimit', gas, '--json')
    d = p.get('data')
    if not d: return 'skip', str(p.get('error', p))[:150]
    if not clean(d): return 'unclean', json.dumps(d['simulationResult'])[:200]
    if dry: return 'clean', 'dry'
    return 'exec', json.dumps(baw(a, 'contract-call', 'execute', '--requestId', d['requestId'], '--json'))[:300]
