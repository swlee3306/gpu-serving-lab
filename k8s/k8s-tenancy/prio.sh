#!/bin/bash
# 공유 vLLM(max-num-seqs 16) 하나에 이웃 A(무거운 부하)와 피해자 B(가벼운 부하)가 함께 들어온다. 사용: prio.sh fcfs|priority
export KUBECONFIG=$HOME/.kube/config
mode=$1
run() { kubectl -n gpu-shared exec -i deploy/vllm -- python3 - "$@" < ~/tenancy/load3.py 2>&1 | tail -1; }
echo "== [$mode] B 단독 기준선"; run B-alone 48 4
if [ "$mode" = priority ]; then PA=10; PB=0; else PA=""; PB=""; fi
echo "== [$mode] A 무거운 부하 중 B"
( run A-heavy 800 64 $PA > /tmp/prio_a.out ) &
sleep 8
run B-with-A 48 4 $PB
wait; cat /tmp/prio_a.out
