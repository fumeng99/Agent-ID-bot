#!/usr/bin/env python3
# Gloop (0x183c…e4fe) 合约内 PoW 免费 mint：keccak256(minter ++ nonce) 前 N 位为 0。每号 preview 干净才 execute。
import sys, os, json, time, subprocess, multiprocessing as mp
from Crypto.Hash import keccak
C = '0x183cbb52b3c7bcb2433d50de0add99b59f4de4fe'
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from confirm import confirm_tx

def work(args):
    addr, bits, start, step, q, stop = args
    pre = bytes.fromhex(addr[2:]); n = start; shift = 256 - bits
    k0 = keccak.new(digest_bits=256)
    while not stop.is_set():
        for _ in range(100000):
            k = keccak.new(digest_bits=256); k.update(pre + n.to_bytes(32, 'big'))
            if int.from_bytes(k.digest(), 'big') >> shift == 0:
                q.put(n); return
            n += step

def mine(addr, bits, secs=600):
    m = mp.Manager(); q = m.Queue(); stop = m.Event(); k = os.cpu_count() or 2
    salt = int.from_bytes(os.urandom(8), 'big') << 64
    ps = [mp.Process(target=work, args=((addr, bits, salt + i, k, q, stop),)) for i in range(k)]
    for p in ps: p.start()
    res = None; dl = time.time() + secs
    while time.time() < dl:
        try: res = q.get(timeout=1); break
        except Exception:
            if not any(p.is_alive() for p in ps): break
    stop.set()
    for p in ps: p.terminate()
    return res

def rpc_call(data):
    from rpcpool import rpc
    return rpc('eth_call', [{'to': C, 'data': data}, 'latest'])

if __name__ == '__main__':
    only = sys.argv[1:]
    accts = json.load(open('$AIDBOT_HOME/baw/accounts/accounts.json'))
    B = '$AIDBOT_HOME/baw/node_modules/.bin/baw'
    out = []
    for a in accts:
        if only and a['label'] not in only: continue
        tm = int(rpc_call('0xa2309ff8'), 16)
        bits = 18 if tm < 834 else 21 if tm < 1670 else 24 if tm < 2504 else 27
        hm = rpc_call('0x' + __import__('Crypto.Hash.keccak', fromlist=['x']).new(digest_bits=256, data=b'hasMinted(address)').hexdigest()[:8] + a['bsc'][2:].lower().rjust(64, '0'))
        if int(hm, 16) == 1: out.append((a['label'], 'already_minted')); continue
        t0 = time.time(); n = mine(a['bsc'], bits)
        if n is None: out.append((a['label'], 'mine_timeout', bits)); continue
        data = '0x' + keccak.new(digest_bits=256, data=b'mint(uint256)').hexdigest()[:8] + n.to_bytes(32, 'big').hex()
        env = dict(os.environ, HOME=a['home'], npm_config_cache='/tmp/npmcache')
        r = subprocess.run([B, 'contract-call', 'preview', '--binanceChainId', '56', '--from', a['bsc'], '--to', C, '--value', '0', '--inputData', data, '--gasLimit', '500000', '--json'], env=env, capture_output=True, text=True, timeout=120)
        try: p = json.loads(r.stdout)
        except Exception: out.append((a['label'], 'preview_fail', (r.stdout + r.stderr)[:200])); continue
        d = p.get('data') or {}; sr = d.get('simulationResult') or {}; rk = d.get('risks') or {}
        clean = (sr.get('simulationCode') == '000000000' and not sr.get('allowanceChanges') and not sr.get('authorityChanges')
                 and not rk.get('riskDetails') and not rk.get('riskBehaviors') and all(b.get('change', 0) > 0 for b in (sr.get('balanceChanges') or [])))
        if not clean: out.append((a['label'], 'preview_not_clean', sr.get('simulationCode'), str(sr.get('simulationErrorDetail'))[:120])); continue
        r2 = subprocess.run([B, 'contract-call', 'execute', '--requestId', d['requestId'], '--json'], env=env, capture_output=True, text=True, timeout=120)
        try: ex = json.loads(r2.stdout); tx = (ex.get('data') or {}).get('txHash')
        except Exception: tx = None
        out.append((a['label'], 'executed' if tx else 'exec_unknown', tx, round(time.time() - t0), bits))
        print(out[-1], flush=True)
        time.sleep(3)
    print(json.dumps(out, ensure_ascii=False))
