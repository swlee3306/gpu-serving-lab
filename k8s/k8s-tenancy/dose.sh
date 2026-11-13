#!/bin/bash
# 이웃(tenant-a)의 동시 요청 수를 4/16/64로 바꿔 가며, 피해자(tenant-b, 가벼운 부하 동시 4)의 지연 변화를 잰다. 서버 VM에서 실행.
export KUBECONFIG=$HOME/.kube/config
run() { kubectl -n tenant-$1 exec -i deploy/vllm -- python3 - "$2" "$3" "$4" < load2.py 2>&1 | tail -1; }
echo "== B 단독 기준선"; run b B-alone 48 4
for cfg in "4 200" "16 600" "64 1200"; do
  set -- $cfg
  ( run a A-c$1 $2 $1 > /tmp/dose_a_$1.out ) &
  sleep 8
  run b B-with-A-c$1 48 4
  wait; cat /tmp/dose_a_$1.out
done
echo "== 끝난 뒤 B 단독"; run b B-alone-after 48 4
