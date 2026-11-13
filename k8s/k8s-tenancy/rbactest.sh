#!/bin/bash
# tenant-a 의 ServiceAccount 토큰으로 접속하는 임시 kubeconfig 를 만들어 권한을 시험한다 (토큰은 출력하지 않는다).
export KUBECONFIG=$HOME/.kube/config
KC=$(mktemp); chmod 600 $KC; cp $KUBECONFIG $KC
TOK=$(kubectl -n tenant-a create token tenant-admin --duration=10m)
kubectl --kubeconfig=$KC config set-credentials tenant-admin --token="$TOK" >/dev/null
kubectl --kubeconfig=$KC config set-context --current --user=tenant-admin >/dev/null
unset TOK
t() { local what="$1"; shift; out=$(kubectl --kubeconfig=$KC "$@" 2>&1 | head -1 | cut -c1-150); printf "  %-34s -> %s\n" "$what" "$out"; }
echo "[누구인가]"; t "auth whoami" auth whoami
echo "[자기 네임스페이스 tenant-a]"
t "pods 조회" -n tenant-a get pods --no-headers
t "pod 생성" -n tenant-a run rbac-probe --image=busybox:1.36 --restart=Never -- sleep 60
t "쿼터 조회(읽기)" -n tenant-a get resourcequota quota --no-headers
t "쿼터 삭제 시도" -n tenant-a delete resourcequota quota
t "네트워크 정책 삭제 시도" -n tenant-a delete networkpolicy default-deny-ingress
echo "[남의 네임스페이스·클러스터]"
t "tenant-b pods 조회" -n tenant-b get pods
t "노드 조회" get nodes
t "네임스페이스 생성 시도" create namespace x-hack
t "kube-system secrets 조회" -n kube-system get secrets
kubectl -n tenant-a delete pod rbac-probe --ignore-not-found --wait=false >/dev/null 2>&1
rm -f $KC
