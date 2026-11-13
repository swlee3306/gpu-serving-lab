#!/bin/bash
# 실제 서빙 이웃: 피해자 vLLM B(HAMi 없음, util 0.48) + 이웃 vLLM A(HAMi 메모리 7600m, SM 한도 변화, 동시 16). 순서: none 50 25 10 | 10 25 none
set -u
LIB=/data/lab/hami-src/src/build/libvgpu.so
IMG=vllm/vllm-openai:latest
H="-v $LIB:/libvgpu.so:ro"
wait_ready() { for i in $(seq 1 60); do [ "$(docker inspect -f '{{.State.Running}}' $1 2>/dev/null)" = "true" ] || return 1; docker exec $1 python3 -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=3)" 2>/dev/null && return 0; sleep 5; done; return 1; }
gt() { docker exec vllm-a python3 -c "import urllib.request;t=urllib.request.urlopen('http://127.0.0.1:8000/metrics').read().decode();print(int(sum(float(l.split()[-1]) for l in t.splitlines() if l.startswith('vllm:generation_tokens_total'))))" 2>/dev/null; }
docker rm -f vllm-a vllm-b >/dev/null 2>&1
docker run -d --rm --gpus all --ipc=host --name vllm-b -v /data/models/hf:/root/.cache/huggingface $IMG --model Qwen/Qwen2.5-3B-Instruct --max-model-len 8192 --gpu-memory-utilization 0.48 >/dev/null
wait_ready vllm-b && echo "vllm-b ready" || { echo "vllm-b NOT READY"; exit 1; }
runB() { docker exec -i vllm-b python3 - "$1" 48 4 < /tmp/load2.py 2>&1 | tail -1; }
runB base1; runB base2
one() { # label sm
  sm=$2; envs="-e LD_PRELOAD=/libvgpu.so -e CUDA_DEVICE_MEMORY_LIMIT=8000m"; [ "$sm" != none ] && envs="$envs -e CUDA_DEVICE_SM_LIMIT=$sm"
  docker run -d --gpus all --ipc=host --name vllm-a $H $envs -v /data/models/hf:/root/.cache/huggingface --entrypoint bash $IMG -c "mkdir -p /tmp/vgpulock; exec vllm serve Qwen/Qwen2.5-3B-Instruct --max-model-len 8192 --gpu-memory-utilization 0.95 --enforce-eager" >/dev/null
  if ! wait_ready vllm-a; then echo "$1: vllm-a NOT READY"; docker logs vllm-a 2>&1 | grep -E "ValueError|OutOfMemory|RuntimeError" | head -2 | cut -c1-260; docker rm -f vllm-a >/dev/null 2>&1; return; fi
  ( docker exec -i vllm-a python3 - A-neighbor 3000 16 < /tmp/load2.py > /tmp/hami_f_a.out 2>&1 ) &
  sleep 10
  g1=$(gt); r=$(runB "$1"); g2=$(gt)
  w=$(echo "$r" | python3 -c "import sys,json,re; print(json.loads(re.search(r'\{.*\}', sys.stdin.read()).group(0))['wall_s'])" 2>/dev/null)
  echo "$r"; echo "   이웃 A 생성 토큰: $(( ${g2:-0} - ${g1:-0} ))개 / ${w}s = $(python3 -c "print(round(($(( ${g2:-0} - ${g1:-0} )))/float('${w:-1}')))") tok/s"
  docker rm -f vllm-a >/dev/null 2>&1; wait 2>/dev/null; sleep 4
}
for e in none:none 25:25 10:10 25:25; do IFS=: read lab sm <<< "$e"; echo "== eager sm=$lab"; one "eager-sm$lab" $sm; done
runB base-after
docker rm -f vllm-a vllm-b >/dev/null 2>&1
