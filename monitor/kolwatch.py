#!/usr/bin/env python3
"""KOL 钱包盯盘（用户 2026-11-03 02:01：阿峰_Afeng 0xbf00…4903，盯 AGC）。只读，只提醒，不交易。
每次扫上次块之后的代币 Transfer（进/出 KOL），写 discover_out/kolwatch_events.jsonl（每条：时间、KOL、代币、方向 买入/卖出/转入/转出、数量、占持仓%、tx）。
discoverd 每约 60 秒拉起（flock）。配置 kolwatch.json：{"kols":[{"name","addr","tokens":[...]}]}"""
import sys, os, json, time
D = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, D); os.chdir(D)
from rpcpool import rpc
TR = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'
CFG = 'kolwatch.json'; ST = 'kolwatch_state.json'; EV = 'discover_out/kolwatch_events.jsonl'
def load(p, d):
    try: return json.load(open(p))
    except Exception: return d
cfg = load(CFG, {'kols': []}); st = load(ST, {})
bn = int(rpc('eth_blockNumber', []), 16)
pairs = {'0x63dea5acdbe1f27c968907d08610a5f0a0284ba6': '0xe56041cddbb46db01c67b7a62684f428a04db8c6'}
for k in cfg['kols']:
    K = k['addr'].lower(); pad = '0x' + K[2:].rjust(64, '0')
    for T in k['tokens']:
        T = T.lower(); key = f'{K}|{T}'; e = st.get(key, {})
        frm = e.get('block', bn - 20) + 1; to = min(bn, frm + 1500)  # 2026-11-05：每轮最多 1500 块，落后时多轮追上
        if frm > to: continue
        logs = []
        for topics in ([TR, pad], [TR, None, pad]):
            logs += rpc('eth_getLogs', [{'address': T, 'fromBlock': hex(frm), 'toBlock': hex(to), 'topics': topics}]) or []
        bal = int(rpc('eth_call', [{'to': T, 'data': '0x70a08231' + K[2:].rjust(64, '0')}, 'latest']), 16) / 1e18
        for lg in sorted(logs, key=lambda x: (int(x['blockNumber'], 16), int(x['logIndex'], 16))):
            f = '0x' + lg['topics'][1][-40:]; t = '0x' + lg['topics'][2][-40:]; amt = int(lg['data'], 16) / 1e18
            pr = pairs.get(T)
            side = ('卖出' if t == pr else '转出') if f == K else ('买入' if f == pr else '转入')
            rec = {'time_cst': time.strftime('%m-%d %H:%M:%S', time.gmtime(time.time() + 8 * 3600)), 'kol': k['name'], 'addr': K, 'token': T,
                   'side': side, 'amount': amt, 'bal_after_now': bal, 'pct_of_pos': round(100 * amt / (bal + amt), 1) if side in ('卖出', '转出') and bal + amt else None,
                   'tx': lg['transactionHash'], 'block': int(lg['blockNumber'], 16)}
            open(EV, 'a').write(json.dumps(rec, ensure_ascii=False) + '\n'); print(json.dumps(rec, ensure_ascii=False))
        st[key] = {'block': to, 'bal': bal, 't': time.time()}
json.dump(st, open(ST, 'w'))
