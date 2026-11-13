#!/bin/bash
# usage: run_candidate2.sh NAME MODEL "extra vllm args" "rates"  (non-thinking; big eval x2 + load sweep)
NAME=$1; MODEL=$2; EXTRA=$3; RATES=${4:-2,3,4,6,8,10,14}
B=/data/lab/bench
docker ps -a --format '{{.Names}}' | grep -q '^vllm$' && { echo "ABORT: a vllm container exists"; exit 1; }
echo "##### $NAME  model=$MODEL  args=[$EXTRA]"
start_server() {  # $1 = extra args ; returns 0 when ready, 1 on exit/timeout (logs kept in /tmp/vllm-$NAME.log)
  docker rm -f vllm >/dev/null 2>&1
  docker run -d --gpus all --ipc=host --name vllm -v /data/models/hf:/root/.cache/huggingface -p 127.0.0.1:8000:8000 vllm/vllm-openai:latest --model $MODEL $1 >/dev/null
  local t0=$(date +%s)
  until curl -s -m 2 http://127.0.0.1:8000/v1/models >/dev/null 2>&1; do
    sleep 5
    if [ "$(docker inspect -f '{{.State.Running}}' vllm 2>/dev/null)" != "true" ]; then docker logs vllm > /tmp/vllm-$NAME.log 2>&1; echo "CONTAINER EXITED"; grep -m1 -E "^(ValueError|RuntimeError|torch.*Error|.*OutOfMemory)" /tmp/vllm-$NAME.log | cut -c1-300; return 1; fi
    [ $(( $(date +%s) - t0 )) -gt 1500 ] && { docker logs vllm > /tmp/vllm-$NAME.log 2>&1; echo "NOT READY (timeout)"; return 1; }
  done
  READY_SECS=$(( $(date +%s) - t0 )); return 0
}
ARGS="$EXTRA"
start_server "$ARGS" || {
  N=$(grep -m1 -oE "at most [0-9]+" /tmp/vllm-$NAME.log | grep -oE "[0-9]+")
  if [ -n "$N" ]; then ARGS="$EXTRA --max-num-seqs $N"; echo "retry with --max-num-seqs $N"; start_server "$ARGS" || { docker rm -f vllm >/dev/null 2>&1; exit 1; }
  else docker rm -f vllm >/dev/null 2>&1; exit 1; fi
}
echo "final server args: [$ARGS]"
echo "ready after ${READY_SECS}s"
docker logs vllm 2>&1 | grep -E "Model loading took|Available KV cache memory|GPU KV cache size|Maximum concurrency" | sed -E 's/^.*\] //' | cut -c1-200
echo "--- thinking-off sanity:"
curl -s http://127.0.0.1:8000/v1/chat/completions -H 'Content-Type: application/json' -d "{\"model\":\"$MODEL\",\"messages\":[{\"role\":\"user\",\"content\":\"쿠버네티스를 한 문장으로 설명해줘.\"}],\"max_tokens\":60,\"temperature\":0,\"chat_template_kwargs\":{\"enable_thinking\":false}}" | python3 -c "import sys,json; d=json.load(sys.stdin); m=d['choices'][0]['message']; print(repr((m.get('content') or '')[:120]), '| reasoning:', repr((m.get('reasoning_content') or m.get('reasoning') or '')[:40]))"
python3 $B/eval_mc.py --model $MODEL --no-think --set $B/evalset_big.json --workers 32 --out $B/c2-$NAME.q1.json
python3 $B/eval_mc.py --model $MODEL --no-think --set $B/evalset_big.json --workers 32 --out $B/c2-$NAME.q2.json
python3 - <<P
import json
a=json.load(open("$B/c2-$NAME.q1.json"))["preds"]; b=json.load(open("$B/c2-$NAME.q2.json"))["preds"]
print("대조군: 같은 서버 두 번 실행 시 답이 달라진 문제 수:", sum(a[k]!=b[k] for k in a), "/", len(a))
P
python3 $B/bench_real.py --model $MODEL --no-think --rates $RATES --duration 25 --stop-after-fail 2 --out $B/c2-$NAME.load.json 2>&1 | tail -10
echo "peak: $(docker logs vllm 2>&1 | grep -oE 'GPU KV cache usage: [0-9.]+%' | sort -t: -k2 -n | tail -1)"
docker stop vllm >/dev/null 2>&1; docker rm -f vllm >/dev/null 2>&1; sleep 3; echo "cleanup: vllm containers=$(docker ps -a --format '{{.Names}}' | grep -c '^vllm$') GPU=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader)"
