#!/usr/bin/env bash
# 홈서버(/root/k8s-lab/rollback.sh): k8s-cp-* OpenStack 리소스만 삭제. --dry-run 이면 출력만.
# 안전장치: 이름이 k8s-cp- 로 시작하는 것만, FIP 는 manifest.txt 의 FIP_ID 만 삭제. 기존 리소스는 건드리지 않는다.
set -u
DRY=0; [ "${1:-}" = "--dry-run" ] && DRY=1
. /home/user/admin-openrc.sh
DIR=/root/k8s-lab
run(){ if [ $DRY = 1 ]; then echo "[dry-run] $*"; else echo "+ $*"; "$@"; fi; }
mget(){ grep -E "^$1=" $DIR/manifest.txt 2>/dev/null | tail -1 | cut -d= -f2; }

echo "== 1. 서버 VM 삭제"
if openstack server show k8s-cp-01 >/dev/null 2>&1; then run openstack server delete --wait k8s-cp-01; else echo "(없음) k8s-cp-01"; fi
echo "== 2. Floating IP 삭제 (manifest 의 FIP_ID 만)"
FIP=$(mget FIP_ID)
if [ -n "$FIP" ] && openstack floating ip show "$FIP" >/dev/null 2>&1; then run openstack floating ip delete "$FIP"; else echo "(없음/미기록) FIP_ID=$FIP"; fi
echo "== 3. 보안그룹 삭제"
if openstack security group show k8s-cp-sg >/dev/null 2>&1; then run openstack security group delete k8s-cp-sg; else echo "(없음) k8s-cp-sg"; fi
echo "== 4. 키페어 삭제"
if openstack keypair show k8s-cp-key >/dev/null 2>&1; then run openstack keypair delete k8s-cp-key; else echo "(없음) k8s-cp-key"; fi
echo "== 5. 커스텀 플레이버(있다면)"
for f in $(openstack flavor list -f value -c Name | grep '^k8s-cp-'); do run openstack flavor delete "$f"; done
echo "== 6. 로컬 개인키 파일"
for f in $DIR/k8s-cp-key $DIR/k8s-cp-key.pub $DIR/node-token.tmp; do [ -e "$f" ] && run rm -f "$f"; done
echo "(manifest.txt/rollback.sh 는 기록 보존을 위해 남김. 완전 삭제는 rm -rf $DIR)"
