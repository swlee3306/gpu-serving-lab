#!/bin/bash
# vLLM sleep mode 로 두 테넌트가 GPU를 시간 분할로 독점하는 시나리오. A(8001), B(8002). 한 번에 한 쪽만 깨어 있다.
set -u
IMG=vllm/vllm-openai:latest
start() { # name port
  docker rm -f $1 >/dev/null 2>&1
  docker run -d --rm --gpus all --ipc=host --name $1 -e VLLM_SERVER_DEV_MODE=1 -p 127.0.0.1:$2:8000 -v /data/models/hf:/root/.cache/huggingface $IMG --model Qwen/Qwen2.5-3B-Instruct --max-model-len 8192 --gpu-memory-utilization 0.85 --enable-sleep-mode >/dev/null
  for i in $(seq 1 60); do curl -s -m 3 -o /dev/null -w "%{http_code}" http://127.0.0.1:$2/health | grep -q 200 && echo "$1 ready after ~$((i*5))s" && return; sleep 5; done; echo "$1 NOT READY"; }
mem() { echo "  GPU 메모리: $(nvidia-smi --query-gpu=memory.used --format=csv,noheader)"; }
load() { PORT=$1 python3 /tmp/load2p.py "$2" 48 4; }
start vllm-b 8002; echo "[B 기동 직후]"; mem
python3 /tmp/swap.py 8002 8002 >/dev/null 2>&1 || true
curl -s -X POST "http://127.0.0.1:8002/sleep?level=1" -o /dev/null -w "B sleep http=%{http_code}\n"; sleep 3; echo "[B 재운 뒤]"; mem; curl -s http://127.0.0.1:8002/is_sleeping; echo
start vllm-a 8001; echo "[A 기동 직후 (B는 잠든 상태)]"; mem
echo "== A 단독(독점) 부하"; load 8001 A-awake
for i in 1 2 3; do
  echo "== 전환 $i: A 재우고 B 깨우기"; python3 /tmp/swap.py 8001 8002; mem
  load 8002 B-awake-$i
  echo "== 전환 $i': B 재우고 A 깨우기"; python3 /tmp/swap.py 8002 8001; mem
  load 8001 A-awake-$i
done
docker rm -f vllm-a vllm-b >/dev/null 2>&1
