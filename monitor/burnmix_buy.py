#!/usr/bin/env python3
"""BurnAgentMixed 类 fair mint 通用买入（2026-11-03 01:21 用户「按照你推荐优化」）。
合约特征：mint(id,qty,expectedStart,minTokens,deadline) / mintWithInviter(...,inviterId)、quotaOf/mintedById/quoteFor/totalMinted/opened。
expectedStart 必须等于 totalMinted+1（合约防抢跑），被别人抢先就 revert（只亏 gas）。优化：
  ① 失败不等待：立刻重读 totalMinted、用 quoteFor 重算成本再预演（不再 sleep 1 秒）；
  ② 六号并发（错开 0.4 秒起步，减少自己人互撞）；
  ③ 每号成本上限 --max-bnb（默认 0.025 BNB = 10 份 × 0.002 + 余量），超出不买；
  ④ unknown（发出去没回执）绝不重发，只记 txHash。
只在用户确认买入或已授权的自动打满规则下运行（autofill 调用时闸门照旧）。
usage: python3 burnmix_buy.py <mint合约> [--max-bnb 0.025] [--tries 40] [--dry] [labels...]
       python3 burnmix_buy.py <mint合约> --probe   → 输出是否此模板 + 实测储备比例（JSON）
"""
import sys, os, json, time, threading
args = sys.argv[1:]
def opt(k, d):
    return type(d)(args[args.index(k) + 1]) if k in args else d
MAXB = opt('--max-bnb', 0.025); FREE = '--free' in args; TRIES = opt('--tries', 40); PROBE = '--probe' in args
pos = [x for i, x in enumerate(args) if not x.startswith('--') and (i == 0 or args[i - 1] not in ('--max-bnb', '--tries'))]
M = pos[0].lower(); labels = pos[1:]
# 复用 fair_buy.py 的工具函数（sel/u/call/ci/handle/go/A/DRY…），参数位用占位
sys.argv = ['fair_buy.py', M, M, '10'] + (['--dry'] if '--dry' in args else [])
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fair_buy.py')).read()
exec(src.split('from concurrent.futures import ThreadPoolExecutor')[0])
from Crypto.Hash import keccak as _k


def tryci(data):
    try: return ci(M, data)
    except Exception: return None


def probe():
    q = call(M, '0x' + sel('quotaOf(uint256)') + u(1))
    mb = call(M, '0x' + sel('mintedById(uint256)') + u(1))
    tm = tryci('0x' + sel('totalMinted()'))
    is_t = bool(q and mb and tm is not None and len(q) >= 66)
    out = {'burnmix': is_t}
    if is_t:
        pl = tryci('0x' + sel('pendingLiquidity()')); fm = tryci('0x' + sel('freeMinted()')) or 0
        oa = tryci('0x' + sel('OPEN_AT()')); op = tryci('0x' + sel('opened()'))
        p1 = tryci('0x' + sel('priceAt(uint256)') + u(1))
        paid = (tm - fm) * p1 if p1 else None
        out.update(total_minted=tm, open_at=oa, opened=bool(op), pending_liq_bnb=pl / 1e18 if pl is not None else None,
                   paid_bnb=paid / 1e18 if paid else None, reserve_ratio=round(pl / paid, 4) if pl and paid else None)
    return out


if PROBE:
    try: print(json.dumps(probe()))
    except Exception as ex: print(json.dumps({'burnmix': None, 'err': str(ex)[:200]}))
    sys.exit()


def aid_of(a):
    h = handle(a['bsc']); return (h, int(_k.new(digest_bits=256, data=h.encode()).hexdigest(), 16)) if h else (None, None)


ACC = [a for a in A if not labels or a['label'] in labels]
MAINA = next((a for a in A if a['label'] == '主'), None)
MAIN_ID = aid_of(MAINA)[1] if MAINA else None
res, lock = [], threading.Lock()


def run(i, a):
    time.sleep(0.4 * i)
    h, aid = aid_of(a)
    if not aid: return [(a['label'], 'no_agent_id')]
    out, spent = [], 0
    for t in range(TRIES):
        try:
            if tryci('0x' + sel('opened()')): out.append((a['label'], 'opened_stop')); break
            minted = ci(M, '0x' + sel('mintedById(uint256)') + u(aid)); quota = ci(M, '0x' + sel('quotaOf(uint256)') + u(aid))
            n = min(10, quota - minted)
            if FREE:  # 2026-11-03 用户「为什么这个免费的没自动打成功」：前 N 个 ID 首份免费 → mint(id,1,…) value=0
                if tryci('0x' + sel('freeClaimed(uint256)') + u(aid)): out.append((a['label'], 'free_already')); break
                fs, fm_ = tryci('0x' + sel('FREE_SHARES()')), tryci('0x' + sel('freeMinted()'))
                if fs is not None and fm_ is not None and fm_ >= fs: out.append((a['label'], 'free_gone', fm_, fs)); break
                n = min(n, 1)
            if n <= 0: out.append((a['label'], h, 'full', minted, quota)); break
            q = call(M, '0x' + sel('quoteFor(uint256,uint8)') + u(aid) + u(n))
            cost, tokens = int(q[2:66], 16), int(q[66:130], 16)
            tm = ci(M, '0x' + sel('totalMinted()'))
        except Exception as ex:
            out.append((a['label'], 'read_err', str(ex)[:80])); time.sleep(0.5); continue
        if FREE and cost != 0: out.append((a['label'], 'not_free', cost / 1e18)); break
        if spent + cost > MAXB * 1e18: out.append((a['label'], 'cost_cap', cost / 1e18, spent / 1e18)); break
        minT = tokens * 97 // 100; dl = int(time.time()) + 300
        first_paid = tryci('0x' + sel('firstPaid(uint256)') + u(aid))
        if not FREE and a is not MAINA and MAIN_ID and not first_paid:
            data = '0x' + sel('mintWithInviter(uint256,uint8,uint256,uint256,uint256,uint256)') + u(aid) + u(n) + u(tm + 1) + u(minT) + u(dl) + u(MAIN_ID)
        else:
            data = '0x' + sel('mint(uint256,uint8,uint256,uint256,uint256)') + u(aid) + u(n) + u(tm + 1) + u(minT) + u(dl)
        st = go(a, M, data, value=str(cost))
        print(a['label'], t, n, tm + 1, st[0], str(st[1])[:80], flush=True)
        if st[0] in ('ok', 'dry_ok'):
            spent += cost; out.append((a['label'], h, st[0], n, cost / 1e18, st[1]))
            if st[0] == 'dry_ok' or FREE: break
            continue  # 有邀请额度时继续补
        if st[0] == 'unknown': out.append((a['label'], 'unknown_no_resend', st[1])); break
        if st[0] == 'unclean' and 'sim' not in str(st[1]): out.append((a['label'], 'unclean_stop', str(st[1])[:120])); break
        # preview_fail / execute_fail / reverted / sim 失败：多半被抢，立刻重来（不 sleep）
    else:
        out.append((a['label'], 'gave_up', TRIES))
    return out


ths = []
for i, a in enumerate(ACC):
    th = threading.Thread(target=lambda i=i, a=a: (lambda r: (lock.acquire(), res.extend(r), lock.release()))(run(i, a))); th.start(); ths.append(th)
for th in ths: th.join()
print(json.dumps(res, ensure_ascii=False))
