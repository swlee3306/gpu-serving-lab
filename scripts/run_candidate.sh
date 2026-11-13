#!/bin/bash
# usage: run_candidate.sh NAME MODEL "extra vllm args" "rates"   (runs on gpu-server; writes /data/lab/bench/cand-NAME.*)
NAME=$1; MODEL=$2; EXTRA=$3; RATES=${4:-2,4,6,8,10,14}
B=/data/lab/bench; IMG=vllm/vllm-openai:latest
if [ -n "$(docker ps -aq)" ]; then echo "ABORT: containers exist"; exit 1; fi
echo "##### $NAME  model=$MODEL  args=[$EXTRA]"
docker run -d --rm --gpus all --ipc=host --name vllm -v /data/models/hf:/root/.cache/huggingface -p 127.0.0.1:8000:8000 $IMG --model $MODEL $EXTRA >/dev/null
t0=$(date +%s); until curl -s -m 2 http://127.0.0.1:8000/v1/models >/dev/null 2>&1; do sleep 5; [ $(( $(date +%s) - t0 )) -gt 900 ] && { echo "NOT READY"; docker logs vllm 2>&1 | grep -E "ERROR|Error" | head -5; docker stop vllm >/dev/null; exit 1; }; done
echo "ready after $(( $(date +%s) - t0 ))s"
docker logs vllm 2>&1 | grep -i -m3 -E "max_num_batched_tokens=|max_num_seqs=" | sed -E 's/^.*\] //' | cut -c1-200
docker logs vllm 2>&1 | grep -i -m3 -E "marlin|awq|gptq" | grep -v "Initializing a V1" | sed -E 's/^.*\] //' | cut -c1-200
docker logs vllm 2>&1 | grep -E "Model loading took|Available KV cache memory|GPU KV cache size" | sed -E 's/^.*\] //'
python3 $B/eval_mc.py --model $MODEL --out $B/cand-$NAME.q1.json
python3 $B/eval_mc.py --model $MODEL --out $B/cand-$NAME.q2.json
python3 - <<P
import json
a=json.load(open("$B/cand-$NAME.q1.json"))["preds"]; b=json.load(open("$B/cand-$NAME.q2.json"))["preds"]
print("대조군: 같은 서버 두 번 실행 시 답이 달라진 문제 수:", sum(a[k]!=b[k] for k in a), "/", len(a))
P
python3 $B/bench_real.py --model $MODEL --rates $RATES --duration 25 --stop-after-fail 2 --out $B/cand-$NAME.load.json 2>&1 | tail -9
echo "peak: $(docker logs vllm 2>&1 | grep -oE 'GPU KV cache usage: [0-9.]+%' | sort -t: -k2 -n | tail -1)"
docker stop vllm >/dev/null 2>&1; sleep 3; echo "cleanup: containers=$(docker ps -aq | wc -l) GPU=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader)"
