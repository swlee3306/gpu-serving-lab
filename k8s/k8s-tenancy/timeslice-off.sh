#!/bin/bash
# time-slicing 설정을 제거하고 device plugin 을 원래(GPU 1개 = 파드 1개) 구성으로 되돌린다. 서버 VM 에서 실행.
export KUBECONFIG=$HOME/.kube/config
kubectl delete cm nvdp-config -n kube-system --ignore-not-found
kubectl delete -f ~/nvidia-device-plugin.yml --ignore-not-found
kubectl apply -f ~/nvidia-device-plugin.yml
kubectl -n kube-system patch ds nvidia-device-plugin-daemonset --type merge -p '{"spec":{"template":{"spec":{"runtimeClassName":"nvidia","nodeSelector":{"gpu":"true"}}}}}'
kubectl -n kube-system rollout status ds/nvidia-device-plugin-daemonset --timeout=90s
