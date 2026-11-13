#!/bin/bash
# 멀티테넌시 실습 전체 제거: 테넌트 네임스페이스(쿼터·정책·RBAC·워크로드 포함)와 PV 를 지우고 time-slicing 을 끈다. 서버 VM 에서 실행.
# PV 는 local 타입 + Retain 이라 호스트의 /data/models/hf 데이터는 지워지지 않는다.
export KUBECONFIG=$HOME/.kube/config
kubectl delete ns tenant-a tenant-b --ignore-not-found
kubectl delete pv hf-cache-a hf-cache-b --ignore-not-found
bash "$(dirname "$0")/timeslice-off.sh"
