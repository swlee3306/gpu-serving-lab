#!/usr/bin/env bash
# gpu-server: 롤백 후 사전 스냅샷과 비교. 차이가 없으면 PASS.
B=/data/k3s-lab-backup; fail=0
norm(){ sed -E 's/\[[0-9]+:[0-9]+\]/[0:0]/; /^#/d; /^COMMIT/d' ; }
echo "== iptables (Docker 규칙은 컨테이너 재시작 시 달라질 수 있음 -> diff 내용 확인)"
diff <(norm < $B/iptables-save.pre.txt) <(sudo iptables-save | norm) && echo "iptables 동일" || { echo "iptables 차이 있음"; fail=1; }
echo "== 리스닝 포트(프로세스 제외, 5자리 임시 udp 포트 제외)"
diff <(awk 'NR>1{print $1,$5}' $B/ss-ltnup.pre.txt | grep -vE '^udp .*:[0-9]{5}$' | sort) <(sudo ss -ltnup | awk 'NR>1{print $1,$5}' | grep -vE '^udp .*:[0-9]{5}$' | sort) && echo "포트 동일" || { echo "포트 차이 있음"; fail=1; }
echo "== docker ps -a"
diff <(awk -F'\t' '{print $1,$2}' $B/docker-ps-a.pre.txt | sort) <(docker ps -a --format '{{.Names}}\t{{.Image}}\t{{.Status}}' | awk -F'\t' '{print $1,$2}' | sort) && echo "컨테이너 동일" || { echo "컨테이너 차이 있음"; fail=1; }
echo "== k3s 잔재"
for p in /data/k3s /etc/rancher/k3s /usr/local/bin/k3s /etc/systemd/system/k3s-agent.service; do [ -e $p ] && { echo "잔재: $p"; fail=1; }; done
ip -br addr | grep -E 'flannel|cni0' && fail=1
[ $fail = 0 ] && echo "PASS" || echo "FAIL (위 차이 확인)"; exit $fail
