# 价格条件自动卖：每 3 秒查一次价，触发即调 fast_sell 五号并发卖出。只用于用户明确授权的币和条件。
# usage: python3 price_trigger.py <token> [--trail 30] [--tp 2.0:50] [--below PRICE] [--minutes 18]
#   --trail 30   : 从启动后最高价回撤 30% 全卖
#   --tp 2.0:50  : 涨到启动价 2 倍卖 50%（只触发一次）
#   --below P    : 价格 ≤ P 全卖
import sys, os, json, subprocess, time
D = os.path.dirname(os.path.abspath(__file__)); a = sys.argv[1:]; T = a[0]
def opt(k, d=None): return a[a.index(k) + 1] if k in a else d
trail = float(opt('--trail', 0)); tp = opt('--tp'); below = float(opt('--below', 0)); mins = float(opt('--minutes', 18))
ST = os.path.join(D, f'trigger_{T.lower()[:10]}.json')
st = json.load(open(ST)) if os.path.exists(ST) else {}
def price():
    r = subprocess.run(['curl', '-s', '--max-time', '5', 'https://api.dexscreener.com/latest/dex/tokens/' + T], capture_output=True, text=True)
    # 2026-11-01 学  v2.4.0：取流动性最大的那个池子的价格（以前取 pairs[0]，小池子会误判暴跌/暴涨）
    ps = [x for x in json.loads(r.stdout).get('pairs') or [] if x.get('chainId') == 'bsc' and str((x.get('baseToken') or {}).get('address', '')).lower() == T.lower() and (x.get('liquidity') or {}).get('usd', 0) > 50]
    return float(max(ps, key=lambda x: x['liquidity']['usd'])['priceUsd'])
def sell(pct, why):
    r = subprocess.run(['python3', os.path.join(D, 'fast_sell.py'), T, str(pct)], capture_output=True, text=True, timeout=200)
    st.setdefault('fired', []).append({'t': time.strftime('%H:%M:%S'), 'why': why, 'res': r.stdout[-600:]})
    json.dump(st, open(ST, 'w'), ensure_ascii=False, indent=1); print(why, r.stdout[-600:], flush=True)
end = time.time() + mins * 60
while time.time() < end and not st.get('done'):
    try: p = price()
    except Exception: time.sleep(3); continue
    st.setdefault('start', p); st['peak'] = max(st.get('peak', 0), p); st['last'] = p
    if tp and not st.get('tp_done'):
        m, pc = map(float, tp.split(':'))
        if p >= st['start'] * m: sell(pc, f'tp {m}x'); st['tp_done'] = True
    hit = (trail and p <= st['peak'] * (1 - trail / 100)) or (below and p <= below)
    st['hit'] = st.get('hit', 0) + 1 if hit else 0  # 学 v2.5.0：连续 2 次（约 3 秒）都低于门槛才卖，避免价格来源跳一下就误卖
    if hit and st['hit'] >= 2:
        sell(100, f'stop p={p} peak={st["peak"]}'); st['done'] = True
    json.dump(st, open(ST, 'w'), ensure_ascii=False, indent=1); time.sleep(3)
print(json.dumps({k: st.get(k) for k in ('start', 'peak', 'last', 'done')}))
