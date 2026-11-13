#!/bin/bash
# gpu-server에서 모델을 하나씩 vLLM(이미지 digest 고정)으로 띄우고, SSH 터널로 이 맥에서 평가를 돌린 뒤 내린다.
set -u
cd "$(dirname "$0")/.."
IMG=vllm/vllm-openai@sha256:befccf0e7e4089aa23926bb6f887a85c0d46c2a99e6de5c5ffdf5cb7957c5b34
HOST=gpu-server; LP=18000
PY=./.venv/bin/python
run_one() { # label model extra
  label=$1; model=$2; extra=$3
  echo "=== $label ($model) $(date +%T)"
  ssh $HOST "docker rm -f vllm-eval >/dev/null 2>&1; docker run -d --rm --gpus all --ipc=host --name vllm-eval -v /data/models/hf:/root/.cache/huggingface -p 127.0.0.1:8000:8000 $IMG --model $model --max-model-len 8192 --gpu-memory-utilization 0.85 $extra >/dev/null"
  ok=0
  for i in $(seq 1 90); do
    if ssh $HOST "curl -s -m 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/health" 2>/dev/null | grep -q 200; then ok=1; break; fi
    if ! ssh $HOST "docker ps -q -f name=vllm-eval" | grep -q .; then break; fi
    sleep 5
  done
  if [ $ok != 1 ]; then echo "$label: 서버 기동 실패"; ssh $HOST "docker logs vllm-eval 2>&1 | grep -E 'ValueError|Error' | tail -3 | cut -c1-260"; ssh $HOST "docker rm -f vllm-eval >/dev/null 2>&1"; return; fi
  ssh -f -N -L $LP:127.0.0.1:8000 $HOST; sleep 2
  $PY harness/run_eval.py --base-url http://127.0.0.1:$LP --model $model --label $label --repeat 2 --t4-samples 3
  pkill -f "ssh -f -N -L $LP:127.0.0.1:8000" 2>/dev/null
  ssh $HOST "docker rm -f vllm-eval >/dev/null 2>&1"; sleep 5
}
run_one qwen25-3b Qwen/Qwen2.5-3B-Instruct ""
run_one qwen35-4b Qwen/Qwen3.5-4B "--max-num-seqs 128"
run_one qwen35-9b-awq cyankiwi/Qwen3.5-9B-AWQ-4bit "--max-num-seqs 128"
echo "ALL DONE $(date +%T)"
