#!/usr/bin/env python3
"""discover_all 自检修复（2026-11-02 新增，起因：fairmint 因 QuickNode 节点停用连续 11+ 轮 exit=4 无人处理）。
只读：只做节点体检和重跑发现类脚本（getLogs / eth_call / 网页），不签名、不交易、不改任何买卖规则。

用法：python3 selfheal.py <discover_out 目录>   （discover_all.sh 在统计 fail_streak 之后调用）
逻辑：fail_streak.json 里某步连续失败 ≥3 轮，且是第 3/6/9… 轮（每 3 轮最多修一次，避免每轮都多花时间）时：
  1. 节点体检：清掉 rpcpool 冷却表；探测 getLogs 节点和 eth_call 节点，健康的排前面（FM_LOGS_RPC / AIDBOT_RPC 环境变量传给重跑）；
  2. 用「缩小范围」的参数重跑该步一次（fairmint 只扫 10 分钟、250 块一段、不做 20 秒重读；chainscan 只扫 15 分钟；opencheck 缩短预算……）；
  3. 重跑成功（exit=0 且输出是 JSON）→ 用新输出覆盖 discover_out/<步骤>.json，streak 清零，记 selfheal ok；
     仍失败 → 记 selfheal ok=false 和错误，该步才进 alert（巡逻只报 alert）。
"""
import json, os, re, subprocess, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor

D = os.path.dirname(os.path.abspath(__file__))
O = sys.argv[1] if len(sys.argv) > 1 else os.path.join(D, 'discover_out')
F = os.path.join(O, 'fail_streak.json')
LOGS_CAND = ['https://rpc-bsc.48.club', 'https://0.48.club', 'https://bsc.rpc.blxrbdn.com', 'https://bsc.blockrazor.xyz']
CALL_CAND = ['https://bsc.blockrazor.xyz', 'https://rpc-bsc.48.club', 'https://0.48.club', 'https://bsc-dataseed.bnbchain.org', 'https://bsc-dataseed1.defibit.io',
             'https://bsc-rpc.publicnode.com', 'https://bsc-mainnet.public.blastapi.io', 'https://bsc.rpc.blxrbdn.com', 'https://bsc.drpc.org']
FM, CS = os.path.join(D, 'fairmint'), os.path.join(D, 'chainscan')
# 步骤 -> (目录, 缩小范围后的命令, 外层超时秒)
FALLBACK = {
    'fairmint': (FM, ['env', 'FM_DEADLINE=95', 'FM_RANGE=250', 'python3', 'fairmint.py', '--minutes', '10', '--no-recheck'], 105),
    'heatscan': (FM, ['env', 'FM_DEADLINE=80', 'python3', 'heatscan.py'], 90),
    'chainscan': (CS, ['env', 'SCAN_BUDGET=40', 'python3', 'scan.py', '--minutes', '15'], 110),
    'regscan': (CS, ['env', 'REG_DEADLINE=80', 'python3', 'regscan.py'], 95),
    'opencheck': (D, ['env', 'OC_RPC_BUDGET=45', 'OC_WATCHDOG=70', 'python3', 'opencheck.py'], 85),
    'precheck': (D, ['env', 'PRECHECK_BUDGET=60', 'python3', 'precheck.py'], 80),
    'hotfree': (D, ['python3', 'hotfree_unverified.py'], 90),
    'sitescan': (D, ['python3', 'sitescan.py'], 110),
    'watch_sites': (D, ['python3', 'watch_sites.py'], 110),
    'claimscan': (D, ['python3', 'claimscan.py', '60'], 90),
    'platforms': (D, ['python3', 'platforms/platforms.py'], 90),
    'tg': (D, ['python3', 'tg/tgcall.py', 'agentidcall', 'dashutiaozi'], 90),
    'approvals': (D, ['python3', 'approvals_watch.py'], 60),
    'fastprogress': (FM, ['python3', 'fastprogress.py'], 40),
}


def _post(u, body, to=6):
    return json.load(urllib.request.urlopen(urllib.request.Request(u, json.dumps(body).encode(), {'content-type': 'application/json', 'User-Agent': 'Mozilla/5.0'}), timeout=to))


def probe():
    """返回 (getLogs 健康节点, eth_call 健康节点)。"""
    try: head = int(_post('https://bsc.blockrazor.xyz', {'jsonrpc': '2.0', 'id': 1, 'method': 'eth_blockNumber', 'params': []})['result'], 16)
    except Exception: head = None
    def lg(u):
        if not head: return None
        span = 20 if 'blockrazor' in u else 300
        try:
            d = _post(u, {'jsonrpc': '2.0', 'id': 1, 'method': 'eth_getLogs', 'params': [{'fromBlock': hex(head - span), 'toBlock': hex(head),
                     'topics': ['0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef']}]}, 10)
            return u if 'result' in d and 'blockrazor' not in u else None  # blockrazor 只能 25 块，不当宽区间节点
        except Exception: return None
    def cl(u):
        try:
            d = _post(u, [{'jsonrpc': '2.0', 'id': i, 'method': 'eth_blockNumber', 'params': []} for i in range(3)], 5)
            return u if isinstance(d, list) and all('result' in x for x in d) else None
        except Exception: return None
    with ThreadPoolExecutor(8) as ex:
        logs = [u for u in ex.map(lg, LOGS_CAND) if u]
        calls = [u for u in ex.map(cl, CALL_CAND) if u]
    return logs, calls


def rerun(step, env):
    d, cmd, to = FALLBACK[step]
    s = time.time(); out = os.path.join(O, f'{step}.selfheal.json'); err = os.path.join(O, f'{step}.selfheal.err')
    with open(out, 'w') as fo, open(err, 'w') as fe:
        try: rc = subprocess.run(['timeout', str(to)] + cmd, cwd=d, stdout=fo, stderr=fe, env=env).returncode
        except Exception as e: rc = 99; fe.write(str(e))
    ok = rc == 0
    try: json.load(open(out))
    except Exception: ok = False
    if ok: os.replace(out, os.path.join(O, f'{step}.json')); os.replace(err, os.path.join(O, f'{step}.err'))
    e = ''
    if not ok:
        try: e = last_err(open(err).read(), rc)
        except Exception: e = f'exit={rc}'
    return {'step': step, 'ok': ok, 'rc': rc, 'secs': round(time.time() - s, 1), 'err': e}


def last_err(text, rc):
    """取最后一条像报错的行（Traceback/Error/失败/超时…），没有就写 exit 码；不再把 'logs <块> n=' 这类进度行当成错误。"""
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    bad = [l for l in lines if re.search(r'error|exception|traceback|失败|超时|timeout|timed out|refused|denied|429|403|50\d', l, re.I)]
    return (bad[-1][:200] if bad else f'exit={rc}（stderr 无报错行）')


def main():
    try: st = json.load(open(F))
    except Exception: return print(json.dumps({'selfheal': [], 'note': 'no fail_streak.json'}, ensure_ascii=False))
    streak = st.get('streak', {}); heal = st.setdefault('selfheal', {})
    todo = [n for n, c in streak.items() if c >= 3 and c % 3 == 0 and n in FALLBACK]
    res = []
    if todo:
        try: os.remove('/tmp/rpcpool_cool.json')  # 清掉 rpcpool 的节点冷却表，重新排序
        except Exception: pass
        logs, calls = probe()
        env = dict(os.environ)
        if logs: env['FM_LOGS_RPC'] = ','.join(logs)
        if calls: env['AIDBOT_RPC'] = ','.join(calls[:4])
        with ThreadPoolExecutor(len(todo)) as ex: res = list(ex.map(lambda n: rerun(n, env), todo))
        now = time.strftime('%m-%d %H:%M', time.gmtime(time.time() + 8 * 3600))
        for r in res:
            heal[r['step']] = {'at': now, 'ok': r['ok'], 'rounds': streak[r['step']], 'err': r['err'], 'logs_nodes': logs, 'call_nodes': calls[:4]}
            if r['ok']:
                streak[r['step']] = 0; st.get('last_err', {}).pop(r['step'], None)
                with open(os.path.join(O, 'summary.txt'), 'a') as f: f.write(f"{r['step']} selfheal=ok 缩小范围重跑成功 用时={r['secs']}s\n")
            else:
                with open(os.path.join(O, 'summary.txt'), 'a') as f: f.write(f"{r['step']} selfheal=fail exit={r['rc']} 用时={r['secs']}s {r['err'][:80]}\n")
    # 只有「连续 ≥3 轮失败且最近一次自检修复也失败」的步骤才报警
    st['alert'] = [{'step': n, 'rounds': c, 'last_err': st.get('last_err', {}).get(n, ''), 'selfheal': heal.get(n)}
                   for n, c in streak.items() if c >= 3 and (n not in FALLBACK or not heal.get(n, {}).get('ok', False))]
    st['streak'] = streak
    json.dump(st, open(F, 'w'), ensure_ascii=False, indent=1)
    print(json.dumps({'selfheal': res, 'alert': st['alert']}, ensure_ascii=False))


if __name__ == '__main__': main()
