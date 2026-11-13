#!/bin/bash
# tenant-a ServiceAccount 로 hostPath 파드를 만들어 본다 (읽기 전용으로 /etc/hostname 한 파일만 마운트).
export KUBECONFIG=$HOME/.kube/config
KC=$(mktemp); chmod 600 $KC; cp $KUBECONFIG $KC
TOK=$(kubectl -n tenant-a create token tenant-admin --duration=10m)
kubectl --kubeconfig=$KC config set-credentials tenant-admin --token="$TOK" >/dev/null; kubectl --kubeconfig=$KC config set-context --current --user=tenant-admin >/dev/null; unset TOK
echo "  현재 enforce 라벨: $(kubectl get ns tenant-a -o jsonpath='{.metadata.labels.pod-security\.kubernetes\.io/enforce}')"
kubectl --kubeconfig=$KC apply -f hostpath-pod.yaml 2>&1 | cut -c1-260
sleep 12; kubectl -n tenant-a logs hostpath-probe 2>&1 | head -2
kubectl -n tenant-a delete pod hostpath-probe --ignore-not-found --wait=true >/dev/null 2>&1; rm -f $KC
