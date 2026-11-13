#!/usr/bin/env bash
# gpu-server(/data/k3s-lab-backup/rollback-gpu-server.sh): k3s 에이전트 제거. Docker/monitoring/data(lab,models,monitoring)는 건드리지 않음.
DRY=0; [ "${1:-}" = "--dry-run" ] && DRY=1
run(){ if [ $DRY = 1 ]; then echo "[dry-run] $*"; else echo "+ $*"; "$@"; fi; }
echo "== 1. k3s-agent 제거 (서비스 중지, 바이너리, iptables CNI/KUBE 체인, /var/lib/rancher, /run/k3s)"
if [ -x /usr/local/bin/k3s-agent-uninstall.sh ]; then run sudo /usr/local/bin/k3s-agent-uninstall.sh; else echo "(k3s-agent-uninstall.sh 없음)"; fi
echo "== 2. 데이터/설정 삭제"
for p in /data/k3s /etc/rancher/k3s /etc/rancher /var/lib/kubelet /var/lib/cni /run/flannel /var/lib/rancher; do
  [ -e "$p" ] && { [ "$p" = /etc/rancher ] && { run sudo rmdir "$p" 2>/dev/null || echo "(/etc/rancher 비어있지 않음, 유지)"; continue; }; run sudo rm -rf "$p"; }
done
echo "== 3. flannel/cni 네트워크 인터페이스(있다면)"
for i in flannel.1 cni0; do ip link show $i >/dev/null 2>&1 && run sudo ip link delete $i; done
echo "== 4. 보존: /data/k3s-lab-backup (스냅샷), Docker, monitoring-*, /data/{lab,models,monitoring,containerd}"
echo "검증: bash /data/k3s-lab-backup/verify-rollback.sh"
