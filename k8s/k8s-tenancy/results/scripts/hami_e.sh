#!/bin/bash
# D: vLLM이 HAMi-core 아래에서 기동·서빙되는가. E: 이웃(gemm/copy)에 HAMi SM 한도를 걸면 피해자 vLLM 지연이 어떻게 되는가.
set -u
LIB=/data/lab/hami-src/src/build/libvgpu.so
IMG=vllm/vllm-openai:latest
H="-v $LIB:/libvgpu.so:ro"
wait_ready() { for i in $(seq 1 60); do docker exec $1 python3 -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=3)" 2>/dev/null && return 0; sleep 5; done; return 1; }
runload() { docker exec -i $1 python3 - "$2" 48 4 < /tmp/load2.py 2>&1 | tail -1; }

echo "== [메모리 한도 4g, 할당이 멈추는 지점]"
docker run --rm --gpus all --entrypoint bash $H -v /tmp/hami_smoke.py:/s.py -e LD_PRELOAD=/libvgpu.so -e CUDA_DEVICE_MEMORY_LIMIT=4g $IMG -c "mkdir -p /tmp/vgpulock; python3 /s.py memcheck" 2>&1 | grep -E "alloc:" 

echo "== [D] vLLM + HAMi-core (메모리 10g 한도, SM 한도 없음)"
docker rm -f vllm-d >/dev/null 2>&1
docker run -d --rm --gpus all --ipc=host --name vllm-d $H -e LD_PRELOAD=/libvgpu.so -e CUDA_DEVICE_MEMORY_LIMIT=10g -v /data/models/hf:/root/.cache/huggingface --entrypoint bash $IMG -c "mkdir -p /tmp/vgpulock; exec vllm serve Qwen/Qwen2.5-3B-Instruct --max-model-len 8192 --gpu-memory-utilization 0.85" >/dev/null
if wait_ready vllm-d; then echo "vllm-d ready"; docker logs vllm-d 2>&1 | grep -E "Available KV cache memory|GPU KV cache size|Model loading took" | head -3 | cut -c1-170; echo "GPU: $(nvidia-smi --query-gpu=memory.used --format=csv,noheader)"; runload vllm-d D-hami-alone; else echo "vllm-d NOT READY"; docker logs vllm-d 2>&1 | grep -E "ERROR|Error|error" | head -5 | cut -c1-200; fi
docker rm -f vllm-d >/dev/null 2>&1; sleep 3

echo "== [E] 피해자 vLLM B(HAMi 없음, util 0.48) + 이웃(HAMi 한도)"
docker rm -f vllm-b nbr >/dev/null 2>&1
docker run -d --rm --gpus all --ipc=host --name vllm-b -v /data/models/hf:/root/.cache/huggingface $IMG --model Qwen/Qwen2.5-3B-Instruct --max-model-len 8192 --gpu-memory-utilization 0.48 >/dev/null
wait_ready vllm-b && echo "vllm-b ready" || echo "vllm-b NOT READY"
runload vllm-b base1; runload vllm-b base2
nb() { # label mode sm mem
  docker run -d --gpus all --name nbr --entrypoint bash $H -v /tmp/nbr.py:/nbr.py -e LD_PRELOAD=/libvgpu.so -e CUDA_DEVICE_MEMORY_LIMIT=$4 $( [ "$3" != none ] && echo "-e CUDA_DEVICE_SM_LIMIT=$3" ) $IMG -c "mkdir -p /tmp/vgpulock; python3 /nbr.py $2 1.0 70" >/dev/null
  sleep 12
  r=$(runload vllm-b "$1"); echo "$r"
  for i in $(seq 1 30); do [ "$(docker inspect -f '{{.State.Running}}' nbr 2>/dev/null)" = "true" ] || break; sleep 3; done
  docker logs nbr 2>&1 | grep -E "duty=" | tail -1
  docker rm -f nbr >/dev/null 2>&1; sleep 3
}
nb gemm100-nolimit gemm none 2g
nb gemm100-sm50 gemm 50 2g
nb gemm100-sm25 gemm 25 2g
nb gemm100-sm10 gemm 10 2g
nb copy100-nolimit copy none 4g
nb copy100-sm25 copy 25 4g
runload vllm-b base-after
docker rm -f vllm-b nbr >/dev/null 2>&1
