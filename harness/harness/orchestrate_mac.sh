#!/bin/sh
# Mac mini(M4 32GB)에서 mlx-lm 서버를 띄우고 SSH 터널(로컬 18001)로 같은 평가를 돌린다. 사용: orchestrate_mac.sh repo label
REPO=$1; LABEL=$2; cd "$(dirname "$0")/.."
ssh macmini "cd ~/lab/mlx && nohup .venv/bin/mlx_lm.server --model $REPO --host 127.0.0.1 --port 8001 --max-tokens 4096 > server_$LABEL.log 2>&1 & echo \$! > server.pid"
ssh -f -N -L 18001:127.0.0.1:8001 macmini
for i in $(seq 1 180); do curl -s -m 3 http://127.0.0.1:18001/v1/models >/dev/null && break; sleep 5; done
curl -s -m 3 http://127.0.0.1:18001/v1/models >/dev/null || { echo "서버 기동 실패 $LABEL"; }
ssh macmini 'ps -o rss= -p $(cat ~/lab/mlx/server.pid)' | awk '{print "서버 RSS(기동 직후) MiB:", $1/1024}'
# 첫 요청이 모델 다운로드/로드를 일으키므로 평가 전에 워밍업한다(타임아웃·소요시간 오염 방지)
curl -s -m 1500 http://127.0.0.1:18001/v1/chat/completions -H 'Content-Type: application/json' -d "{\"model\":\"$REPO\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}],\"max_tokens\":4}" >/dev/null
./.venv/bin/python harness/run_eval.py --base-url http://127.0.0.1:18001 --model $REPO --label $LABEL --repeat 2 --t4-samples 3
ssh macmini 'ps -o rss= -p $(cat ~/lab/mlx/server.pid)' | awk '{print "서버 RSS(평가 후) MiB:", $1/1024}'
ssh macmini 'kill $(cat ~/lab/mlx/server.pid)'
pkill -f "18001:127.0.0.1:8001"
