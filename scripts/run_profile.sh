#!/bin/bash
# usage: run_profile.sh NAME MODEL "extra vllm args" MODE(load|startup) NOTHINK(0|1)
# Records: startup log numbers, idle VRAM, and (MODE=load) GPU util / VRAM / power during a fixed load (4 req/s, 25 s).
NAME=$1; MODEL=$2; EXTRA=$3; MODE=${4:-load}; NOTHINK=${5:-0}
B=/data/lab/bench; OUT=$B/profile-$NAME.json
docker ps -a --format '{{.Names}}' | grep -q '^vllm$' && { echo "ABORT: a vllm container exists"; exit 1; }
echo "##### $NAME model=$MODEL args=[$EXTRA] mode=$MODE"
start_server() {
  docker rm -f vllm >/dev/null 2>&1
  docker run -d --gpus all --ipc=host --name vllm -v /data/models/hf:/root/.cache/huggingface -p 127.0.0.1:8000:8000 vllm/vllm-openai:latest --model $MODEL $1 >/dev/null
  local t0=$(date +%s)
  until curl -s -m 2 http://127.0.0.1:8000/v1/models >/dev/null 2>&1; do
    sleep 4
    if [ "$(docker inspect -f '{{.State.Running}}' vllm 2>/dev/null)" != "true" ]; then docker logs vllm > /tmp/vllm-$NAME.log 2>&1; echo "CONTAINER EXITED"; grep -m1 -E "^(ValueError|RuntimeError|torch.*Error|.*OutOfMemory)" /tmp/vllm-$NAME.log | cut -c1-260; return 1; fi
    [ $(( $(date +%s) - t0 )) -gt 900 ] && { docker logs vllm > /tmp/vllm-$NAME.log 2>&1; echo "NOT READY (timeout)"; return 1; }
  done
  READY_SECS=$(( $(date +%s) - t0 )); return 0
}
ARGS="$EXTRA"
start_server "$ARGS" || {
  N=$(grep -m1 -oE "at most [0-9]+" /tmp/vllm-$NAME.log | grep -oE "[0-9]+")
  if [ -n "$N" ]; then ARGS="$EXTRA --max-num-seqs $N"; echo "retry with --max-num-seqs $N"; start_server "$ARGS" || { docker rm -f vllm >/dev/null 2>&1; echo "{\"name\":\"$NAME\",\"failed\":true}" > $OUT; exit 1; }
  else docker rm -f vllm >/dev/null 2>&1; echo "{\"name\":\"$NAME\",\"failed\":true}" > $OUT; exit 1; fi
}
sleep 3
docker logs vllm > /tmp/vllm-$NAME.log 2>&1
IDLE=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits | head -1 | tr -d ' ')
echo "ready ${READY_SECS}s | idle VRAM ${IDLE} MiB"
grep -E "Model loading took|Available KV cache memory|GPU KV cache size|Maximum concurrency" /tmp/vllm-$NAME.log | sed -E 's/^.*\] //' | cut -c1-200
rm -f /tmp/smi-$NAME.csv
if [ "$MODE" = "load" ]; then
  nvidia-smi --query-gpu=utilization.gpu,memory.used,power.draw,temperature.gpu --format=csv,noheader,nounits -lms 1000 > /tmp/smi-$NAME.csv &
  SP=$!
  FLAG=""; [ "$NOTHINK" = "1" ] && FLAG="--no-think"
  python3 $B/bench_real.py --model $MODEL $FLAG --rates 4 --duration 25 --out /tmp/bench-$NAME.json 2>&1 | tail -3
  kill $SP 2>/dev/null; sleep 1
fi
PEAKKV=$(docker logs vllm 2>&1 | grep -oE 'GPU KV cache usage: [0-9.]+%' | sort -t: -k2 -n | tail -1)
python3 - <<P
import json, re, os
log = open("/tmp/vllm-$NAME.log", errors="ignore").read()
def g(p, cast=float):
    m = re.search(p, log); return cast(m.group(1).replace(",", "")) if m else None
d = {"name": "$NAME", "model": "$MODEL", "args": "$ARGS", "mode": "$MODE", "ready_s": $READY_SECS, "idle_vram_mib": int($IDLE),
     "weights_gib": g(r"Model loading took ([0-9.]+) GiB"), "kv_gib": g(r"Available KV cache memory: ([0-9.]+) GiB"),
     "kv_tokens": g(r"GPU KV cache size: ([0-9,]+) tokens", int), "max_conc": g(r"Maximum concurrency for [0-9,]+ tokens per request: ([0-9.]+)x"),
     "peak_kv_pct": "$PEAKKV".split(":")[-1].strip() if "$PEAKKV" else None}
if "$MODE" == "load" and os.path.exists("/tmp/smi-$NAME.csv"):
    rows = [[float(x) for x in l.split(",")] for l in open("/tmp/smi-$NAME.csv") if l.strip().count(",") == 3]
    act = [r for r in rows if r[0] > 5] or rows
    d["load"] = {"samples": len(rows), "util_avg": round(sum(r[0] for r in act) / len(act), 1), "util_max": max(r[0] for r in rows),
                 "vram_peak_mib": max(r[1] for r in rows), "power_avg_w": round(sum(r[2] for r in act) / len(act), 1), "power_max_w": max(r[2] for r in rows), "temp_max_c": max(r[3] for r in rows)}
    try:
        b = json.load(open("/tmp/bench-$NAME.json"))["rows"][0]; d["bench"] = {k: b.get(k) for k in ("throughput_tps", "per_request_tps_p50", "ttft_p50_ms", "ttft_p95_ms", "peak_inflight", "ok")}
    except Exception as e: d["bench_err"] = str(e)
json.dump(d, open("$OUT", "w"), ensure_ascii=False, indent=1); print(json.dumps(d, ensure_ascii=False))
P
docker stop vllm >/dev/null 2>&1; docker rm -f vllm >/dev/null 2>&1; sleep 3; echo "cleanup: GPU=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader)"
