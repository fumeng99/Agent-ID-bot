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
def _post(u, body, timeout=10):
    r = urllib.request.urlopen(urllib.request.Request(u, json.dumps(body).encode(), {'content-type': 'application/json', 'User-Agent': 'Mozilla/5.0'}), timeout=timeout)
    return json.load(r)
def _order():
    c = _cool(); now = time.time(); own = [u for u in NODES if u in os.environ.get('AIDBOT_RPC', '')]
    rest = [u for u in NODES if u not in own]; random.shuffle(rest)
    ok = [u for u in own + rest if c.get(u, 0) < now]
    return ok + [u for u in own + rest if u not in ok]
def batch(calls, tolerant=False, chunk=40, timeout=10, deadline=None, parallel=1):
    """calls: [(method, params), ...] -> [result, ...]（顺序一致）。任一项报错就换节点重来。
    tolerant=True：合约 revert 之类的单项错误返回 None，不换节点（适合批量探测函数是否存在）。超过 chunk 条自动分批。
    2026-11-01 20:50 新增（fastprogress 偶发 40 秒超时）：timeout=单次请求超时秒数；deadline=绝对时间戳，过了就不再换节点、直接抛错；
    parallel>1 时各分批并发请求（每批各自轮换节点），总耗时≈最慢一批而不是各批相加。默认值与旧行为一致。"""
    if len(calls) > chunk:
        parts = [calls[i:i + chunk] for i in range(0, len(calls), chunk)]
        if parallel > 1:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(min(parallel, len(parts))) as ex:
                rs = list(ex.map(lambda c: batch(c, tolerant, chunk, timeout, deadline), parts))
            return [x for r in rs for x in r]
        return [x for c in parts for x in batch(c, tolerant, chunk, timeout, deadline)]
    body = [{'jsonrpc': '2.0', 'id': i, 'method': m, 'params': p} for i, (m, p) in enumerate(calls)]
    last = None
    for u in _order():
        if deadline and time.time() >= deadline:
            last = (last or '') + ' | 已到截止时间'; break
        try:
            t = timeout if not deadline else max(1.0, min(timeout, deadline - time.time()))
            r = _post(u, body, t)
            if not isinstance(r, list): raise ValueError(str(r)[:120])
            r = {x['id']: x for x in r}
            if any(i not in r for i in range(len(calls))): raise ValueError('partial')
            if tolerant:
                lim = [x for x in r.values() if 'error' in x and any(k in str(x['error']).lower() for k in ('limit', 'rate', 'too many', 'capacity'))]
                if lim: raise ValueError('rate limited')
                return [r[i].get('result') for i in range(len(calls))]
            if any('result' not in r[i] for i in range(len(calls))): raise ValueError('partial/error')
            return [r[i]['result'] for i in range(len(calls))]
        except Exception as e:
            last = f'{u}: {e}'; _ban(u)
    raise RuntimeError('所有 RPC 节点都失败，最后一个：' + str(last))
def rpc(m, p): return batch([(m, p)])[0]
