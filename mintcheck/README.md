# BSC Mint 预检 Skill（2026-11-03 建）
用法：python3 mintcheck.py <网址|CA> [--json]；经验库：python3 mintlib.py stats|show|match|record|template|timing|seed；回归：bash selftest.sh
只读：eth_call / getCode / getStorageAt / estimateGas / stateOverride 模拟，从不签名、授权、付款。
详见 the agent 自定义 Skill「bsc-mint-precheck」。
