#!/bin/sh
# Mac mini: 9B MLX 서버 기동 후 동일 부하(T1/T2 24케이스×1, T4 1샘플)를 돌리며 powermetrics로 패키지 전력(CPU+GPU+ANE)을 1초 간격 기록
cd "$(dirname "$0")/.."; REPO=mlx-community/Qwen3.5-9B-MLX-4bit
ssh macmini "cd ~/lab/mlx && nohup .venv/bin/mlx_lm.server --model $REPO --host 127.0.0.1 --port 8001 --max-tokens 4096 > server_power.log 2>&1 & echo \$! > server.pid"
ssh -f -N -L 18001:127.0.0.1:8001 macmini
for i in $(seq 1 60); do curl -s -m 3 http://127.0.0.1:18001/v1/models >/dev/null && break; sleep 3; done
curl -s -m 600 http://127.0.0.1:18001/v1/chat/completions -H 'Content-Type: application/json' -d "{\"model\":\"$REPO\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}],\"max_tokens\":4}" >/dev/null
echo "idle-before"; ssh macmini 'sudo -n powermetrics --samplers cpu_power -i 1000 -n 10 | grep "Combined Power"' > results/power/mac_idle.txt
ssh macmini 'sudo -n powermetrics --samplers cpu_power -i 1000 -n 3000 | grep "Combined Power" > ~/lab/mlx/pm_load.txt' &
PM=$!; sleep 2; S=$(date +%s)
./.venv/bin/python harness/run_eval.py --base-url http://127.0.0.1:18001 --model $REPO --label power-mac-9b --repeat 1 --t4-samples 1 --workers 1
E=$(date +%s); echo "mac_elapsed $((E-S))" > results/power/mac_elapsed.txt
ssh macmini 'sudo -n pkill powermetrics; kill $(cat ~/lab/mlx/server.pid)'; wait $PM
scp -q macmini:lab/mlx/pm_load.txt results/power/mac_load.txt
pkill -f "18001:127.0.0.1:8001"
