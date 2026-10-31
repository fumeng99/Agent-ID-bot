#!/usr/bin/env python3
"""巡逻汇报行生成器（2026-11-01 审计后新增）。只读，不签名、不交易。
起因：巡逻消息里反复出现「BscScan： 、」「Tangpingfrog ｜Hydrahamster ｜」「KENRAI｜001｜（后面空）」、
顶部一串没有名字的裸链接——链接是手写的，文字为空或被挪走。这里统一生成「名字和链接在同一行、链接文字非空」的行，
巡逻直接原样粘贴，不再手写链接。

链接格式：[项目名 0xabcd…1234](https://bscscan.com/...)。链接文字一定非空；名字读不到写「未命名」。

用法：
  python3 report.py fairmint            # 「▍Fair mint 进度」全部未开池行 + 已部署未开铸/刚开铸/刚开池/≥60% 行（读 discover_out/fairmint.json）
  python3 report.py fast                # fastprogress.py 的跨档/提速提醒行（读 stdin 或 fairmint/fastprogress_last.json）
  python3 report.py candidates          # chainscan/regscan 新候选：每个一行「名字 + 合约链接 + 函数 + 来源」
  python3 report.py tx --name KENRAI --contract 0x… [--kind 免费mint] [--qty 1] [--u 0] < mint_many 输出
                                        # 每个账号一行：项目｜账号｜动作｜数量｜金额 U｜到账状态｜tx 链接（每个号都有自己的 tx）
  python3 report.py link <名字> <合约> [代币]   # 单个项目的一行链接（合约 + 代币 + Sourcify）
输出：纯文本行，每行一条，直接贴进消息对应区块。
"""
import ast, json, os, re, sys, time
D = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, D)
O = os.environ.get('REPORT_DIR') or os.path.join(D, 'discover_out')
STATE_CN = {'confirmed': '已确认到账', 'ok': '已确认到账', 'onchain_no_balance': '已上链-余额未变', 'reverted': '失败(revert)', 'unknown': '未确认(超时，不要重发)',
            'timeout': '未确认(超时，不要重发)', 'preview_fail': '预演失败-未执行', 'unclean': '预演有风险-未执行', 'flap_block': '风控拦截-未执行',
            'execute_fail': '提交失败-未执行', 'dry_ok': '预演通过(未执行)'}


def short(a): return (a[:6] + '…' + a[-4:]) if a and a.startswith('0x') and len(a) >= 42 else (a or '')
def addr_url(a): return f'https://bscscan.com/address/{a}'
def tok_url(a): return f'https://bscscan.com/token/{a}'
def tx_url(h): return f'https://bscscan.com/tx/{h}'
def md(text, url):
    text = (text or '').strip() or '链接'
    return f'[{text}]({url})'
def name_of(n): return (n or '').strip() or '未命名'


def bnb_u():
    cache = '/tmp/report_bnbu.json'
    try:
        c = json.load(open(cache))
        if time.time() - c['t'] < 600: return c['v']
    except Exception: pass
    try:
        from rpcpool import batch
        U = '0x55d398326f99059ff775485246999027b3197955'; WBNB = '0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c'; R = '0x10ed43c718714eb63d5aa57b78b54704e256024e'
        r = batch([('eth_call', [{'to': R, 'data': '0xd06ca61f' + hex(10**18)[2:].rjust(64, '0') + '40'.rjust(64, '0') + '2'.rjust(64, '0') + WBNB[2:].rjust(64, '0') + U[2:].rjust(64, '0')}, 'latest'])])[0]
        v = int(r[-64:], 16) / 1e18
        json.dump({'t': time.time(), 'v': v}, open(cache, 'w')); return v
    except Exception:
        return None


def price_u(r, bu):
    if r.get('cost_1share_token_units') and r.get('price_currency') == 'USDT': return f"{r['cost_1share_token_units']:.2f} U"
    if r.get('cost_1share_bnb'):
        return f"{r['cost_1share_bnb'] * bu:.2f} U" if bu else f"{r['cost_1share_bnb']:.4f} BNB"
    if r.get('cost_1share_token_units'): return f"{r['cost_1share_token_units']:g} 代币/份"
    return '单价未读到'


def fm_line(r, bu, found):
    v = found.get(r['contract'], {})
    nm = name_of(r.get('name') or v.get('name'))
    head = md(f"{nm} {short(r['contract'])}", r.get('bscscan') or addr_url(r['contract']))
    oa = r.get('open_at_shares')
    prog = f"{r['open_pct']:.1f}%（{r.get('total_minted') if r.get('total_minted') is not None else '?'}/{oa if oa else '?'}）"
    tags = []
    if r.get('open_pct_guess'): tags.append('猜测值')
    if r.get('stale'): tags.append('本轮读取失败，显示上轮数')
    if r.get('autofill_supported') is False: tags.append(f"{r.get('template')} 模板，≥70% 只提醒")
    if r.get('can_open_early') == 'treasury': tags.append('项目方可提前开池')
    elif r.get('can_open_early') == 'anyone_after_deadline': tags.append(f"到期后任何人可开池{('（' + r['deadline_cst'] + '）') if r.get('deadline_cst') else ''}")
    if r.get('has_refund'): tags.append('有退款函数')
    if r.get('premint'): tags.append('已部署未开铸' if not r.get('total_minted') else '刚开铸')
    d = r.get('delta_pct') or 0
    line = f"{head}｜{price_u(r, bu)}｜{prog}｜Δ{d:+.1f}"
    if v.get('token'): line += '｜' + md('代币', tok_url(v['token']))
    if tags: line += '｜' + '，'.join(tags)
    return line


def cmd_fairmint():
    try: fm = json.load(open(os.path.join(O, 'fairmint.json')))
    except Exception as e: print(f'Fair mint 进度：读取 discover_out/fairmint.json 失败（{e}）'); return
    try: found = json.load(open(os.path.join(D, 'fairmint', 'found.json')))
    except Exception: found = {}
    bu = bnb_u()
    if fm.get('rpc_degraded'): print(f"⚠️ 本轮 {fm.get('read_failed')} 个盘进度读取失败（RPC 限流），下面标「本轮读取失败」的是上轮数")
    sec = [('▍刚开池', fm.get('just_opened', [])), ('▍≥60% / 单轮暴涨', fm.get('near_open', [])), ('▍猜测总量 ≥60%（先核实总量）', fm.get('near_open_guess', [])),
           ('▍刚开铸', fm.get('mint_started', [])), ('▍已部署未开铸（新）', fm.get('premint_new', []))]
    for title, rows in sec:
        if not rows: continue
        print(title)
        for r in rows:
            r = dict(r); r.setdefault('open_pct', 0)
            ln = fm_line(r, bu, found)
            if r.get('early_open'): ln += f"｜⚠️提前开池（开池时约 {r.get('open_pct_at_open')}%）"
            if r.get('deployer'): ln += '｜部署者 ' + md(short(r['deployer']), addr_url(r['deployer'])) + (f"（同部署者 {r['dev_contracts']} 个盘）" if r.get('dev_contracts', 0) >= 3 else '')
            print(ln)
    rows = fm.get('watch_unopened', [])
    print(f"▍Fair mint 进度（未开池 {len(rows)} 个，按开池进度降序；开池进度 = 已铸 ÷ 开池份数）")
    for r in rows: print(fm_line(r, bu, found))


def cmd_fast():
    raw = sys.stdin.read() if not sys.stdin.isatty() else ''
    try: o = json.loads(raw) if raw.strip() else json.load(open(os.path.join(D, 'fairmint', 'fastprogress_last.json')))
    except Exception as e: print(f'高频进度：没有可读输出（{e}）'); return
    for c in o.get('crossings', []):
        lvl = c['level']; what = {'open': '已开池', 'early_open': '⚠️提前开池（未到开池份数）'}.get(lvl, f'跨过 {lvl}%')
        eta = f"，预计 {c['eta_open_min']} 分钟后开池" if c.get('eta_open_min') else ''
        print(f"{md(name_of(c['name']) + ' ' + short(c['contract']), c['bscscan'])}｜{what}｜现 {c['open_pct']:.1f}%（{c['minted']}/{c['open_at_shares']}）｜速度 {c.get('rate_pct_per_min')}%/分钟{eta}")
    if o.get('read_failed'): print(f"高频进度：{o['read_failed']} 个盘读取失败")


def cmd_candidates():
    rows = []
    for f in ('chainscan.json', 'regscan.json'):
        try: rows += [dict(c, _src=f.split('.')[0]) for c in json.load(open(os.path.join(O, f))).get('new_candidates', [])]
        except Exception: pass
    if not rows: print('本轮无新链上候选'); return
    for c in rows:
        a = c.get('mint_target') or c.get('contract') or c.get('token')
        nm = name_of(c.get('name'))
        url = c.get('bscscan') or addr_url(a)
        fn = c.get('selector_name') or c.get('selector') or ''
        kind = '免费' if c.get('free_like') else '付费/待查'
        flags = ('｜风险：' + '、'.join(c['risk_flags'])) if c.get('risk_flags') else ''
        print(f"{md(nm + ' ' + short(a), url)}｜{fn}｜{kind}｜来源 {c['_src']}{flags}")


def parse_mint_many(text):
    out = []
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln.startswith('('): continue
        try: t = ast.literal_eval(ln)
        except Exception: continue
        if isinstance(t, tuple) and t: out.append(t)
    return out


def cmd_tx():
    a = sys.argv
    g = lambda k, d=None: a[a.index(k) + 1] if k in a else d
    name, c = name_of(g('--name')), g('--contract', '')
    kind, qty, u = g('--kind', '免费mint'), g('--qty', '1'), float(g('--u', '0'))
    rows = parse_mint_many(sys.stdin.read())
    if not rows: print(f'{name}：没有读到 mint_many 输出'); return
    head = md(f'{name} {short(c)}', addr_url(c)) if c else name
    for t in rows:
        label, state = t[0], t[1]
        txh = t[2] if len(t) > 2 and isinstance(t[2], str) and re.fullmatch(r'0x[0-9a-fA-F]{64}', t[2]) else None
        st = STATE_CN.get(state, state)
        link = md(f'tx {short(txh)}', tx_url(txh)) if txh else '无 tx（未执行）'
        print(f"{head}｜{label}｜{kind}｜{qty if txh else 0}｜{u:.2f} U｜{st}｜{link}")


def cmd_link():
    a = sys.argv[2:]
    if len(a) < 2: print('用法：report.py link <名字> <合约> [代币]'); return
    nm, c = name_of(a[0]), a[1].lower(); t = a[2].lower() if len(a) > 2 else None
    parts = [md(f'{nm} {short(c)}', addr_url(c)), md('Sourcify', f'https://repo.sourcify.dev/56/{c}')]
    if t: parts += [md('代币', tok_url(t)), md('DexScreener', f'https://dexscreener.com/bsc/{t}')]
    print('｜'.join(parts))


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'fairmint'
    {'fairmint': cmd_fairmint, 'fast': cmd_fast, 'candidates': cmd_candidates, 'tx': cmd_tx, 'link': cmd_link}.get(cmd, cmd_fairmint)()
