#!/usr/bin/env python3
# 2026-11-05 16:58 用户：自动卖失败要快速重试（1 分钟太久）。
# 2026-11-06 用户「为什么卡单 彻底解决」：
#  1) 同一币同一时刻只跑一个卖单进程（flock 排队），不再多个号/多个进程同时打同一池子、抢同一余额；
#  2) 首次失败 5 秒重试，之后 15/30/60 秒退避，共 5 次；限流（15002）多等 10 秒；余额为 0 即视为已卖完；
#  3) 仍失败交给 agc_sweep.py 每轮按链上余额补卖（不会再留残余）。滑点等规则不变（fast_sell 内 quote 预演）。
import subprocess, sys, time, json, fcntl, os
args = sys.argv[1:]; label = args[-1]; tok = args[0].lower()
D = os.path.dirname(os.path.abspath(__file__))
lk = open(f'/tmp/sell_{tok[2:10]}.lock', 'w')
fcntl.flock(lk, fcntl.LOCK_EX)  # 排队，不并发
waits = [5, 15, 30, 60]
for i in range(5):
    r = subprocess.run(['python3', os.path.join(D, 'fast_sell.py')] + args, capture_output=True, text=True, cwd=D)
    out = r.stdout + r.stderr; print(out, flush=True)
    ok = False; limited = False
    for ln in out.splitlines():
        try:
            o = json.loads(ln); od = o.get('orders', {}).get(label)
            if od == 'no_balance' or (isinstance(od, dict) and 'got_u' in od): ok = True
        except Exception: pass
    if '15002' in out or 'Too many requests' in out: limited = True
    if ok: break
    if i < 4:
        w = waits[i] + (10 if limited else 0)
        print(f'retry {i+1} in {w}s', flush=True); time.sleep(w)
