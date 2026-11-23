#!/usr/bin/env python3
"""从 config/config.json 生成 $AIDBOT_HOME/baw/accounts/accounts.json。

脚本按 label / bsc / home 三个键读取账号清单；只收录已填好真实地址的账号
（未登录账号地址为 0x000...0，收录进去会让余额/铸造脚本读到错误的号）。

用法：python3 monitor/gen_accounts_json.py"""
import json
import os
import pathlib
import sys

AIDBOT_HOME = os.environ.get("AIDBOT_HOME") or os.path.expanduser("~/.agent-id-bot")
CFG = pathlib.Path(__file__).resolve().parent.parent / "config/config.json"
ZERO = "0x0000000000000000000000000000000000000000"


def main() -> None:
    if not CFG.exists():
        sys.exit(f"缺 {CFG}：先 cp config/config.example.json config/config.json")
    cfg = json.load(open(CFG))
    rows = []
    for a in cfg.get("accounts", []):
        addr = (a.get("address") or "").strip()
        if not addr or addr.lower() == ZERO:
            continue
        rows.append({
            "dir": a.get("dir"),
            "label": a.get("label") or a.get("dir"),
            "agentId": a.get("agentId", ""),
            "bsc": addr,
            "home": os.path.join(AIDBOT_HOME, "baw/accounts", a.get("dir", "")),
        })
    out = pathlib.Path(AIDBOT_HOME) / "baw/accounts/accounts.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    json.dump(rows, open(out, "w"), ensure_ascii=False, indent=2)
    print(f"写入 {out}：{[(r['label'], r['bsc'][:10]) for r in rows]}")


if __name__ == "__main__":
    main()