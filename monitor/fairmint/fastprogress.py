#!/usr/bin/env python3
"""Fair mint 高频进度检查（2026-11-01 审计后新增）。只读：只做 eth_call，不签名、不交易、不写 found.json。

为什么要有：币安人 0x8778…8df5 链上实测（Minted 事件逐笔回放）：
  20% 16:40:03 → 30% 16:59 → 50% 17:21:52 → 60% 17:26:25 → 70% 17:29:01 → 开池(第 10000 份) 17:32:28 CST
  60%→开池只有 6 分钟，70%→开池 3.5 分钟。巡逻每 10 分钟一轮（每轮还要跑 2–6 分钟），60%/70% 两档会在一个间隔内全部跳过。
做法：读 fairmint/found.json 里全部未开池、非 NFT、开池份数已知（非猜测）的盘，每个只读 1–2 个核心字段（已铸份数 + 是否开池），
  一次批量请求（约 200 个 eth_call，5 秒左右），和上次读数比，算速度（%/分钟）和到 60%/70%/开池的预计分钟数。
输出（stdout JSON）：
  crossings   本次新跨过 30/60/70% 或刚开池（含提前开池 early_open）的盘 → 调用方据此立刻走巡逻第 2e 步（审计）/第 6d 步（autofill）
  hot         ≥30% 或 预计 30 分钟内到 70% 的盘（建议提前完成审计，写 audit_ok.json，到 70% 时 autofill 才来得及）
  pre_audit   hot 里还没有 audit_ok.json 结论的盘
  urgent      true = 有 ≥60% 跨档 / 刚开池 / 预计 ≤5 分钟到 70%：调用方应马上处理，不要等下一轮巡逻
  read_failed 读失败的盘数（>0 时如实写「进度读取失败」，不要当成没变化）
状态：fairmint/fastprogress_state.json（每盘最近 12 次读数 + 已提醒档位，自己的文件，不和 fairmint.py 抢写）。
用法：python3 fastprogress.py                 # 读一次
      python3 fastprogress.py --loop 100 --every 20   # 在 100 秒内每 20 秒读一次，有 urgent 立即输出并退出
      python3 fastprogress.py --min 30         # hot 门槛（默认 30%）
"""
import json, os, sys, time, fcntl
D = os.path.dirname(os.path.abspath(__file__)); L = os.path.dirname(D)
sys.path.insert(0, L); sys.path.insert(0, os.path.join(L, 'vendor')); sys.path.append('$AIDBOT_HOME/pylib')
import codeutil as CU
from rpcpool import batch

FOUND = os.path.join(D, 'found.json'); ST = os.path.join(D, 'fastprogress_state.json'); AOK = os.path.join(D, 'audit_ok.json')
CORE = {'jager': ('totalMinted()', 'opened()'), 'other': ('totalMinted()', 'opened()'), None: ('totalMinted()', 'opened()'),
        'jagent': ('sharesMinted()', 'poolOpen()'), 'boss': ('totalMinted()', 'finalized()'), 'mao': ('totalPaidShares()', 'liquidityTargetReached()')}
LEVELS = (30, 60, 70)
argv = sys.argv
MIN = float(argv[argv.index('--min') + 1]) if '--min' in argv else 30.0
LOOP = float(argv[argv.index('--loop') + 1]) if '--loop' in argv else 0
EVERY = float(argv[argv.index('--every') + 1]) if '--every' in argv else 20


def load(p, d):
    try: return json.load(open(p))
    except Exception: return d


def targets(found):
    out = []
    for a, v in found.items():
        if v.get('opened') or v.get('is_nft') or v.get('reserve_missing'): continue  # 资金没留作开池储备的（BNDOG 类）不进高频
        oa = v.get('open_at_shares')  # 只用合约里读到的开池份数；cap_guess（猜的总量）不进高频检查
        if not oa: continue
        out.append((a, v.get('template'), oa, v.get('name') or '未命名'))
    return out


def read_once(tg):
    calls = []
    for a, tpl, _, _ in tg:
        m, o = CORE.get(tpl, CORE[None])
        calls += [('eth_call', [{'to': a, 'data': '0x' + CU.sel(m)}, 'latest']), ('eth_call', [{'to': a, 'data': '0x' + CU.sel(o)}, 'latest'])]
    try: R = batch(calls, tolerant=True) if calls else []
    except Exception as e: return None, str(e)[:160]
    res = {}
    for j, (a, tpl, oa, nm) in enumerate(tg):
        m, o = R[2 * j], R[2 * j + 1]
        minted = int(m[:66], 16) if isinstance(m, str) and len(m) >= 66 else None
        opened = bool(int(o[:66], 16)) if isinstance(o, str) and len(o) >= 66 else None
        res[a] = (minted, opened)
    return res, None


def step(found, st, aok):
    now = time.time(); tg = targets(found)
    res, err = read_once(tg)
    out = {'time_cst': time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(now + 8 * 3600)), 'checked': len(tg), 'read_failed': 0,
           'crossings': [], 'hot': [], 'pre_audit': [], 'urgent': False}
    if res is None:
        out.update({'read_failed': len(tg), 'error': '所有 RPC 节点失败：' + err}); return out
    for a, tpl, oa, nm in tg:
        minted, opened = res[a]
        if minted is None: out['read_failed'] += 1; continue
        pct = round(min(minted * 100 / oa, 999), 2)
        s = st.setdefault(a, {'hist': [], 'alerted': []})
        hist = s['hist']; prev = hist[-1] if hist else None
        hist.append([int(now), minted]); del hist[:-12]
        # 速度：最近 10 分钟内最早一次读数到现在
        base = next((h for h in hist if now - h[0] <= 600 and h is not hist[-1]), None)
        rate = round((minted - base[1]) * 100 / oa / ((now - base[0]) / 60), 2) if base and now - base[0] >= 30 else None  # %/分钟
        eta = lambda lvl: (round((lvl - pct) / rate, 1) if rate and rate > 0 and pct < lvl else None)
        row = {'contract': a, 'name': nm, 'template': tpl, 'open_pct': pct, 'minted': minted, 'open_at_shares': oa,
               'rate_pct_per_min': rate, 'eta_70_min': eta(70), 'eta_open_min': eta(100),
               'bscscan': f'https://bscscan.com/address/{a}', 'autofill_supported': tpl in (None, 'jager')}
        if opened:
            kind = 'early_open' if minted < oa else 'open'
            if kind not in s['alerted']:
                s['alerted'].append(kind); out['crossings'].append(dict(row, level=kind)); out['urgent'] = True
            continue
        if pct > 100: continue  # 份数超过开池门槛仍未开池 = 开池份数读错或资金没留作储备（BNDOG），不报跨档、不进 hot
        for lvl in LEVELS:
            if pct >= lvl and lvl not in s['alerted']:
                s['alerted'].append(lvl); out['crossings'].append(dict(row, level=lvl, from_pct=round(prev[1] * 100 / oa, 2) if prev else None))
                if lvl >= 60: out['urgent'] = True
        if pct >= MIN or (row['eta_70_min'] is not None and row['eta_70_min'] <= 30):
            out['hot'].append(row)
            if a not in aok: out['pre_audit'].append({'contract': a, 'name': nm, 'open_pct': pct, 'bscscan': row['bscscan']})
        if row['eta_70_min'] is not None and row['eta_70_min'] <= 5: out['urgent'] = True
    out['hot'].sort(key=lambda r: -r['open_pct'])
    return out


def main():
    lk = open(ST + '.lock', 'w'); fcntl.flock(lk, fcntl.LOCK_EX)
    st = load(ST, {}); t_end = time.time() + LOOP; out = None
    while True:
        found = load(FOUND, {}); aok = load(AOK, {})
        o = step(found, st, aok)
        if out is None: out = o
        else:  # 循环模式：合并多次的跨档
            out['crossings'] += o['crossings']; out['urgent'] = out['urgent'] or o['urgent']
            for k in ('time_cst', 'checked', 'read_failed', 'hot', 'pre_audit'): out[k] = o[k]
        json.dump(st, open(ST + '.tmp', 'w')); os.replace(ST + '.tmp', ST)
        if out['urgent'] or not LOOP or time.time() + EVERY > t_end: break
        time.sleep(EVERY)
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == '__main__': main()
