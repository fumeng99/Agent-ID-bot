#!/usr/bin/env python3
"""学  v2.6.x（AG_MIN）：70% 打满前，确认真的有很多 Agent ID 钱包在打，防项目方自己灌进度。
数 fair mint 合约 Minted(address indexed minter,...) 事件里的不同铸造地址（增量缓存），再抽 24 个查是否持有 .ai 身份，
ai_minters = 不同铸造地址数 × .ai 比例。

2026-11-02 修「读失败当不达标」（13:40 BscAgentID 0x112f…7dfe）：
  根因：QuickNode 停用后 getLogs 改走 LOGS_RPCS，这些节点限 5000 块/次，而本脚本一段扫 10000 块 → 每次都报
  「exceed maximum block range: 5000」→ 返回 None → autofill 把「读不到」当「不足 100 个」。
  现在：①带地址 getLogs 先走 bsc-rpc.publicnode.com（4000 块/段，实测 0.4 秒），再 48.club 两个节点（1000 块/段），
  每段最多 3 轮换节点重试、指数退避；②每扫完一段就落盘缓存，超时下次接着扫，不从头来；
  ③读不到不再返回 None，而是返回 {'status': 'unknown', 'reason': ...}。调用方：unknown ≠ 通过，也 ≠ 不达标——
  autofill 本轮不买、写 discover_out/autofill_recheck.json，fastprogress 常驻 60 秒内重读。
  成功时返回 {'status': 'ok', 'ai_minters': ..., ...}。
用法：python3 minters.py <fair mint 合约> [--budget 秒]
"""
import os, sys, json, time, random, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fairmint as F
D = os.path.dirname(os.path.abspath(__file__)); CACHE = os.path.join(D, 'minters_cache.json')
REG = '0x' + F.REG
# (节点, 每段块数)：publicnode 支持带地址 4000 块；48.club 限 5000 块但大段常超时，用 1000 块
ADDR_LOGS = [(u, 1000) for u in os.environ.get('FM_ADDR_LOGS_RPC', '').split(',') if u] + [
    ('https://bsc-rpc.publicnode.com', 4000), ('https://rpc-bsc.48.club', 500), ('https://0.48.club', 500)]
_BAD = {}


def unknown(reason): return {'status': 'unknown', 'reason': str(reason)[:200]}


def _post(u, body, to):
    return json.load(urllib.request.urlopen(urllib.request.Request(u, json.dumps(body).encode(), {'content-type': 'application/json', 'User-Agent': 'Mozilla/5.0'}), timeout=to))


def _logs(c, f, t, t_end):
    """[f,t] 段的 Minted 日志：按节点各自的段长切小，逐段重试。
    publicnode 常回 429（突发限流）：同节点退避 0.6/1.2/2.4/4.8 秒重试，仍不行再换 48.club（500 块/段，慢）。整体 3 轮。返回 list 或抛错。"""
    last = None
    for rnd in range(3):
        for u, step in ADDR_LOGS:
            if _BAD.get(u, 0) > time.time() and rnd == 0 and u != ADDR_LOGS[-1][0]: continue
            out = []; ok = True; a = f
            while a <= t:
                if time.time() > t_end: raise TimeoutError(f'预算用完（最后错误 {last}）')
                b = min(a + step - 1, t); got = None
                for k in range(6):
                    try:
                        d = _post(u, {'jsonrpc': '2.0', 'id': 1, 'method': 'eth_getLogs', 'params': [{'address': c, 'topics': [F.TOPICS], 'fromBlock': hex(a), 'toBlock': hex(b)}]},
                                  max(2, min(8, t_end - time.time())))
                        if 'result' in d: got = d['result']; break
                        last = f'{u}: {str(d.get("error"))[:120]}'
                        if 'range' in str(d.get('error')).lower() or 'limit exceeded' in str(d.get('error')).lower(): break
                    except Exception as e:
                        last = f'{u}: {str(e)[:120]}'
                        if '429' not in str(e) and 'Too Many' not in str(e): break
                    if time.time() + 1.5 * (k + 1) > t_end: break
                    time.sleep(1.5 * (k + 1))  # publicnode 429 是秒级突发限流：1.5/3/4.5/6/7.5 秒退避
                if got is None: _BAD[u] = time.time() + 60; ok = False; break
                out += got; a = b + 1; time.sleep(0.35)  # 节流，少触发 429
            if ok: return out
        time.sleep(2 ** rnd + random.random() * 0.3)
    raise RuntimeError(last)


def _save(cache):
    tmp = CACHE + '.%d.tmp' % os.getpid(); json.dump(cache, open(tmp, 'w')); os.replace(tmp, CACHE)


def ai_minters(c, deploy_block=None, budget=40):
    c = c.lower(); t0 = time.time(); t_end = t0 + budget
    F.DEADLINE = max(getattr(F, 'DEADLINE', 0), t_end + 10)
    try: cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
    except Exception: cache = {}
    e = cache.get(c) or {'m': [], 'to': -1, 'ai': {}, 'cnt': {}}
    if 'cnt' not in e: e = {'m': [], 'to': -1, 'ai': e.get('ai', {}), 'cnt': {}}  # 旧缓存没有逐地址计数 → 从部署块重扫
    if e['to'] < 0:
        _f = {}
        try: _f = (json.load(open(os.path.join(D, 'found.json'))).get(c) or {})
        except Exception: pass
        dep = deploy_block or _f.get('deploy_block') or ((_f.get('first_block_seen') or 0) - 5000 if _f.get('first_block_seen') else None)
        if not dep: return unknown('found.json 没有部署区块/首次发现区块，无法确定扫描起点')
        e['to'] = dep - 1
    head = None
    for _ in range(3):
        try: head = int(F.rpc('eth_blockNumber', []), 16); break
        except Exception as ex: last = ex; time.sleep(1)
    if head is None: return unknown(f'读不到最新区块：{str(last)[:120]}')
    m = set(e['m'])
    f = e['to'] + 1; SEG = 20000
    while f <= head:
        if time.time() > t_end: cache[c] = e; _save(cache); return unknown(f'预算 {budget}s 内未扫完（已到区块 {e["to"]}，剩 {head - e["to"]} 块，已存缓存，下次接着扫）')
        t = min(f + SEG - 1, head)
        try: L = _logs(c, f, t, t_end)
        except Exception as ex:
            cache[c] = e; _save(cache); return unknown(f'getLogs 失败（已重试换节点）：{str(ex)[:150]}')
        for l in L:
            if len(l['topics']) > 1:
                a_ = '0x' + l['topics'][1][-40:]; m.add(a_); e['cnt'][a_] = e['cnt'].get(a_, 0) + 1
        e['to'] = t; e['m'] = sorted(m); f = t + 1
        cache[c] = e; _save(cache)  # 每段落盘：超时/失败也不丢进度
    # 抽样查 .ai 身份（查过的缓存；读失败的不记，下次再查）
    todo = [x for x in random.sample(sorted(m), min(24, len(m))) if x not in e['ai']]
    for _ in range(3):
        if not todo or time.time() > t_end + 5: break
        try: r = F.bat([('eth_call', [{'to': REG, 'data': '0x70a08231' + x[2:].rjust(64, '0')}, 'latest']) for x in todo])
        except Exception: r = [None] * len(todo)
        for x, b in zip(todo, r):
            if isinstance(b, str) and len(b) >= 66: e['ai'][x] = int(b[:66], 16) > 0
        todo = [x for x in todo if x not in e['ai']]
        if todo: time.sleep(1.5)
    smp = [v for k, v in e['ai'].items() if k in m]
    e['m'] = sorted(m); cache[c] = e; _save(cache)
    if len(smp) < min(10, len(m)): return unknown(f'.ai 身份抽样只读到 {len(smp)} 个（需 ≥{min(10, len(m))}），RPC 失败')
    ratio = sum(smp) / len(smp) if smp else 0
    tot = sum(e['cnt'].values()); top = max(e['cnt'].items(), key=lambda kv: kv[1]) if e['cnt'] else (None, 0)
    return {'status': 'ok', 'minters': len(m), 'ai_ratio': round(ratio, 2), 'ai_minters': int(len(m) * ratio), 'mint_events': tot, 'top_minter': top[0],
            'top_share': round(top[1] / tot, 3) if tot else None, 'scanned_to': e['to'], 'elapsed_s': round(time.time() - t0, 1)}


def ai_minters_retry(c, deploy_block=None, budget=30, tries=3):
    """读失败换节点重试（最多 tries 次，退避 2/4 秒）；仍失败返回 unknown。缓存是增量的，每次重试都接着上次扫。"""
    r = None
    for i in range(tries):
        r = ai_minters(c, deploy_block, budget)
        if r.get('status') == 'ok': r['tries'] = i + 1; return r
        if i < tries - 1: time.sleep(2 * (i + 1))
    r['tries'] = tries; return r


if __name__ == '__main__':
    b = float(sys.argv[sys.argv.index('--budget') + 1]) if '--budget' in sys.argv else 40
    print(json.dumps(ai_minters_retry(sys.argv[1], budget=b), ensure_ascii=False))
