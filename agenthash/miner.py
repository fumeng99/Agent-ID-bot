#!/usr/bin/env python3
# PoW 矿工：找 nonce 使 sha256(miner20||challenge32||nonce32) <= target。用法: miner.py <miner> <challenge> <target_hex> [max_seconds]
import sys, os, hashlib, time, multiprocessing as mp
def work(args):
    prefix, target, start, step, deadline, q = args
    base = hashlib.sha256(prefix); n = start
    while True:
        for _ in range(200000):
            h = base.copy(); h.update(n.to_bytes(32,'big'))
            if int.from_bytes(h.digest(),'big') <= target:
                q.put(n); return
            n += step
        if time.time() > deadline: return
if __name__ == "__main__":
    miner, ch, tgt = sys.argv[1], sys.argv[2], int(sys.argv[3],16)
    secs = float(sys.argv[4]) if len(sys.argv)>4 else 600
    prefix = bytes.fromhex(miner[2:].rjust(40,'0')) + bytes.fromhex(ch[2:].rjust(64,'0'))
    salt = int.from_bytes(os.urandom(16),'big') << 64
    m = mp.Manager(); q = m.Queue(); k = os.cpu_count() or 2
    dl = time.time()+secs
    ps = [mp.Process(target=work, args=((prefix,tgt,salt+i,k,dl,q),)) for i in range(k)]
    for p in ps: p.start()
    res=None
    while time.time() < dl+5:
        try: res = q.get(timeout=1); break
        except Exception:
            if not any(p.is_alive() for p in ps): break
    for p in ps: p.terminate()
    print(res if res is not None else "NOTFOUND")
