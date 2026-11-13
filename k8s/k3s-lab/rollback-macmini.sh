#!/usr/bin/env bash
# MacBook(또는 ssh 가능한 곳)에서 실행: 맥미니의 ~/.kube/k8s-lab.yaml 만 삭제. ~/.kube/config 는 건드리지 않는다.
DRY=0; [ "${1:-}" = "--dry-run" ] && DRY=1
if [ $DRY = 1 ]; then
  echo "[dry-run] ssh macmini 'rm -f ~/.kube/k8s-lab.yaml'"; ssh macmini 'ls -l ~/.kube/k8s-lab.yaml 2>&1'
else ssh macmini 'rm -f ~/.kube/k8s-lab.yaml'; fi
