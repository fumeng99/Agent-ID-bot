#!/usr/bin/env bash
# 学习源仓库更新检测：git pull 每个仓库，对比 repo_watch.json 里上次学到的 commit，输出有新提交的仓库与提交列表。不改 learned（学完由 Agent 写回）。
W=${AIDBOT_HOME:-$HOME/.agent-id-bot}; J=$W/launch-monitor/repo_watch.json
python3 - "$W" "$J" <<'PY'
import json,subprocess,sys,os
W,J=sys.argv[1],sys.argv[2];st=json.load(open(J));out={}
for d,v in st.items():
    p=os.path.join(W,d)
    if not os.path.isdir(os.path.join(p,'.git')):
        subprocess.run(['git','clone','-q',v['url'],p],timeout=120)
    else:
        subprocess.run(['git','-C',p,'pull','-q','--ff-only'],timeout=120)
    h=subprocess.run(['git','-C',p,'rev-parse','HEAD'],capture_output=True,text=True).stdout.strip()
    if h and h!=v.get('learned'):
        rng=f"{v['learned']}..{h}" if v.get('learned') else h+' -20'
        log=subprocess.run(['git','-C',p,'log','--oneline']+rng.split(),capture_output=True,text=True).stdout.strip().splitlines()
        out[d]={'from':v.get('learned'),'to':h,'commits':log[:40]}
print(json.dumps(out,ensure_ascii=False,indent=1) if out else '{}')
PY
