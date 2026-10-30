#!/usr/bin/env bash
# 包装器：固定 BINANCE_INSTANCE_ID（baw 默认用网卡 MAC 加密会话，沙盒每次换 IP/MAC 会导致会话解不开 = 假掉线）
if [ -z "${BINANCE_INSTANCE_ID:-}" ] && [ -f "$HOME/instance_id" ]; then export BINANCE_INSTANCE_ID="$(cat "$HOME/instance_id")"; fi
if [ -z "${BINANCE_INSTANCE_ID:-}" ]; then echo '{"success":false,"error":"NO_INSTANCE_ID: missing $HOME/instance_id"}'; exit 3; fi
exec node /workspace/work/baw/node_modules/@binance/agentic-wallet/dist/index.js "$@"
