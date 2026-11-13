#!/bin/bash
# noisy neighbor: tenant-b 는 가벼운 부하(피해자), tenant-a 는 무거운 부하(시끄러운 이웃). 모든 부하는 각 vLLM 파드 안에서 localhost 로 건다.
export KUBECONFIG=$HOME/.kube/config
run() { kubectl -n tenant-$1 exec -i deploy/vllm -- python3 - "$2" "$3" "$4" < load2.py 2>&1 | tail -1; }
echo "== 1) B 단독 (피해자 기준선, 2회)"
run b B-alone-1 96 4; run b B-alone-2 96 4
echo "== 2) A 가 무거운 부하를 거는 동안 B 가 같은 가벼운 부하 (2회)"
for i in 1 2; do
  ( run a A-heavy-$i 1200 64 > /tmp/a_heavy_$i.out ) &
  sleep 8
  run b B-with-noisy-$i 96 4
  wait; cat /tmp/a_heavy_$i.out
done
echo "== 3) 끝난 뒤 B 단독 (대조군)"
run b B-alone-after 96 4
