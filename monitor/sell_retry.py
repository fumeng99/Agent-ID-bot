#!/usr/bin/env python3
# 2026-11-05 16:58 用户：自动卖失败要快速重试（1 分钟太久）。fast_sell 失败后 5 秒重试，最多 3 次；每次仍由 fast_sell 先 quote 预演、滑点上限不变。
import subprocess, sys, time, json
args = sys.argv[1:]; label = args[-1]
for i in range(3):
    r = subprocess.run(['python3', 'fast_sell.py'] + args, capture_output=True, text=True)
    out = r.stdout + r.stderr; print(out, flush=True)
    ok = False
    for ln in out.splitlines():
        try:
            o = json.loads(ln); od = o.get('orders', {}).get(label)
            if od and 'got_u' in od: ok = True
        except Exception: pass
    if ok: break
    if i < 2: print(f'retry {i+1} in 5s', flush=True); time.sleep(5)
