#!/usr/bin/env python3
"""热盘提前预警简报（2026-11-01 20:47 用户采纳）。只读：eth_call / eth_getLogs，不签名、不交易。
用法：python3 fairmint/hotbrief.py <fair mint 合约> [...]      输出每盘一段纯文本，直接贴进消息。
内容：项目、合约/代币链接、单价 U、开池进度、速度、预计到 70% 与开池的分钟数、Agent ID 铸造钱包数、铸造集中度、
审计结论（audit_ok.json note；不通过写卡在哪项；没有结论写「未审计」）。池子/持仓集中度由调用方用 query-token-info 补（读到才写）。
结尾固定「需要买请回复：确认买入 XX 金额 + 账号」。
"""
import json, os, sys
D = os.path.dirname(os.path.abspath(__file__)); L = os.path.dirname(D); sys.path.insert(0, L); sys.path.insert(0, D)
import report as R


def load(p, d):
    try: return json.load(open(p))
    except Exception: return d


def brief(c):
    c = c.lower(); found = load(os.path.join(D, 'found.json'), {}); v = found.get(c) or {}
    aok = load(os.path.join(D, 'audit_ok.json'), {}).get(c); st = (load(os.path.join(D, 'fastprogress_state.json'), {}).get(c) or {})
    live = load(os.path.join(L, 'discover_out', 'fp_live.json'), {}); row = next((h for h in live.get('hot', []) if h.get('contract') == c), None)
    if not row: row = next((h for h in load(os.path.join(D, 'fastprogress_last.json'), {}).get('hot', []) if h.get('contract') == c), None) or {}
    oa = v.get('open_at_shares') or row.get('open_at_shares'); hist = st.get('hist') or []
    minted = row.get('minted', hist[-1][1] if hist else None)
    pct = row.get('open_pct') if row.get('open_pct') is not None else (round(minted * 100 / oa, 1) if minted is not None and oa else None)
    bu = R.bnb_u(); nm = R.name_of(v.get('name') or row.get('name'))
    L_ = [f"🔥热盘预警：{nm}｜{R.addr_url(c)}"]
    if v.get('token'): L_.append(f"代币 {R.short(v['token'])}｜{R.tok_url(v['token'])}")
    L_.append(f"单价 {R.price_u(v, bu)}｜进度 {pct if pct is not None else '?'}%（{minted if minted is not None else '?'}/{oa or '?'}）｜速度 {row.get('rate_pct_per_min', '?')}%/分钟")
    L_.append(f"预计到 70%：{row.get('eta_70_min') if row.get('eta_70_min') is not None else '已过或无法估算'} 分钟｜预计开池：{row.get('eta_open_min') if row.get('eta_open_min') is not None else '无法估算'} 分钟")
    try:
        import minters as MN; m = MN.ai_minters(c, budget=25)
    except Exception as e: m = None
    if m: L_.append(f"Agent ID 铸造钱包 ≈{m['ai_minters']}（铸造地址 {m['minters']}，.ai 比例 {m['ai_ratio']}）｜单地址最高占铸造 {round(m['top_share']*100,1) if m.get('top_share') is not None else '?'}%")
    else: L_.append("Agent ID 铸造钱包：读取失败（autofill 当作不达标）")
    if aok: L_.append(f"审计：{'通过' if aok.get('ok') else '不通过'}（{'自动' if aok.get('auto') else '人工'}，{aok.get('time', '')}）— {aok.get('note', '')}")
    else: L_.append("审计：未审计（≥60% 时做完整审计）")
    tags = []
    if v.get('template') not in (None, 'jager'): tags.append(f"{v.get('template')} 模板，≥70% 只提醒不自动打满")
    if v.get('has_refund'): tags.append('有退款函数')
    if v.get('can_open_early') == 'treasury': tags.append('项目方可提前开池')
    if tags: L_.append('｜'.join(tags))
    L_.append("需要买请回复：确认买入 XX 金额 + 账号")
    return '\n'.join(L_)


if __name__ == '__main__':
    print('\n\n'.join(brief(a) for a in sys.argv[1:]))
