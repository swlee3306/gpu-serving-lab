#!/bin/bash
# tenant-a/client 와 tenant-b/client 가 tenant-b 의 web 파드 두 개(gpu 노드, cp 노드)에 닿는지 시험한다.
export KUBECONFIG=$HOME/.kube/config
G=$(kubectl -n tenant-b get pod web-gpu -o jsonpath={.status.podIP}); C=$(kubectl -n tenant-b get pod web-cp -o jsonpath={.status.podIP})
t() { # from-ns target-name ip
  out=$(kubectl -n tenant-$1 exec client -- wget -T 3 -qO- http://$3:8080 2>&1 | head -1)
  [ -n "$out" ] && [[ "$out" == hello* ]] && echo "  tenant-$1 -> $2 : 허용 ($out)" || echo "  tenant-$1 -> $2 : 차단/실패 (${out:-응답 없음})"
}
t a "web-gpu(같은 노드)" $G; t a "web-cp(다른 노드)" $C
t b "web-gpu(같은 노드)" $G; t b "web-cp(다른 노드)" $C
