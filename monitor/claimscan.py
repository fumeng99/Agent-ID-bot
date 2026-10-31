"""claimscan.py — 找我们各账号在已参与项目里「可以领」的东西（只读，不发交易）。
用户 2026-10-31 18:26 原话「其他需要领取的也要自动领取」。
做法：从 seen.json 取已铸/已买过的合约（及 extra_claim_targets.json 里手动加的地址），
用 Sourcify ABI 找无参数、nonpayable 的领取类函数（claim/claimReward/harvest/getReward/claimFeed/claimDividend/
claimAirdrop/release/withdrawReward 等；不含 unlock/withdraw 本金/transfer/approve），
对每个账号用 eth_call(from=账号) 模拟，成功（不 revert）就列为候选。
输出 JSON: {"candidates":[{"contract","name","fn","calldata","accounts":[label...]}], "checked":N}
之后由巡逻对每个候选跑 mint_many.py <合约> <calldata> <账号...>（preview 干净才 execute）。
"""
import sys, os, json, re, time, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rpcpool import batch
try:
    from Crypto.Hash import keccak
except Exception:
    keccak = None
SEL = {'claim': '0x4e71d92d', 'claimAll': '0xd1058e59', 'claimReward': '0xb88a802f', 'claimRewards': '0x372500ab', 'claimFeed': '0x8506b82a', 'claimDividend': '0xf0fc6bca', 'claimDividends': '0x668038e0', 'claimAirdrop': '0x5b88349d', 'claimYield': '0x406cf229', 'claimMining': '0x94a611da', 'claimTokens': '0x48c54b9d', 'harvest': '0x4641257d', 'getReward': '0x3d18b912', 'getRewards': '0x0572b0cc', 'withdrawReward': '0xc885bc58', 'withdrawRewards': '0xc7b8981c', 'release': '0x86d1a69f', 'collect': '0xe5225381', 'collectRewards': '0x70bb45b3', 'redeemRewards': '0xf0fc4ec1'}

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, 'claim_abi_cache.json')
ACC = json.load(open('$AIDBOT_HOME/baw/accounts/accounts.json'))
CLAIM_RE = re.compile(r'^(claim|claimAll|claimReward|claimRewards|claimFeed|claimDividend|claimDividends|claimAirdrop|'
                      r'claimYield|claimMining|claimTokens|harvest|getReward|getRewards|withdrawReward|withdrawRewards|'
                      r'release|collect|collectRewards|redeemRewards)$', re.I)
SKIP_CONTRACTS = {'0x01a451256ac22070d4af503fa7fe03b7337273e0'}  # Moribundus 祭坛由单独任务处理

def sel(sig):
    if keccak is None: return SEL.get(sig[:-2])
    k = keccak.new(digest_bits=256); k.update(sig.encode()); return '0x' + k.hexdigest()[:8]

def abi_of(addr, cache):
    if addr in cache: return cache[addr]
    abi = None
    try:
        u = f'https://sourcify.dev/server/v2/contract/56/{addr}?fields=abi'
        with urllib.request.urlopen(urllib.request.Request(u, headers={'User-Agent': 'x'}), timeout=15) as r:
            abi = json.load(r).get('abi')
    except Exception:
        abi = None
    cache[addr] = abi
    return abi

def targets():
    S = json.load(open(os.path.join(HERE, 'seen.json')))
    out = {}
    for x in S:
        c = (x.get('contract') or '').lower()
        st = str(x.get('status', ''))
        if not re.fullmatch(r'0x[0-9a-f]{40}', c) or c in SKIP_CONTRACTS: continue
        if '跳过' in st and '已' not in st: continue
        if re.search(r'已铸|已确认|到账|已领|minted|已买|bought|已提交|已广播', st) or x.get('bought'):
            out[c] = x.get('name', '')
    extra = os.path.join(HERE, 'extra_claim_targets.json')
    if os.path.exists(extra):
        for c, n in json.load(open(extra)).items(): out[c.lower()] = n
    return out

def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 80
    cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
    T = list(targets().items())
    # 轮转：每轮从上次位置继续
    st_path = os.path.join(HERE, 'claimscan_state.json')
    pos = json.load(open(st_path)).get('pos', 0) if os.path.exists(st_path) else 0
    if pos >= len(T): pos = 0
    batch_t = T[pos:pos + limit]
    json.dump({'pos': pos + limit, 'total': len(T), 'time': time.time()}, open(st_path, 'w'))
    fns = []
    for c, n in batch_t:
        abi = abi_of(c, cache)
        if not abi: continue
        for f in abi:
            if f.get('type') == 'function' and not f.get('inputs') and f.get('stateMutability') == 'nonpayable' \
                    and CLAIM_RE.match(f.get('name', '')):
                sd = sel(f['name'] + '()')
                if sd: fns.append((c, n, f['name'], sd))
    json.dump(cache, open(CACHE, 'w'))
    calls = [('eth_call', [{'from': a['bsc'], 'to': c, 'data': d}, 'latest']) for (c, n, fn, d) in fns for a in ACC]
    res = batch(calls, tolerant=True) if calls else []
    cands = []
    i = 0
    for (c, n, fn, d) in fns:
        ok = []
        for a in ACC:
            r = res[i]; i += 1
            if r is not None: ok.append(a['label'])
        if ok: cands.append({'contract': c, 'name': n, 'fn': fn, 'calldata': d, 'accounts': ok})
    print(json.dumps({'checked': len(batch_t), 'total_targets': len(T), 'claim_fns': len(fns), 'candidates': cands},
                     ensure_ascii=False, indent=1))

if __name__ == '__main__':
    main()
