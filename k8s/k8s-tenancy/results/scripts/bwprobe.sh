#!/bin/bash
# vLLM 하나(단독)에서 동시 요청 수별로 GPU 메모리 컨트롤러 사용률(NVML utilization.memory)과 전력·클록을 샘플링한다.
set -u
docker rm -f vllm-x >/dev/null 2>&1
docker run -d --rm --gpus all --ipc=host --name vllm-x -v /data/models/hf:/root/.cache/huggingface vllm/vllm-openai:latest --model Qwen/Qwen2.5-3B-Instruct --max-model-len 8192 --gpu-memory-utilization 0.85 >/dev/null
for i in $(seq 1 60); do docker exec vllm-x python3 -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=3)" 2>/dev/null && break; sleep 5; done
echo "ready"; nvidia-smi --query-gpu=utilization.gpu,utilization.memory,power.draw,clocks.sm,clocks.mem --format=csv,noheader,nounits | sed 's/^/idle: util.gpu,util.mem,power,smclk,memclk = /'
for cfg in "4 48" "1 24" "16 160" "64 400"; do
  set -- $cfg
  nvidia-smi --query-gpu=utilization.gpu,utilization.memory,power.draw,clocks.sm --format=csv,noheader,nounits -lms 500 > /tmp/bw_c$1.csv &
  SP=$!
  sleep 1
  r=$(docker exec -i vllm-x python3 - "c$1" $2 $1 < /tmp/load2.py 2>&1 | tail -1)
  kill $SP 2>/dev/null; wait $SP 2>/dev/null
  echo "$r"
  awk -F', ' 'NR>2 {g+=$1; m+=$2; p+=$3; c+=$4; n++} END {printf "   샘플 %d개 평균: util.gpu=%.0f%% util.mem=%.0f%% power=%.0fW smclk=%.0fMHz\n", n, g/n, m/n, p/n, c/n}' /tmp/bw_c$1.csv
done
docker rm -f vllm-x >/dev/null 2>&1
