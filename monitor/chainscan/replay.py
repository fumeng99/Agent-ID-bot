#!/usr/bin/env python3
"""照抄别人成功的铸造调用：python3 replay.py <txhash> <我的地址>
输出 to / value / calldata（把原发送者地址换成我的地址），以及能否照抄的判断。只生成数据，不签名。"""
import sys, json
sys.path.insert(0, __import__('os').path.dirname(__file__))
from scan import rpc, BAD_SEL
h, me = sys.argv[1], sys.argv[2].lower()
tx = rpc('eth_getTransactionByHash', [h]); rc = rpc('eth_getTransactionReceipt', [h])
inp = tx['input'].lower(); frm = tx['from'].lower()[2:]; sel = inp[:10]
out = {'to': tx['to'], 'value_wei': int(tx['value'], 16), 'selector': sel, 'status': rc['status']}
flags = []
if rc['status'] != '0x1': flags.append('原交易失败')
if sel in BAD_SEL: flags.append('转账/授权类选择器，禁止照抄')
body = inp[10:]
words = [body[i:i+64] for i in range(0, len(body), 64)]
if frm in body: body = body.replace(frm, me[2:]); flags.append('已把原发送者地址换成我的地址')
addrs = [w[24:] for w in words if w[:24] == '0'*24 and int(w[24:] or '0', 16) > 2**100 and w[24:] != frm]
if addrs: out['other_addresses'] = ['0x'+a for a in addrs]; flags.append('含其他地址（可能是合约/邀请人/收货人，需人工判断）')
if len(body) > 64*8: flags.append('参数很长，可能含签名/nonce，需官网取自己的参数')
out['calldata'] = sel + body; out['flags'] = flags
print(json.dumps(out, ensure_ascii=False, indent=1))
