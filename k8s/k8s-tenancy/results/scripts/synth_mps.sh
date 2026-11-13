#!/bin/bash
# 피해자 vLLM B(util 0.48)와 합성 이웃(별도 컨테이너·CUDA 컨텍스트). 순서를 정방향/역방향으로 두 번씩 돌린다.
set -u
IMG=vllm/vllm-openai:latest
docker rm -f vllm-b nbr >/dev/null 2>&1
docker run -d --rm --gpus all --ipc=host -v /tmp/nvidia-mps:/tmp/nvidia-mps --name vllm-b -v /data/models/hf:/root/.cache/huggingface $IMG --model Qwen/Qwen2.5-3B-Instruct --max-model-len 8192 --gpu-memory-utilization 0.48 >/dev/null
for i in $(seq 1 60); do docker exec vllm-b python3 -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=3)" 2>/dev/null && break; sleep 5; done
echo "B ready; GPU $(nvidia-smi --query-gpu=memory.used --format=csv,noheader)"
runB() { docker exec -i vllm-b python3 - "$1" 48 4 < /tmp/load2.py 2>&1 | tail -1; }
runB base1; runB base2
one() { # label mode duty
  docker run -d --gpus all --ipc=host -v /tmp/nvidia-mps:/tmp/nvidia-mps --name nbr --entrypoint python3 -v /tmp/nbr.py:/nbr.py $IMG /nbr.py $2 $3 120 >/dev/null
  sleep 10
  r=$(runB "$1")
  echo "$r"
  docker kill nbr >/dev/null 2>&1; sleep 1
  # 이웃의 마지막 통계는 kill 로 출력되지 않으므로 별도 짧은 실행으로 단독 성능을 따로 잰다(아래 neighbor-alone).
  docker rm -f nbr >/dev/null 2>&1; sleep 3
}
for pass in fwd rev; do
  if [ $pass = fwd ]; then L="idle:idle:1 copy100:copy:1.0 gemm100:gemm:1.0 copy25:copy:0.25 gemm25:gemm:0.25"; else L="gemm25:gemm:0.25 copy25:copy:0.25 gemm100:gemm:1.0 copy100:copy:1.0 idle:idle:1"; fi
  echo "== pass $pass"
  for e in $L; do IFS=: read lab mode duty <<< "$e"; one "$pass-$lab" $mode $duty; done
done
echo "== neighbor-alone (이웃만 20초 단독 실행했을 때의 처리량, B 없이 아님: B는 유휴)"
for e in copy:1.0 gemm:1.0 copy:0.25 gemm:0.25; do IFS=: read mode duty <<< "$e"; docker run --rm --gpus all --ipc=host -v /tmp/nvidia-mps:/tmp/nvidia-mps --entrypoint python3 -v /tmp/nbr.py:/nbr.py $IMG /nbr.py $mode $duty 20 2>&1 | tail -1; done
docker rm -f vllm-b nbr >/dev/null 2>&1
