#!/bin/bash
# gpu-server: 9B AWQ vLLM 기동 후 동일 부하를 workers=1, workers=8로 돌리며 nvidia-smi 보드 전력을 1초 간격 기록(시스템 전체 전력은 측정 불가)
cd "$(dirname "$0")/.."; HOST=gpu-server; M=cyankiwi/Qwen3.5-9B-AWQ-4bit
IMG=vllm/vllm-openai@sha256:befccf0e7e4089aa23926bb6f887a85c0d46c2a99e6de5c5ffdf5cb7957c5b34
ssh $HOST "docker ps -q -f name=vllm-eval | grep -q . || docker run -d --rm --gpus all --ipc=host --name vllm-eval -v /data/models/hf:/root/.cache/huggingface -p 127.0.0.1:8000:8000 $IMG --model $M --max-model-len 8192 --gpu-memory-utilization 0.85 --max-num-seqs 128 >/dev/null"
for i in $(seq 1 90); do ssh $HOST "curl -s -m 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/health" | grep -q 200 && break; sleep 5; done
ssh -f -N -L 18000:127.0.0.1:8000 $HOST; sleep 2
curl -s -m 300 http://127.0.0.1:18000/v1/chat/completions -H 'Content-Type: application/json' -d "{\"model\":\"$M\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}],\"max_tokens\":4}" >/dev/null
ssh $HOST 'for i in $(seq 10); do nvidia-smi --query-gpu=power.draw --format=csv,noheader,nounits; sleep 1; done' > results/power/gpu_idle_loaded.txt
for W in 1 8; do
  ssh $HOST 'rm -f /tmp/pw.txt; nohup sh -c "while true; do nvidia-smi --query-gpu=power.draw --format=csv,noheader,nounits >> /tmp/pw.txt; sleep 1; done" >/dev/null 2>&1 </dev/null & echo $! > /tmp/pw.pid'
  S=$(date +%s)
  ./.venv/bin/python harness/run_eval.py --base-url http://127.0.0.1:18000 --model $M --label power-gpu-9b-w$W --repeat 1 --t4-samples 1 --workers $W
  E=$(date +%s); echo "elapsed $((E-S))" > results/power/gpu_w${W}_elapsed.txt
  ssh $HOST 'kill $(cat /tmp/pw.pid)'; scp -q $HOST:/tmp/pw.txt results/power/gpu_w$W.txt
done
pkill -f "18000:127.0.0.1:8000"; ssh $HOST "docker rm -f vllm-eval >/dev/null 2>&1"
