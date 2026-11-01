#!/usr/bin/env bash
# 包装器：固定 BINANCE_INSTANCE_ID（baw 默认用网卡 MAC 加密会话，沙盒每次换 IP/MAC 会导致会话解不开 = 假掉线）
if [ -z "${BINANCE_INSTANCE_ID:-}" ] && [ -f "$HOME/instance_id" ]; then export BINANCE_INSTANCE_ID="$(cat "$HOME/instance_id")"; fi
if [ -z "${BINANCE_INSTANCE_ID:-}" ]; then echo '{"success":false,"error":"NO_INSTANCE_ID: missing $HOME/instance_id"}'; exit 3; fi
# 规则 B 硬闸（2026-11-01）：任何 contract-call preview 的目标合约源码必须已验证（Sourcify/BscScan，代理查实现），否则拒绝。不可绕过。
if [ "${1:-}" = "contract-call" ] && [ "${2:-}" = "preview" ]; then
  _to=""; _prev=""; for _a in "$@"; do [ "$_prev" = "--to" ] && _to="$_a"; _prev="$_a"; done
  if [ -n "$_to" ]; then
    _v=$(cd "${AIDBOT_HOME:-$HOME/.agent-id-bot}/launch-monitor" && timeout 90 python3 verify_source.py "$_to" 2>/dev/null | head -1)
    if ! printf '%s' "$_v" | grep -q '"verified": *true'; then
      echo "{\"success\":false,\"error\":\"SOURCE_UNVERIFIED: $_to 源码未验证或查询失败，规则 B 禁止交互\"}"; exit 3
    fi
  fi
fi
exec node "${AIDBOT_HOME:-$HOME/.agent-id-bot}"/baw/node_modules/@binance/agentic-wallet/dist/index.js "$@"
