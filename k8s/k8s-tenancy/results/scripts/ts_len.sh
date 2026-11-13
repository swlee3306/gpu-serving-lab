#!/bin/bash
# 같은 GPU에 vLLM 두 개(util 0.48)를 Docker로 올리고, time-slice 길이(0 기본,1 짧음,2 중간,3 김)별로 피해자 B의 지연을 잰다.
set -u
IMG=vllm/vllm-openai:latest
start() { # name
  docker rm -f $1 >/dev/null 2>&1
  docker run -d --rm --gpus all --ipc=host --name $1 -v /data/models/hf:/root/.cache/huggingface $IMG --model Qwen/Qwen2.5-3B-Instruct --max-model-len 8192 --gpu-memory-utilization 0.48 >/dev/null
  for i in $(seq 1 60); do docker exec $1 python3 -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=3)" 2>/dev/null && echo "$1 ready" && return; sleep 5; done; echo "$1 NOT READY"; }
run() { docker exec -i $1 python3 - "$2" "$3" "$4" < /tmp/load2.py 2>&1 | tail -1; }
start vllm-a; start vllm-b
nvidia-smi --query-gpu=memory.used --format=csv,noheader
for ts in 0 1 2 3; do
  sudo nvidia-smi compute-policy --set-timeslice=$ts 2>&1 | tail -1
  echo "== timeslice=$ts"; nvidia-smi compute-policy -l 2>&1 | grep -i -E "timeslice|default|short|medium|long" | head -2
  run vllm-b B-alone-ts$ts 48 4
  ( run vllm-a A-heavy-ts$ts 800 64 > /tmp/ts_a_$ts.out ) &
  sleep 8
  run vllm-b B-with-A-ts$ts 48 4
  wait; cat /tmp/ts_a_$ts.out
done
sudo nvidia-smi compute-policy --set-timeslice=0 2>&1 | tail -1
docker rm -f vllm-a vllm-b >/dev/null 2>&1
