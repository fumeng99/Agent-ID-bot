#!/usr/bin/env python3
"""报价型 mint 抢跑器（FLOKI 模板：mint(id,qty,expectMinted,minTokens,deadline) / mintWithInviter(...,inviterId)）。
六号各一个线程并发、紧循环：读 totalMinted → 拼 calldata(期望值=已铸+1) → baw preview → 立即 execute → 查回执。
只在用户当场确认（--ok "<原话>"）时运行；value 固定为 单价×份数，不改金额、不换币。成功/售罄/额度 0/超时 停。
用法：python3 racer.py --to <合约> --price 0.003 [--qty 10] [--secs 600] [--dry] --ok "原话" 主 001 ...
"""
import sys, os, json, time, subprocess, threading
sys.path.insert(0, '$AIDBOT_HOME/bsc-mint-skill'); sys.path.insert(0, '$AIDBOT_HOME/bsc-mint-skill/vendor')
import chain
a = sys.argv[1:]
def opt(k, d=None): return a[a.index(k)+1] if k in a else d
TO = opt('--to').lower(); PRICE = float(opt('--price')); QTY = int(opt('--qty', '10')); SECS = int(opt('--secs', '600'))
DRY = '--dry' in a; QMAP = json.loads(opt('--qtymap', '{}')); AUTO = '--auto' in a; PU = float(opt('--bnbu', '0')); CAP = int(opt('--cap', '0')); MINPS = int(float(opt('--min-per-share', '0')) * 1e18); OK = opt('--ok'); MAINLBL = opt('--main', '主')
if not OK: sys.exit('需要 --ok "<用户原话>"')
labels = [x for i, x in enumerate(a) if not x.startswith('--') and (i == 0 or a[i-1] not in ('--to','--price','--qty','--secs','--ok','--main','--cap','--min-per-share','--qtymap','--bnbu'))]
ACC = {x['label']: x for x in json.load(open('$AIDBOT_HOME/baw/accounts/accounts.json'))}
HANDLE = lambda x: ACC[x]['agentId'].replace('.ai', '')
S = dict(totalMinted='0xa2309ff8', quota='0x786c90c2', mint='0xae3dfa28', inv='0x760b22a2', preview='0xb3d7f6b9')
U = chain.enc_uint
NODE = ['node', '$AIDBOT_HOME/baw/node_modules/@binance/agentic-wallet/dist/index.js']
LOG = open(f'$AIDBOT_HOME/quote-racer/race_{TO[:10]}.log', 'a'); L = threading.Lock()
def log(**k):
    k['t'] = time.strftime('%H:%M:%S')
    with L: LOG.write(json.dumps(k, ensure_ascii=False) + '\n'); LOG.flush(); print(json.dumps(k, ensure_ascii=False), flush=True)
def baw(acc, args, t=60):
    env = dict(os.environ, HOME=acc['home'], npm_config_cache='/tmp/npmcache')
    iid = os.path.join(acc['home'], 'instance_id')
    if os.path.exists(iid): env['BINANCE_INSTANCE_ID'] = open(iid).read().strip()
    r = subprocess.run(NODE + args + ['--json'], env=env, capture_output=True, text=True, timeout=t)
    try: return json.loads(r.stdout)
    except Exception: return {'success': False, 'raw': (r.stdout + r.stderr)[-200:]}
def book(lbl, qty, tx):
    # 预授权自动抢：记入 autofill.json，计入每号每项目 30U / 每天 100U 额度
    import fcntl
    p = '$AIDBOT_DATA/autofill.json'
    with L, open(p + '.qr.lock', 'w') as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try: led = json.load(open(p))
        except Exception: led = {}
        day = time.strftime('%Y-%m-%d', time.gmtime(time.time() + 8 * 3600))
        d = led.setdefault(day, {'spent_u': 0, 'buys': []}); u = round(PRICE * qty * PU + 0.02, 2)
        d['buys'].append({'name': 'quote-racer', 'contract': TO, 'executor': 'quote_racer', 'per_account_u': {lbl: u}, 'tx': tx, 'qty': qty, 'time': time.strftime('%H:%M', time.gmtime(time.time() + 8 * 3600))})
        d['spent_u'] = round(d.get('spent_u', 0) + u, 2); json.dump(led, open(p, 'w'), ensure_ascii=False, indent=1)
_DIST = '$AIDBOT_HOME/baw/node_modules/@binance/agentic-wallet/dist'
if not os.path.exists(_DIST + '/qrworker.mjs') or os.path.getmtime(_DIST + '/qrworker.mjs') < os.path.getmtime(_DIST + '/index.js'):
    subprocess.run(['python3', '$AIDBOT_HOME/quote-racer/make_qrworker.py'], check=True)
class Worker:
    # 常驻 baw worker（qrworker.mjs）：preview 约 0.45s（CLI 约 1.0s），preview 干净当场 execute，少一次往返
    def __init__(s, acc):
        env = dict(os.environ, HOME=acc['home'], npm_config_cache='/tmp/npmcache')
        iid = os.path.join(acc['home'], 'instance_id')
        if os.path.exists(iid): env['BINANCE_INSTANCE_ID'] = open(iid).read().strip()
        s.p = subprocess.Popen(['node', '$AIDBOT_HOME/baw/node_modules/@binance/agentic-wallet/dist/qrworker.mjs'], env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
        s.ready = json.loads(s.p.stdout.readline() or '{}').get('ready'); s.res = {}; s.ev = {}; s.lk = threading.Lock(); s.n = 0
        threading.Thread(target=s._rd, daemon=True).start()
        if s.ready:  # 预热一次（首个请求约 0.75s，之后约 0.4s）
            s.go({'binanceChainId': '56', 'from': acc['bsc'], 'to': '0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c', 'value': '0', 'inputData': '0x70a08231' + acc['bsc'][2:].lower().rjust(64, '0')}, True)
    def _rd(s):
        for ln in s.p.stdout:
            try: j = json.loads(ln)
            except Exception: continue
            i = j.get('id')
            if i in s.ev: s.res[i] = j; s.ev[i].set()
    def go(s, body, dry, t=30):
        with s.lk: s.n += 1; i = str(s.n); s.ev[i] = threading.Event()
        s.p.stdin.write(json.dumps({'id': i, 'body': body, 'dry': dry}) + '\n'); s.p.stdin.flush()
        s.ev[i].wait(t); return s.res.pop(i, {'stage': 'error', 'err': 'worker 超时'})
def _tm(): return int(chain.call(TO, S['totalMinted'])[1], 16)
TM = {'v': None}
def _poll():  # 后台 3 路并发轮询 totalMinted（各约 0.3s，错开 0.1s），lane 直接取最新值，省掉每次 0.3s 读链
    def one(k):
        time.sleep(0.1 * k)
        while True:
            try:
                v = _tm()
                if TM['v'] is None or v > TM['v']: TM['v'] = v
            except Exception: time.sleep(0.2)
    for k in range(3): threading.Thread(target=one, args=(k,), daemon=True).start()
def tm():
    while TM['v'] is None: time.sleep(0.05)
    return TM['v']
def run(lbl):
    acc = ACC[lbl]; me = int(chain.kec(HANDLE(lbl).encode()), 16); mainid = int(chain.kec(HANDLE(MAINLBL).encode()), 16)
    end = time.time() + SECS; W = Worker(acc)
    if not W.ready: return log(acc=lbl, state='worker_not_ready（会话掉线？）')
    ok, q = chain.call(TO, S['quota'] + U(me)); q = int(q, 16) if ok else 0
    want = QMAP.get(lbl, QTY)
    # 份数 = 剩余 quota 时，并发 3 路交错发（多买彩票）：第二笔成交也会因 quota 用完回滚，不会超买；份数 < quota 时只 1 路
    K = 3 if (q and want >= q) else 1
    stop = threading.Event(); cnt = [0]; qq = [q]; qt = [time.time()]
    def lane(k):
        time.sleep(0.15 * k)
        while time.time() < end and not stop.is_set():
            try:
                if time.time() - qt[0] > 3:  # quota 每 3 秒刷一次（成交后靠回执停，不靠它）
                    ok, qv = chain.call(TO, S['quota'] + U(me)); qq[0] = int(qv, 16) if ok else 0; qt[0] = time.time()
                q = qq[0]
                if q == 0: stop.set(); return log(acc=lbl, state='quota_0_or_done')
                m = tm(); cap = CAP or 10000
                if m >= cap: stop.set(); return log(acc=lbl, state='sold_out')
                qty = min(q, want, cap - m)
                if qty <= 0: stop.set(); return log(acc=lbl, state='budget_0')
                args = [me, qty, m + 1, MINPS * qty, int(time.time()) + 880]
                data = (S['mint'] if lbl == MAINLBL else S['inv']) + ''.join(U(x) for x in args) + ('' if lbl == MAINLBL else U(mainid))
                body = {'binanceChainId': '56', 'from': acc['bsc'], 'to': TO, 'value': str(int(round(PRICE * 1e18)) * qty), 'inputData': data}
                r = W.go(body, DRY); cnt[0] += 1
                if r.get('stage') == 'error': continue  # 被抢/报价过期，立刻下一轮
                if r.get('stage') == 'preview':
                    if not r.get('clean'): log(acc=lbl, state='unclean', detail=r.get('detail')); continue
                    stop.set(); return log(acc=lbl, state='dry_ok', tries=cnt[0], ms=r.get('ms'))
                tx = r.get('txHash')
                if not tx: continue
                for _ in range(20):
                    rc = chain.rpc('eth_getTransactionReceipt', [tx])
                    if rc: break
                    time.sleep(0.5)
                st = (rc or {}).get('status')
                log(acc=lbl, state='ok' if st == '0x1' else ('reverted' if st else 'unknown'), tx=tx, qty=qty, tries=cnt[0], lane=k, ms=r.get('ms'))
                if st == '0x1' and AUTO: book(lbl, qty, tx)
                if st == '0x1' or not st: stop.set(); return  # 成功或结果不确定都不再发
            except Exception as ex:
                log(acc=lbl, state='error', detail=str(ex)[:150]); time.sleep(0.3)
    ls = [threading.Thread(target=lane, args=(k,)) for k in range(K)]
    [t.start() for t in ls]; [t.join() for t in ls]
    if not stop.is_set(): log(acc=lbl, state='timeout', tries=cnt[0])
    W.p.kill()
_poll()
log(state='start', to=TO, accounts=labels, ok=OK, dry=DRY)
ths = [threading.Thread(target=run, args=(x,)) for x in labels]
[t.start() for t in ths]; [t.join() for t in ths]
log(state='end')
