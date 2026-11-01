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
DAEMON = '--daemon' in argv  # 常驻模式（fmkick.sh 启动）：不因 urgent 退出，事件逐条写 discover_out/fp_events.jsonl 供任务汇报
EVERY = float(argv[argv.index('--every') + 1]) if '--every' in argv else 20
# 2026-11-01 20:50 修偶发 40 秒超时：①单次读（discover_all）以前用阻塞锁，常驻进程一直占着锁 → 等到被外层 timeout 杀、输出空文件；
#   ②一轮 300+ 个 eth_call 分 8 批串行、每节点 10 秒超时，慢节点时 >22 秒。现在：单次请求 4 秒超时 + 4 批并发 + 本轮 RPC 截止 RPC_BUDGET 秒，
#   单次模式整体看门狗 FP_DEADLINE 秒（默认 32，留给外层 40 秒），超时/全失败就输出上一轮安全结果（stale:true），不再输出空文件。
RPC_TIMEOUT = float(os.environ.get('FP_RPC_TIMEOUT', 4)); RPC_BUDGET = float(os.environ.get('FP_RPC_BUDGET', 18))
FP_DEADLINE = float(os.environ.get('FP_DEADLINE', 32)); T0 = time.time()
LAST = os.path.join(D, 'fastprogress_last.json'); LIVE = os.path.join(L, 'discover_out', 'fp_live.json')


def safe_last(reason):
    """上一轮安全结果：常驻进程心跳新鲜就用 fp_live.json，否则用 fastprogress_last.json；去掉 crossings/fastfill（已汇报过，避免重复触发）。"""
    src, o = None, None
    try:
        lv = json.load(open(LIVE))
        if time.time() - lv.get('heartbeat', 0) < 90: src, o = 'daemon_live', lv
    except Exception: pass
    if o is None:
        try: src, o = 'last', json.load(open(LAST))
        except Exception: src, o = 'none', {'checked': 0, 'hot': [], 'pre_audit': []}
    o = dict(o); o.update({'crossings': [], 'fastfill_started': [], 'urgent': False, 'stale': src != 'daemon_live', 'source': src, 'note': reason})
    if src == 'none': o['rpc_degraded'] = True
    return o


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
    try: R = batch(calls, tolerant=True, timeout=RPC_TIMEOUT, deadline=time.time() + RPC_BUDGET, parallel=4) if calls else []
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
        # 2026-11-01 20:47 用户采纳「热盘提前预警」：≥30% 且速度 ≥2%/分钟 → hot_alert（每盘只一次，记在本状态文件 alerted 里），
        # 调用方用 `python3 fairmint/hotbrief.py <合约>` 生成完整简报，结尾等用户「确认买入」；之后跨 60%/70% 由 crossings 各报一次。
        if pct >= 30 and (rate or 0) >= 2 and 'hot_alert' not in s['alerted']:
            s['alerted'].append('hot_alert'); out.setdefault('hot_alert', []).append(row)
        if pct >= MIN or (row['eta_70_min'] is not None and row['eta_70_min'] <= 30):
            if pct >= 50 and (DAEMON or time.time() - T0 < FP_DEADLINE - 12):  # B1 预热（单次模式剩余时间不够就不扫，避免超时）：提前把 Agent ID 铸造钱包数算进缓存，70% 时 autofill 不用临时扫
                try:
                    import minters as _MN; row['ai_minters'] = (_MN.ai_minters(a, budget=8) or {}).get('ai_minters')
                except Exception: pass
            out['hot'].append(row)
            if a not in aok: out['pre_audit'].append({'contract': a, 'name': nm, 'open_pct': pct, 'bscscan': row['bscscan']})
        if row['eta_70_min'] is not None and row['eta_70_min'] <= 5: out['urgent'] = True
        # 动态调整（用户 2026-11-01 17:41「快打满的监控频率应该动态调整」）：预计 15 分钟内到 70% 且还没审计 → 提前审计（只读），70% 时直接打满
        if row['eta_70_min'] is not None and row['eta_70_min'] <= 15 and a not in aok and 'early_audit' not in s['alerted']:
            s['alerted'].append('early_audit'); out['urgent'] = True; out.setdefault('early_audit', []).append(row)
        # 70% 快速通道：已有审计通过结论的盘一跨 70% 立即后台跑 fairmint 刷新 + autofill（autofill 内含全部闸门、加锁、filled 防重复），不等 Agent 接手
        # 2026-11-01 安小将教训：Jager 模板盘一有热度（≥10% 或 ≥2%/分钟 或 预计 30 分钟内到 70%）且没有审计结论 → 后台立刻跑只读自动审计 autoaudit.py，
        # 不等 Agent；结论写 audit_ok.json（auto:true），70% 快速通道直接可用。人工结论优先，不覆盖。
        if a not in aok and tpl == 'jager' and 'auto_audit' not in s['alerted'] and (pct >= 10 or (rate or 0) >= 2 or (row['eta_70_min'] or 999) <= 30):
            s['alerted'].append('auto_audit'); out.setdefault('auto_audit_started', []).append(a)
            import subprocess
            subprocess.Popen(['bash', '-c', f'cd {D} && timeout 150 python3 autoaudit.py {a} >> {L}/discover_out/autoaudit.log 2>&1'], start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # 2026-11-01 19:27 BNBHolder：自动审计因「源码未验证」不通过的热盘，开池前每 2 分钟重跑一次只读审计（项目方常在开铸后才验证源码）；人工结论不动
        _ao = aok.get(a) or {}
        if _ao.get('auto') and not _ao.get('ok') and '未验证' in str(_ao.get('note', '')) and tpl == 'jager' and pct < 100 and time.time() - s.get('reaudit_at', 0) >= 120:
            s['reaudit_at'] = time.time(); out.setdefault('auto_audit_started', []).append(a)
            import subprocess
            subprocess.Popen(['bash', '-c', f'cd {D} && timeout 150 python3 autoaudit.py {a} >> {L}/discover_out/autoaudit.log 2>&1'], start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # 2026-11-01 安小将教训：已审计通过、还没到 70% 的热盘 = armed，主循环不因 urgent 退出，继续 5 秒盯到 70% 触发快速通道
        if pct < 70 and (aok.get(a) or {}).get('ok') and 'fastfill' not in s['alerted'] and (pct >= 30 or (rate or 0) >= 3):
            out.setdefault('armed', []).append(a)
        if pct >= 70 and (aok.get(a) or {}).get('ok') and 'fastfill' not in s['alerted'] and '--no-fastfill' not in argv:
            s['alerted'].append('fastfill'); out.setdefault('fastfill_started', []).append(a)
            import subprocess
            # 2026-11-01 安小将教训：先 fairmint.py 全量刷新要 ~110 秒，17%/分钟的盘 70→100% 不到 2 分钟就开池。
            # 改为把本次实测进度直接写进 discover_out/fairmint.json 的 watch_unopened，立刻跑 autofill（闸门不变），fairmint 刷新放到后面
            try:
                fp = f'{L}/discover_out/fairmint.json'; fm = load(fp, {}); wl = fm.setdefault('watch_unopened', [])
                base_row = dict(found.get(a) or {}); base_row.update(row); base_row['contract'] = a; base_row['open_pct'] = pct
                wl[:] = [x for x in wl if x.get('contract', '').lower() != a] + [base_row]
                json.dump(fm, open(fp + '.tmp', 'w'), ensure_ascii=False); os.replace(fp + '.tmp', fp)
            except Exception as e: out.setdefault('fastfill_err', []).append(str(e)[:120])
            subprocess.Popen(['bash', '-c', f'cd {L} && timeout 900 python3 autofill.py > discover_out/autofill_fast.json 2>&1; timeout 110 python3 fairmint/fairmint.py --minutes 10 > discover_out/fairmint.fast.json 2>/dev/null && mv discover_out/fairmint.fast.json discover_out/fairmint.json'], start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    out['hot'].sort(key=lambda r: -r['open_pct'])
    return out


def main():
    lk = open(ST + '.lock', 'w')
    if DAEMON:
        try: fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError: print(json.dumps({'daemon': 'already_running'})); return
        open(f'{L}/discover_out/fp_daemon.pid', 'w').write(str(os.getpid()))
    else:
        import signal
        def _wd(*_):  # 看门狗：到 FP_DEADLINE 还没跑完 → 输出上一轮安全结果，正常退出
            print(json.dumps(dict(safe_last(f'本轮超过 {FP_DEADLINE:.0f} 秒未完成，沿用上一轮结果'), timeout=True), ensure_ascii=False, indent=1)); sys.stdout.flush(); os._exit(0)
        signal.signal(signal.SIGALRM, _wd); signal.alarm(int(FP_DEADLINE))
        try: fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:  # 常驻进程占着锁 = 它正在盯（事件已写 fp_events.jsonl）；直接输出它的最新读数，不再阻塞等锁
            print(json.dumps(safe_last('常驻进程运行中，输出其最新读数'), ensure_ascii=False, indent=1)); return
    st = load(ST, {}); t_end = time.time() + LOOP; out = None
    while True:
        found = load(FOUND, {}); aok = load(AOK, {})
        o = step(found, st, aok)
        if out is None: out = o
        else:  # 循环模式：合并多次的跨档
            out['crossings'] += o['crossings']; out['urgent'] = out['urgent'] or o['urgent']
            for k in ('time_cst', 'checked', 'read_failed', 'hot', 'pre_audit'): out[k] = o[k]
            out['fastfill_started'] = out.get('fastfill_started', []) + o.get('fastfill_started', [])
            out['hot_alert'] = out.get('hot_alert', []) + o.get('hot_alert', [])
        json.dump(st, open(ST + '.tmp', 'w')); os.replace(ST + '.tmp', ST)
        if DAEMON:
            ev = {k: o[k] for k in ('crossings', 'hot_alert', 'early_audit', 'fastfill_started', 'auto_audit_started', 'fastfill_err') if o.get(k)}
            if ev:
                ev['time_cst'] = o['time_cst']; ev['hot'] = o['hot'][:5]
                with open(f'{L}/discover_out/fp_events.jsonl', 'a') as fe: fe.write(json.dumps(ev, ensure_ascii=False) + '\n')
            json.dump(dict(o, interval_s=None, heartbeat=int(time.time())), open(f'{L}/discover_out/fp_live.json', 'w'), ensure_ascii=False)
        # 动态频率：最快的盘预计 ≤15 分钟到 70% 或进度 ≥55% → 每 5 秒；≤30 分钟或 ≥40% → 每 10 秒；有热盘 → EVERY（20 秒）；没有热盘 → 30 秒
        etas = [r['eta_70_min'] for r in o['hot'] if r.get('eta_70_min') is not None]; top = max([r['open_pct'] for r in o['hot']] or [0])
        e = min(etas) if etas else 999
        armed = o.get('armed') or []
        if armed: t_end = max(t_end, time.time() + 600)  # armed 盘在场：至少再盯 10 分钟，不让任务时限把它切掉
        rmax = max([r.get('rate_pct_per_min') or 0 for r in o['hot']] or [0])
        # 用户 2026-11-01 18:04「项目上升快就 3s 监控一次，动态调整」：按上涨速度调频——任一热盘 ≥5%/分钟 或 armed → 3 秒；≥2%/分钟 → 5 秒
        gap = 3 if (armed or rmax >= 5) else 5 if (rmax >= 2 or e <= 15 or top >= 55) else 10 if (e <= 30 or top >= 40) else (EVERY if o['hot'] else 15)
        out['interval_s'] = gap
        if DAEMON and time.time() + gap <= t_end: time.sleep(gap); continue
        if not DAEMON and o.get('error'):  # RPC 全失败：输出上一轮安全结果而不是空列表
            out = dict(safe_last('本轮 RPC 全部失败：' + o['error'][:100]), read_failed=o['read_failed'], rpc_degraded=True); break
        if (out['urgent'] and not armed) or not LOOP or time.time() + gap > t_end: break
        time.sleep(gap)
    if not DAEMON:
        signal_off = getattr(__import__('signal'), 'alarm', None); signal_off and signal_off(0)
        if not out.get('stale') and not out.get('error'):
            try: json.dump(out, open(LAST + '.tmp', 'w'), ensure_ascii=False); os.replace(LAST + '.tmp', LAST)
            except Exception: pass
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == '__main__': main()
