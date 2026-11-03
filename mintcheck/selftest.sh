#!/usr/bin/env bash
# 回归测试：5 个已知样本，检查判断（verdict）是否符合预期。用法：bash selftest.sh
cd "$(dirname "$0")"; R=$(mktemp)
check() { # 输入 期望verdict 期望合约
  local out; out=$(timeout 110 python3 mintcheck.py "$1" --json --no-lib 2>/dev/null)
  local got; got=$(echo "$out" | python3 -c "import json,sys;d=json.load(sys.stdin);r=[x for x in d['reports'] if x['address']=='$3'];print(r[0]['verdict'] if r else 'missing', d['timing_s']['total'])" 2>/dev/null)
  if [[ "$got" == "$2 "* ]]; then echo "PASS $1 -> $got" | tee -a $R; else echo "FAIL $1 期望 $2 实际 $got" | tee -a $R; fi
}
check 0x08ADcd7912C12cF4c755A96512E06817F2BB2Bb5 paid_confirm 0x08adcd7912c12cf4c755a96512e06817f2bb2bb5 &   # FLYSale：USDT 付款，假设授权后模拟通过
check 0x95115e644c87df8ba170393de232402c60005fe4 needs_ai 0x95115e644c87df8ba170393de232402c60005fe4 &       # BBF：Merkle 证明参数
check 0x5f9c9b23469106fd1ba84fafacf279ba52495176 not_open 0x5f9c9b23469106fd1ba84fafacf279ba52495176 &       # ShareGenesis：21:00 前 NotStarted
wait
check https://agentbnbai.com/ free_ready 0xa2643b889eeb232708a644f15f70f951773ddef9 &                          # 网址输入：免费 mint()
check 0x6288dfe86f22b53eeb6886ae2d78941de7483743 paid_confirm 0x6288dfe86f22b53eeb6886ae2d78941de7483743 &   # AGC：押金 0.002 BNB/份
check 0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c not_mint 0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c &   # WBNB：不是 Mint
wait
echo "结果：通过 $(grep -c ^PASS $R)，失败 $(grep -c ^FAIL $R)"; rm -f $R
