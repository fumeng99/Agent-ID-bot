"""BSC 只读 RPC 池：11 个公共节点轮换，遇 429/5xx/超时自动换下一个并冷却该节点 60 秒；支持批量请求（一次查完所有账号）。
环境变量 AIDBOT_RPC 可用逗号放自己的节点（优先使用）。全部失败才抛异常。"""
import json, os, random, time, urllib.request
NODES = [u for u in os.environ.get('AIDBOT_RPC', '').split(',') if u] + [
    'https://bsc-dataseed.bnbchain.org', 'https://bsc-dataseed1.bnbchain.org', 'https://bsc-dataseed2.bnbchain.org',
    'https://bsc-dataseed1.defibit.io', 'https://bsc-dataseed1.ninicoin.io', 'https://bsc-rpc.publicnode.com',
    'https://bsc.blockrazor.xyz', 'https://bsc.drpc.org', 'https://bsc-mainnet.public.blastapi.io',
    'https://bsc.meowrpc.com', 'https://bsc.rpc.blxrbdn.com']
COOL = '/tmp/rpcpool_cool.json'
def _cool():
    try: return json.load(open(COOL))
    except Exception: return {}
def _ban(u):
    c = _cool(); c[u] = time.time() + 60
    try: json.dump(c, open(COOL, 'w'))
    except Exception: pass
def _post(u, body):
    r = urllib.request.urlopen(urllib.request.Request(u, json.dumps(body).encode(), {'content-type': 'application/json', 'User-Agent': 'Mozilla/5.0'}), timeout=10)
    return json.load(r)
def _order():
    c = _cool(); now = time.time(); own = [u for u in NODES if u in os.environ.get('AIDBOT_RPC', '')]
    rest = [u for u in NODES if u not in own]; random.shuffle(rest)
    ok = [u for u in own + rest if c.get(u, 0) < now]
    return ok + [u for u in own + rest if u not in ok]
def batch(calls):
    """calls: [(method, params), ...] -> [result, ...]（顺序一致）。任一项报错就换节点重来。"""
    body = [{'jsonrpc': '2.0', 'id': i, 'method': m, 'params': p} for i, (m, p) in enumerate(calls)]
    last = None
    for u in _order():
        try:
            r = _post(u, body)
            if not isinstance(r, list): raise ValueError(str(r)[:120])
            r = {x['id']: x for x in r}
            if any('result' not in r.get(i, {}) for i in range(len(calls))): raise ValueError('partial/error')
            return [r[i]['result'] for i in range(len(calls))]
        except Exception as e:
            last = f'{u}: {e}'; _ban(u)
    raise RuntimeError('所有 RPC 节点都失败，最后一个：' + str(last))
def rpc(m, p): return batch([(m, p)])[0]
