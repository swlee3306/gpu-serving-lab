# Step 3. 쿠버네티스 GPU 클러스터 (k3s 컨트롤 플레인 VM + gpu-server 워커) — 실습 가이드

장비: 홈서버(OpenStack, `ssh openstack-host`) · gpu-server(`ssh gpu-server`) · 맥미니(`ssh macmini`)
실무 연결: **GPUaaS/IaaS = "IaaS(OpenStack)로 컨트롤 플레인을 만들고, GPU 노드를 클러스터에 붙여 `nvidia.com/gpu`를 스케줄링 가능한 자원으로 만드는 것".** 이 단계가 그 뼈대다.

> **이 문서의 상태 (2026-11-14 갱신):** 처음(2026-11-02)에는 Phase 0(사전 기록 + 롤백 틀)만 수행하고 나머지는 사용자가 직접 입력할 실습 가이드로 썼다. 이후 **Phase 1~5를 모두 직접 실행했다**(2026-11-02~03). 실제로 본 결과는 "Phase 1 실습 기록", "Phase 4 실습 기록"과 "추가 시험 1~4", "Phase 5 실습 기록" 절에 있다.
> 각 Phase 본문의 "예상 출력"(`[예상]` 표시)은 실행 전에 공식 문서·소스를 보고 적은 **예상이며 실제 결과가 아니다.** 실제 결과가 다르면 기록 절이 기준이다. 3~6장은 Phase 0 종료 시점(2026-11-02)에 쓴 것을 그대로 둔 것이라 현재 상태와 다르다. 현재 상태는 이 README의 "측정한 것 vs 계획한 것"을 본다. 실제 출력이 가이드와 다르면 **가장 처음 나온 ERROR 줄**부터 읽는다.
> 비밀값(k3s 토큰, 개인키)은 이 문서에 쓰지 않는다. "파일에서 읽는 방법"만 적는다.

---

## 0. 목적과 구성도

```
  집 LAN 192.168.1.0/24
  ├─ 맥미니 (kubectl, ~/.kube/k8s-lab.yaml)  ──────► https://<FIP>:6443
  ├─ gpu-server 192.168.1.10  [k3s agent, GPU 워커, 라벨 gpu=true]  ──► https://<FIP>:6443 (조인)
  │     RTX 4070 Ti SUPER · Docker(monitoring-*)는 그대로 공존
  └─ 홈서버(OpenStack) ── router-main ── external-net(Floating IP 풀 .200-.254)
           └─ mgmt-net 10.0.0.0/24
                 └─ k8s-cp-01 (Ubuntu 24.04, m1.medium)  [k3s server, taint control-plane:NoSchedule]
                       Floating IP = <FIP>  (LAN 에서만 22/6443/10250/8472 허용)
```
- 컨트롤 플레인(API 서버, etcd 대체 sqlite, 스케줄러)은 VM에, 실제 워크로드는 gpu-server에서만 돈다.
- 이번 범위: 클러스터 + GPU 노출 + `nvidia-smi` 시험 파드. **vLLM Deployment, time-slicing은 다음 단계.**

## 1. 사전 상태 (Phase 0에서 확인·기록한 사실)

| 항목 | 값 | 출처 |
|---|---|---|
| 홈서버 | 8코어, 메모리 31GB(약 29GB 미사용 할당 가능, free_ram 29242MB), vCPU 사용 1/8, `/` 60GB 여유 | `openstack hypervisor stats show`, `df` |
| Floating IP 풀 | external-subnet `192.168.1.200-254`, 사용 중 1개(`.204` = vm-bastion), 게이트웨이 `.1` | `openstack subnet show` |
| 이미지/플레이버 | `Ubuntu 24.04`, `m1.medium`(2vCPU/4GB/40GB) | `openstack image/flavor` |
| 기존 보안그룹 | `default`, `k8s-cluster`, `brain-memory-sg`, `kcp-test-lab` (수정 금지) | |
| gpu-server | 12코어/30GB, GPU 드라이버 580.178.04, VRAM 16376MiB, `ip_forward=1`, `/data` 246GB 여유, swap 8GB(`/swap.img`) | 스냅샷 |
| gpu-server Docker | monitoring-prometheus-1 / grafana-1 / dcgm-exporter-1 실행 중 | `docker ps -a` |
| gpu-server 리스닝 | tcp 22, 9400(127.0.0.1 dcgm), 9090, 3000, 53(로컬 resolver), tailscale 등. 6443/10250/2379/2380/8472 비어 있음 | `ss -ltnup` |
| Docker 런타임 | `/etc/docker/daemon.json`에 `nvidia` 런타임 등록, containerd root `/data/containerd` (k3s는 **자기 containerd**를 쓰므로 별개) | |

> 주의(홈서버 참고 사항): 보안그룹 `kcp-test-lab` 설명에 "Neutron OVS firewall is noop"라고 적혀 있다. **보안그룹이 실제로 강제되지 않을 수도 있다.** Phase 1의 시험에서 "막혀야 할 것이 막히는지"도 확인해 보자(예: 보안그룹에 없는 포트).

### 버전 고정 (공식 자료로 확인, 2026-11-02 조회)
| 구성 | 버전 | 출처 |
|---|---|---|
| k3s | **`v1.36.5+k3s1`** (stable 채널, GitHub release 정식(prerelease=false), 2026-09-30) | `https://update.k3s.io/v1-release/channels`, GitHub releases API |
| NVIDIA k8s-device-plugin | **`v0.20.1`**, 이미지 `nvcr.io/nvidia/k8s-device-plugin:v0.20.1` (정적 매니페스트) | GitHub releases latest, `deployments/static/nvidia-device-plugin.yml` @ v0.20.1 |
| 시험 파드 이미지 | `nvidia/cuda:12.9.1-base-ubuntu24.04` (Docker Hub 태그 존재 확인; 드라이버 580은 CUDA 13까지 지원하므로 12.9 이미지 실행 가능) | Docker Hub API |
| nvidia-container-toolkit | 1.20.1 (gpu-server에 이미 설치) | |

### 공식 자료로 확인한 사실 (이 가이드의 설계 근거)
1. **swap은 문제 없음.** k3s v1.36.5 소스 `pkg/daemons/agent/agent.go`에서 kubelet 설정이 `FailSwapOn: false`다 → swap이 켜져 있어도 kubelet이 뜬다. **swap을 끄지 않는다** (gpu-server 변경 없음). (다만 파드 메모리 제한은 swap을 고려하지 않으므로 학습용으로는 무시해도 됨.)
2. **NVIDIA 런타임 자동 감지.** k3s docs(Advanced > NVIDIA Container Runtime): k3s는 시작 시 `PATH`에서 `nvidia`, `nvidia-cdi` 등 런타임 실행 파일을 찾아 containerd 설정에 자동 추가하고, 이에 대응하는 RuntimeClass를 만든다. 기본 런타임을 바꾸지 않으면 **파드에 `runtimeClassName: nvidia`를 직접 지정해야 한다.**
3. **device-plugin 정적 매니페스트에는 `runtimeClassName`이 없다** (v0.20.1 yml 확인). k3s에서는 DaemonSet에 `runtimeClassName: nvidia`를 **추가해야** 한다 (Phase 4에서 patch).
4. **CoreDNS는 control-plane taint를 견딘다.** k3s v1.36.5의 `manifests/coredns.yaml` tolerations에 `node-role.kubernetes.io/control-plane:NoSchedule`(Exists)이 있다. 즉 **컨트롤 플레인에 taint를 걸어도 CoreDNS는 서버 VM에 뜰 수 있다.** 목표 문서의 "포드를 gpu-server에서만" 가정은 시스템 파드에는 **성립하지 않을 수 있다** → Phase 4에서 `kubectl get pods -A -o wide`로 반드시 확인하고, 그러면 gpu-server 파드의 DNS가 NAT된 Floating IP 사이 VXLAN(UDP 8472)을 건너게 됨을 이해하는 것이 이 실습의 핵심 학습 포인트 중 하나다.
5. 위 5~6번에서 쓰는 k3s 옵션(`--tls-san`, `--node-external-ip`, `--node-taint`, `--disable`, `--write-kubeconfig-mode`, `data-dir`, `node-label`)은 공식 문서 Server/Agent CLI 항목에 있는 것이다. 설치 직전 `curl -sfL https://get.k3s.io` 스크립트와 `https://docs.k3s.io/cli/server`에서 한 번 더 대조해 보자.

---

## 2. 롤백 (먼저 읽기)

### 준비된 스크립트 (Phase 0에서 생성, **실행하지 않음**)
| 스크립트 | 위치 | 하는 일 |
|---|---|---|
| `rollback.sh` | 홈서버 `/root/k8s-lab/rollback.sh` (로컬 사본: `k8s/k3s-lab/rollback.sh`) | VM `k8s-cp-01` → Floating IP(manifest의 `FIP_ID`) → 보안그룹 `k8s-cp-sg` → 키페어 `k8s-cp-key` → `k8s-cp-*` 플레이버 → 개인키 파일 삭제 |
| `rollback-gpu-server.sh` | gpu-server `/data/k3s-lab-backup/` | `k3s-agent-uninstall.sh` 실행, `/data/k3s`, `/etc/rancher`, `/var/lib/kubelet` 등 삭제. Docker·monitoring·`/data/{lab,models,monitoring,containerd}`·스냅샷은 보존 |
| `verify-rollback.sh` | gpu-server `/data/k3s-lab-backup/` | 롤백 후 `iptables-save`, 리스닝 포트, `docker ps -a`를 **사전 스냅샷과 비교**하고 k3s 잔재 확인 → `PASS`/`FAIL` |
| `rollback-macmini.sh` | MacBook `k8s/k3s-lab/` (맥미니에는 두지 않음 — 맥미니 변경은 kubeconfig 한 파일만 허용) | 맥미니의 `~/.kube/k8s-lab.yaml`만 삭제 |

모두 `--dry-run`을 받으면 **무엇을 지울지만 출력**한다.
```bash
ssh openstack-host '/root/k8s-lab/rollback.sh --dry-run'
ssh gpu-server 'bash /data/k3s-lab-backup/rollback-gpu-server.sh --dry-run'
./k8s/k3s-lab/rollback-macmini.sh --dry-run
```
- **Phase 0 시점 dry-run 결과(실제):** 아직 만든 것이 없어서 모두 "(없음)"만 출력됨. 맥미니는 `k8s-lab.yaml` 없음. 즉 스크립트 문법/분기만 검증했고 **실제 리소스가 있을 때의 삭제 동작은 실행 전 dry-run으로 다시 확인해야 한다.**
- **`verify-rollback.sh`를 현재 상태(아직 아무 변경 없음)에서 실행:** iptables 동일, 포트 동일, 컨테이너 동일, k3s 잔재 없음 → `PASS`. (처음엔 5자리 임시 UDP 포트 2개가 달라 FAIL이어서, 임시 포트는 비교에서 제외하도록 고쳤다.)

### 전체 롤백 순서
1. gpu-server 에이전트 제거 → 2. 홈서버 `rollback.sh`(VM, FIP, SG, 키페어) → 3. 맥미니 kubeconfig 삭제 → 4. `verify-rollback.sh`.
```bash
ssh gpu-server 'bash /data/k3s-lab-backup/rollback-gpu-server.sh'
ssh openstack-host '/root/k8s-lab/rollback.sh'
./k8s/k3s-lab/rollback-macmini.sh
ssh gpu-server 'bash /data/k3s-lab-backup/verify-rollback.sh'    # PASS 기대
```
각 Phase 끝에도 그 단계만의 롤백 명령을 적어 두었다. **리소스를 만들 때마다 홈서버 `/root/k8s-lab/manifest.txt`에 `KEY=ID`를 추가한다** (예: `FIP_ID=...`). 롤백 스크립트는 FIP를 이 파일의 `FIP_ID`로만 지운다(실수로 vm-bastion의 FIP를 지우지 않기 위한 장치).

### 사전 스냅샷 (gpu-server `/data/k3s-lab-backup/`)
`iptables-save.pre.txt`, `ip6tables-save.pre.txt`, `ip-br-addr.pre.txt`, `ss-ltnup.pre.txt`, `unit-files.pre.txt`, `docker-ps-a.pre.txt`, `nvidia-smi.pre.txt`, `sysctl.pre.txt`, `planned-changes.txt`.

---

## Phase 1. 컨트롤 플레인 VM 만들기 + 연결 시험 (홈서버)

**개념.** k3s 서버는 평범한 Linux 한 대면 된다. 우리는 이 "한 대"를 OpenStack(IaaS)에서 API로 만든다: 키페어(접속 열쇠), 보안그룹(허용 포트 목록), VM, Floating IP(LAN에서 보이는 주소). 노드 사이는 NAT된 Floating IP를 통하므로 포트 허용이 중요하다.

```bash
ssh openstack-host
. /home/user/admin-openrc.sh
cd /root/k8s-lab

# 1) 키페어 (개인키는 이 디렉터리에만, mode 600)
openstack keypair create k8s-cp-key > k8s-cp-key && chmod 600 k8s-cp-key
echo "KEYPAIR=k8s-cp-key" >> manifest.txt

# 2) 보안그룹: LAN 에서만 허용 (기본 egress 는 열려 있음)
openstack security group create k8s-cp-sg --description "k3s control plane, LAN only"
SG=$(openstack security group show k8s-cp-sg -f value -c id); echo "SG_ID=$SG" >> manifest.txt
for p in 22 6443 10250; do openstack security group rule create k8s-cp-sg --protocol tcp --dst-port $p --remote-ip 192.168.1.0/24; done
openstack security group rule create k8s-cp-sg --protocol udp --dst-port 8472 --remote-ip 192.168.1.0/24
openstack security group rule create k8s-cp-sg --protocol icmp --remote-ip 192.168.1.0/24

# 3) VM
openstack server create k8s-cp-01 --flavor m1.medium --image "Ubuntu 24.04" \
  --network mgmt-net --security-group k8s-cp-sg --key-name k8s-cp-key --wait
echo "SERVER_ID=$(openstack server show k8s-cp-01 -f value -c id)" >> manifest.txt

# 4) Floating IP
openstack floating ip create external-net -f value -c id -c floating_ip_address
#   → 출력된 id 를 FIP_ID=<id> 로 manifest.txt 에 기록, 주소는 FIP 변수로
FIP=<위에서 나온 192.168.1.2xx>
openstack server add floating ip k8s-cp-01 $FIP
echo "FIP_ID=<id>" >> manifest.txt ; echo "FIP_ADDR=$FIP" >> manifest.txt
```
- 사용 가능한 풀이 `.200-.254`이고 `.204`가 사용 중이라 보통 `.200` 등이 배정된다. **`.204`가 아님을 확인.**
- 이미지 기본 사용자는 보통 `ubuntu`. [예상]

**시험 (끝 확인).**
```bash
openstack server show k8s-cp-01 -c status -c addresses -f value        # ACTIVE, mgmt-net=10.0.0.x, <FIP>
ping -c2 $FIP; nc -zv $FIP 22                                         # 홈서버에서
ssh gpu-server "ping -c2 $FIP; nc -zv $FIP 22"                      # gpu-server 에서
ssh macmini "ping -c2 $FIP; nc -zv $FIP 22"                     # 맥미니에서
ssh -i /root/k8s-lab/k8s-cp-key ubuntu@$FIP '
  curl -sI https://get.k3s.io | head -1      # [예상] HTTP/2 200
  timedatectl | grep -E "synchronized|NTP"   # [예상] System clock synchronized: yes
'
```
- 확인 질문: (a) 보안그룹에 6443 규칙을 넣었는데 ping/22가 되는 이유는? (b) `nc -zv $FIP 9999`는 막혀야 하는데 실제로 막히나? ("OVS firewall noop" 가능성 확인)
- 막히면 첫 번째로 볼 곳: `openstack server show` 상태 → `openstack security group rule list k8s-cp-sg` → 라우터 `router-main`의 external gateway → VM 콘솔 로그 `openstack console log show k8s-cp-01`.
- **Phase 1 롤백:** `ssh openstack-host '/root/k8s-lab/rollback.sh'` (먼저 `--dry-run`).

## Phase 2. k3s 서버 설치 (VM 안)

**개념.** `get.k3s.io` 스크립트가 k3s 바이너리와 systemd 서비스를 설치한다. 서버는 API(6443), 스케줄러, 컨트롤러, (기본) sqlite 저장소를 한 프로세스로 돌린다. `--tls-san`은 API 인증서에 Floating IP를 넣어 LAN에서 그 주소로 접속해도 인증서 오류가 안 나게 한다. `--node-taint`로 서버 노드에는 일반 파드가 안 뜨게 한다. traefik/servicelb는 이번에 필요 없어 끈다.

```bash
ssh -i /root/k8s-lab/k8s-cp-key ubuntu@$FIP
FIP=<Floating IP>   # VM 안에서 다시 지정
VMIP=$(hostname -I | awk '{print $1}')   # 10.0.0.x
curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION=v1.36.5+k3s1 sh -s - server \
  --tls-san $FIP --node-ip $VMIP --node-external-ip $FIP --flannel-external-ip \
  --node-taint node-role.kubernetes.io/control-plane=true:NoSchedule \
  --disable traefik --disable servicelb --write-kubeconfig-mode 600
```
- 기본 제안에서 바꾼 점: `--node-ip $VMIP`와 `--flannel-external-ip` 추가. VM은 NAT 뒤라 자기 NIC에는 10.0.0.x만 있다. flannel이 VXLAN 상대에게 알릴 주소로 외부 IP를 쓰게(`--flannel-external-ip`, 공식 문서 Networking > Distributed hybrid 항목) 하기 위함. 설치 후 문제가 되면 이 옵션을 빼고 비교해 보는 것도 좋은 실험.
- `kubectl` 명령은 서버 안에서 `sudo k3s kubectl ...` 또는 `kubectl`(심볼릭 링크)로.

**검증 (끝 확인).**
```bash
sudo k3s kubectl get nodes -o wide        # [예상] k8s-cp-01  Ready  control-plane,master  v1.36.5+k3s1, EXTERNAL-IP=<FIP>
sudo k3s kubectl get pods -A              # [예상] coredns/local-path/metrics-server 가 Running 이거나, 아직이면 Pending
sudo k3s kubectl describe node k8s-cp-01 | grep -i taint   # [예상] node-role.kubernetes.io/control-plane=true:NoSchedule
exit
ssh gpu-server "curl -sk -o /dev/null -w '%{http_code}\n' https://$FIP:6443/readyz"   # [예상] 401 (인증 없음) 또는 200
```
- **토큰은 출력하지 않는다.** 위치: VM의 `/var/lib/rancher/k3s/server/node-token` (root 전용). 확인은 `sudo ls -l` 로만.
- 확인 질문: 워커가 아직 없는데 파드가 Pending이면 정상인가? CoreDNS가 서버 노드에서 Running이 되는 것은 왜 가능한가? (위 "확인한 사실" 4번 참조)
- 막히면 첫 번째로 볼 곳: `sudo journalctl -u k3s --no-pager | grep -m1 -i error` (가장 처음 ERROR) → 6443 리스닝(`sudo ss -ltnp | grep 6443`).
- **Phase 2 롤백:** VM 안에서 `/usr/local/bin/k3s-uninstall.sh`, 또는 VM 삭제(Phase 1 롤백).

## Phase 3. gpu-server를 에이전트로 조인

**개념.** 에이전트는 서버 API에 토큰으로 인증해 조인하고, 컨테이너 실행(containerd, kubelet)을 담당한다. 토큰은 **파일에만** 둔다(`/etc/rancher/k3s/config.yaml`, mode 600) → 명령행·로그·셸 히스토리에 안 남는다. `data-dir: /data/k3s`로 이미지/데이터가 `/`가 아닌 `/data`에 쌓이게 한다. k3s는 Docker와 별개의 containerd를 쓰므로 Docker·monitoring과 충돌하지 않는다.

```bash
# 1) 토큰을 화면에 보이지 않게 gpu-server 파일로 전달 (MacBook 에서 파이프)
ssh gpu-server 'sudo mkdir -p /etc/rancher/k3s && sudo install -m 600 /dev/null /etc/rancher/k3s/config.yaml'
FIP=<Floating IP>
{ echo "server: https://$FIP:6443"; printf 'token: '; ssh -i <개인키 경로> ubuntu@$FIP 'sudo cat /var/lib/rancher/k3s/server/node-token'
  echo "data-dir: /data/k3s"; echo "node-ip: 192.168.1.10"; echo "node-external-ip: 192.168.1.10"
  echo "node-label:"; echo "  - gpu=true"; } \
  | ssh gpu-server 'sudo tee -a /etc/rancher/k3s/config.yaml >/dev/null'
ssh gpu-server 'sudo ls -l /etc/rancher/k3s/config.yaml; sudo sed -E "s/^(token: ).*/\1<감춤>/" /etc/rancher/k3s/config.yaml'
```
- (개인키는 홈서버에 있으므로, 개인키를 MacBook으로 복사하지 않으려면 위 `{ }` 블록을 홈서버에서 실행하고 gpu-server로 ssh 파이프해도 된다. 단 홈서버→gpu-server ssh 가능 여부는 확인 필요.)
```bash
# 2) 설치
ssh gpu-server 'curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION=v1.36.5+k3s1 sudo -E sh -s - agent'
#   sudo 는 env 전달 때문에: sudo INSTALL_K3S_VERSION=... sh 형태가 필요하면 'curl ... | sudo INSTALL_K3S_VERSION=v1.36.5+k3s1 sh -s - agent'
```

**검증 (끝 확인).**
```bash
# 서버에서
sudo k3s kubectl get nodes -o wide          # [예상] k8s-cp-01, gpu-server(또는 호스트명) 둘 다 Ready
# gpu-server 에서
sudo grep nvidia /data/k3s/agent/etc/containerd/config.toml   # [예상] nvidia, nvidia-cdi 등 runtime 항목
sudo systemctl is-active k3s-agent
docker ps --format '{{.Names}} {{.Status}}'                    # monitoring-* 3개 그대로 Up
curl -s localhost:9090/api/v1/targets | grep -o '"health":"[a-z]*"' | sort | uniq -c   # dcgm up 유지
# 변화 비교 (사전 스냅샷 대비)
sudo ss -ltnup | diff <(awk '{print $1,$5}' /data/k3s-lab-backup/ss-ltnup.pre.txt|sort) <(sudo ss -ltnup | awk '{print $1,$5}'|sort)
sudo iptables-save | diff /data/k3s-lab-backup/iptables-save.pre.txt - | head -40
```
- [예상] 새 리스닝: 10250(kubelet), 6444 등 k3s 로컬 포트, UDP 8472(flannel), 10248/10249 등 로컬. iptables에는 `KUBE-*`, `CNI-*`, `FLANNEL-*` 체인이 추가된다. Docker 체인은 그대로여야 한다.
- 확인 질문: 서버가 gpu-server의 10250에 접근해야 하는 때는 언제인가(`kubectl logs/exec`)? swap이 켜져 있는데 kubelet이 왜 뜨나? (위 "확인한 사실" 1번)
- 막히면 첫 번째로 볼 곳: `sudo journalctl -u k3s-agent --no-pager | grep -m1 -i -E "error|fatal"`. 흔한 원인: 서버 6443 도달 불가(SG/라우팅), 토큰 오타, 시간 불일치, 포트 충돌(10250/8472 → 사전 스냅샷상 비어 있음).
- **Phase 3 롤백:** `ssh gpu-server 'bash /data/k3s-lab-backup/rollback-gpu-server.sh --dry-run'` 확인 후 실행 → `verify-rollback.sh`. 서버 쪽 노드 객체: `sudo k3s kubectl delete node <gpu-node>`.

## Phase 4. NVIDIA device plugin + GPU 시험 파드

**개념.** kubelet은 GPU를 모른다. **device plugin**(DaemonSet)이 GPU를 발견해 kubelet에 "`nvidia.com/gpu` 1개 있음"이라고 등록하고, 파드가 요청하면 해당 장치를 컨테이너에 연결해 준다. 컨테이너 런타임이 GPU 라이브러리를 주입하려면 `nvidia` 런타임이 필요해서, k3s에서는 파드(와 device plugin 자신)에 `runtimeClassName: nvidia`를 붙인다. 이번엔 time-slicing 없이 기본 설정(GPU 1개 = 파드 1개 독점).

```bash
# 서버 VM 안에서 (kubectl)
curl -sfLO https://raw.githubusercontent.com/NVIDIA/k8s-device-plugin/v0.20.1/deployments/static/nvidia-device-plugin.yml
kubectl apply -f nvidia-device-plugin.yml
# k3s 에서는 runtimeClass 필요 + gpu 노드에만 배치
kubectl -n kube-system patch ds nvidia-device-plugin-daemonset --type merge -p \
 '{"spec":{"template":{"spec":{"runtimeClassName":"nvidia","nodeSelector":{"gpu":"true"}}}}}'
kubectl get runtimeclass           # [예상] nvidia 존재
kubectl -n kube-system get pods -l name=nvidia-device-plugin-ds -o wide    # Running on gpu node
kubectl describe node <gpu-node> | grep -A8 -E "^Capacity|^Allocatable"      # [예상] nvidia.com/gpu: 1
```
시험 파드 `gpu-test.yaml`:
```yaml
apiVersion: v1
kind: Pod
metadata: {name: gpu-test}
spec:
  restartPolicy: Never
  runtimeClassName: nvidia
  nodeSelector: {gpu: "true"}
  containers:
  - name: cuda
    image: nvidia/cuda:12.9.1-base-ubuntu24.04
    command: ["nvidia-smi"]
    resources: {limits: {nvidia.com/gpu: 1}}
```
```bash
kubectl apply -f gpu-test.yaml
kubectl wait --for=jsonpath='{.status.phase}'=Succeeded pod/gpu-test --timeout=300s   # 이미지 pull 시간 필요
kubectl logs gpu-test                          # [예상] NVIDIA GeForce RTX 4070 Ti SUPER, Driver 580.178.04
kubectl delete pod gpu-test                    # 정리 (device plugin 은 유지)
```
**노드 간 통신 가정 확인.**
```bash
kubectl get pods -A -o wide                    # 시스템 파드가 어느 노드에 있나? CoreDNS 가 서버 VM 에 있나?
kubectl run dnstest --rm -it --restart=Never --image=busybox:1.36 --overrides='{"spec":{"nodeSelector":{"gpu":"true"}}}' -- nslookup kubernetes.default
```
- [예상] CoreDNS는 서버·gpu 어느 쪽에도 뜰 수 있다(둘 다 스케줄 가능). 서버 VM에 떠 있으면 위 nslookup은 **gpu-server → Floating IP(VXLAN UDP 8472) → VM** 경로를 탄다. 성공하면 NAT된 VXLAN이 동작한다는 증거, 실패(타임아웃)면 `--flannel-external-ip`/SG udp 8472/MTU를 의심.
- 확인 질문: `nvidia.com/gpu`는 누가 노출하나? `runtimeClassName`이 없으면 `nvidia-smi` 시험 파드는 어떻게 실패하나(직접 시험)? Docker vLLM과 이 파드가 GPU를 동시에 쓰면?
- 막히면 첫 번째로 볼 곳: `kubectl -n kube-system logs ds/nvidia-device-plugin-daemonset` 의 첫 ERROR (흔한 것: "Incompatible strategy"/NVML 초기화 실패 = runtimeClassName 누락).
- **Phase 4 롤백:** `kubectl delete -f nvidia-device-plugin.yml; kubectl delete pod gpu-test --ignore-not-found`.

### Phase 4 실습 기록 (2026-11-03, 직접 실행한 결과)
- **kubectl 권한**: 서버를 `--write-kubeconfig-mode 600`으로 설치해서 일반 사용자는 `/etc/rancher/k3s/k3s.yaml`을 못 읽는다. `~/.kube/config`로 사본을 만들어도 k3s의 `kubectl`은 `KUBECONFIG`가 없으면 원본만 읽으므로 `export KUBECONFIG=$HOME/.kube/config`(+`~/.bashrc`)가 필요했다.
- **4-0**: 노드 2개 Ready. 시스템 파드 3개(CoreDNS, local-path-provisioner, metrics-server)가 모두 서버 VM에 떴다(control-plane taint를 견디는 toleration).
- **4-A (runtimeClassName 없이 적용)**: 로그 `Failed to initialize NVML: ERROR_LIBRARY_NOT_FOUND`, `Detected platform: unknown`, 노드에 `nvidia.com/gpu` 없음. nodeSelector가 없어도 gpu-server에만 떴다: 서버 VM은 control-plane taint가 막고, DaemonSet의 toleration은 `nvidia.com/gpu Exists` 하나뿐(`describe node`, `jsonpath`로 확인).
- **4-B (patch)**: `runtimeClassName: nvidia` + `nodeSelector gpu=true` 후 `Detected platform: nvml`, `Registered device plugin for 'nvidia.com/gpu' with Kubelet`, Capacity/Allocatable `nvidia.com/gpu: 1`, 재시작 0.
- **4-C**: `nvidia/cuda:12.9.1-base-ubuntu24.04` 파드의 `nvidia-smi`가 RTX 4070 Ti SUPER, 드라이버 580.178.04를 표시. 서버 VM의 `kubectl logs`가 gpu-server 파드 로그를 가져옴(10250 경로 동작).
- **4-D**: busybox `nslookup kubernetes.default`는 NXDOMAIN(짧은 이름), FQDN `kubernetes.default.svc.cluster.local`은 `10.43.0.1`로 해석. CoreDNS가 서버 VM에만 있고 시험 파드는 gpu-server에 있었으므로 gpu-server → Floating IP → 서버 VM의 VXLAN(UDP 8472) 경로가 동작한 증거. 짧은 이름 실패 원인(busybox가 search 도메인을 안 씀)은 관찰과 일치하지만 문서로 확인하지 못함. 파드의 `resolv.conf`에는 gpu-server 호스트의 search 도메인이 상속됨(공개 글에 출력 그대로 싣지 말 것).
- **시험 1 (Docker vLLM 사용 중 GPU 파드)**: vLLM(util 0.5)이 7,666MiB를 쓰는 동안 `gpu-test` 파드가 정상 스케줄·완료, 파드의 `nvidia-smi`가 `7666MiB / 16376MiB`를 표시. K8s는 Docker 쪽 사용을 모르고 GPU를 비었다고 본다. 동시 계산 시 성능 간섭, 메모리를 거의 다 잡은 경우는 미측정.
- **시험 2 (파드 2개가 각각 GPU 1개 요청)**: 하나는 Running, 다른 하나는 Pending, 이벤트 `0/2 nodes are available: 1 Insufficient nvidia.com/gpu, 1 node(s) had untolerated taint(s).`
- **정리**: gpu-test, gpu-hold 삭제, vLLM 컨테이너 중지(GPU 메모리 0MiB 복귀). device plugin은 유지.

### Phase 4 추가 시험 1~4 (2026-11-03, 직접 실행)
- **시험 A: vLLM을 K8s Deployment로**: 매니페스트 `hostPath /data/models/hf`, `emptyDir(Memory) /dev/shm`, `runtimeClassName: nvidia`, `nvidia.com/gpu: 1`, Service. 첫 이미지 pull(33GB급 이미지) 약 4분, 이후 기동~Ready 약 60초. 서버 VM에서 Service ClusterIP로 chat completions 응답, `/metrics`에 `vllm:kv_cache_usage_perc`, `num_requests_running/waiting` 노출. 이때 이미지 `latest`가 **vLLM 0.33.0**이어서(응답의 `system_fingerprint`) 시리즈 글의 0.32.0 수치(KV 177,120토큰)와 다르다(178,736토큰, util 0.85 동일).
  - **함정**: 처음에 CrashLoop. 첫 ERROR는 `ValueError: VLLM_PORT 'tcp://10.43.112.239:8000' appears to be a URI`. Service 이름이 `vllm`이라 K8s가 `VLLM_PORT` 환경변수를 자동 주입했고 vLLM이 이를 자기 설정으로 읽음. `enableServiceLinks: false`로 해결.
- **시험 B: 메모리 충돌**: Docker vLLM(util 0.9, 14,030MiB)이 GPU를 잡은 상태에서 K8s vLLM 파드(util 0.85)를 올리면 파드는 **스케줄되고**(`nvidia.com/gpu` 1/1 할당) vLLM이 시작 단계에서 `ValueError: Free memory on device cuda:0 (1.56/15.54 GiB) on startup is less than desired GPU memory utilization (0.85, 13.21 GiB)`로 실패, 재시작 반복. Docker vLLM은 끝까지 HTTP 200. K8s는 Docker 쪽 사용을 막지도 알지도 못한다.
- **시험 C: GPU 노드 장애(gpu-server의 k3s-agent 정지)**: 노드 NotReady까지 약 40~46초. 에이전트가 멈춰도 컨테이너는 살아 있어(`KillMode=process`) GPU 메모리 13,294MiB를 계속 점유, 파드는 `1/1 Running`으로 표시, 해당 Endpoint만 `ready=false`. 노드에 `unreachable` taint(NoSchedule, NoExecute)가 붙고, 파드의 `unreachable` toleration 300초 뒤 퇴거: 기존 파드 Terminating에 머물고 새 파드는 Pending(`0/2 nodes are available: 2 node(s) had untolerated taint(s)`). GPU 노드가 1대라 갈 곳이 없다. 에이전트 재시작 후 노드 Ready는 몇 초, 새 파드 Ready까지 약 70초.
- **시험 D: time-slicing (device plugin v0.20.1, `replicas: 4`)**: ConfigMap + `CONFIG_FILE` 환경변수. 적용 후 노드 `nvidia.com/gpu`가 1에서 **4**로 표시됨(광고까지 몇 초 지연). RTX 4070 Ti SUPER(GeForce)에서도 동작. vLLM 파드 두 개(util 0.48씩)가 같은 GPU에 모두 Running(합계 14,814MiB, 할당 2/4).
  - 부하(요청 192개, 동시 32, 128토큰, 서버 VM에서 ClusterIP 호출): A 단독 2,408 tok/s·지연 p50 1.60s·p95 2.13s, B 단독 2,387 tok/s·1.60s·2.13s, **A+B 동시** A 1,157 tok/s·p50 3.40s·p95 4.06s / B 1,154 tok/s·3.42s·3.44s, A 단독 재측정 2,380 tok/s·1.61s. 합계 처리량은 거의 같고(약 2,311 vs 2,408), 각자의 지연은 약 2.1배. 계산 시간을 나눠 쓰는 것이지 격리가 아니다.
  - 미시험: 메모리 격리(한쪽이 메모리를 더 잡는 경우), 장애 전파(한 쪽 크래시가 다른 쪽에 미치는 영향, 공식 README는 같은 fault-domain이라고 설명).
- **시험 E: RuntimeClass 없는 vLLM 파드**(`nvidia.com/gpu: 1` 요청, `runtimeClassName` 없음): CrashLoopBackOff. 첫 줄 `No CUDA runtime is found, using CUDA_HOME='/usr/local/cuda'`, 최종 `RuntimeError: Failed to infer device type`. K8s는 GPU를 할당했지만 컨테이너 안에서는 GPU가 보이지 않는다(device plugin의 NVML 오류와 같은 뿌리).
- **시험 F: GPU 노드 전용 taint**(`nvidia.com/gpu=present:NoSchedule` 을 gpu-server에): `nodeSelector gpu=true`만 있는 일반 파드와 GPU를 요청한 파드가 **둘 다 Pending**(`0/2 nodes are available: 2 node(s) had untolerated taint(s)`). 이 클러스터에서는 GPU 요청 파드에 toleration이 자동으로 붙지 않았다(파드 spec 확인). GPU 파드에 `tolerations: [{key: nvidia.com/gpu, operator: Exists, effect: NoSchedule}]`을 명시하면 gpu-server에서 Running, 일반 파드는 Pending 유지. 기존 device plugin 파드는 toleration이 있어 영향 없음. `kubectl taint nodes gpu-server nvidia.com/gpu-`로 제거해 복구.
- **시험 G: 컨트롤 플레인(서버 VM) 장애**(`openstack server stop`, 약 6초 만에 SHUTOFF):
  - 이미 떠 있던 vLLM 파드는 계속 응답(gpu-server에서 파드 IP로 직접 호출). 에이전트 로그에 `Server 192.168.1.251:6443@ACTIVE*->FAILED from failed dial`, `Remotedialer proxy error; reconnecting...` 기록. 이 동안 `kubectl`은 쓸 수 없다.
  - 서버가 꺼진 채 vLLM 컨테이너를 `crictl stop`하자 kubelet이 컨트롤 플레인 없이 **1초 만에** 새 컨테이너를 만들었다(attempt 1). 그러나 새 컨테이너는 CoreDNS(서버 VM)가 없어 `Temporary failure in name resolution`으로 Hugging Face 확인을 반복(`Could not reach the Hub ... Using cached commit hash`)하며 5분 넘게 기동하지 못했다.
  - 서버 VM 시작 후 약 30초에 두 노드 모두 Ready(k3s 서버가 자동 기동, 기존 파드 이름 유지, RESTARTS 1은 내가 중지시킨 컨테이너), 약 86초에 vLLM `/health` 200.
  - 교훈: 컨트롤 플레인이 죽어도 돌고 있는 워크로드는 유지되지만, 재시작이 필요한 순간 클러스터 DNS(서버 VM에 있는 CoreDNS)에 의존하는 컨테이너는 복구되지 않는다. 컨트롤 플레인과 CoreDNS를 gpu-server에도 두거나 DNS 의존을 줄여야 한다(미시험).
- **시험 H: CoreDNS 이중화로 서버 VM 장애 시 DNS 지키기**(2026-11-03, gpu-server에 `dnsprobe` 파드를 두고 1초마다 `nslookup <FQDN>`, 타임아웃 3초, 서버 VM을 90초 중지):
  - 창 A(CoreDNS 1개, 서버 VM에): 조회 25번 중 22번 실패(성공 3번은 정지 직후 몇 초). VM 시작 후 첫 성공은 시작 요청 약 34초 뒤.
  - 창 B(CoreDNS 2개, 노드마다 하나, 기존 `topologySpreadConstraints`로 자동 분산): 41번 중 24번 성공, 17번 실패(약 41% 실패). 원인: gpu-server의 kube-proxy 규칙에 두 엔드포인트(`10.42.0.x` 서버 VM, `10.42.1.x` gpu-server)가 `statistic --mode random --probability 0.5`로 남아 있어서, 서버가 꺼져 엔드포인트가 갱신되지 않으면 절반이 죽은 쪽으로 간다(iptables-save로 직접 확인). 표본이 작아 41%는 대략값.
  - 창 C(CoreDNS 2개 + `kube-dns` Service에 `internalTrafficPolicy: Local`): 서버 VM이 꺼진 90초 동안 93번 전부 성공(최대 소요 0초), VM 시작 후 90초 동안 53번 전부 성공. gpu-server에는 로컬 엔드포인트 하나만 남음(`KUBE-SVL` 체인). Kubernetes 문서: Local이면 kube-proxy는 같은 노드의 엔드포인트만 쓰고, 로컬 엔드포인트가 없는 노드의 파드에게는 엔드포인트가 없는 것처럼 동작한다.
  - 시험 G 재현: 서버 VM을 끈 채 vLLM 컨테이너를 `crictl stop`하자 약 55초에 `/health` 200(Local 정책 적용 상태). 이전에는 5분 넘게 기동하지 못했다.
  - 서버 VM 재시작 뒤에도 replicas 2와 `internalTrafficPolicy: Local`이 유지됐다(각 1회 관찰). K3s 문서는 패키지 구성요소 매니페스트를 "수정하지 말라", 서버가 시작될 때 파일을 다시 쓴다고 설명하므로 업그레이드나 다른 조건에서 되돌아갈 수 있다(미시험).
  - 한계: 각 창 1회 관찰, 서버 장애만 시험(gpu-server가 꺼지는 경우, Local 정책에서 로컬 CoreDNS 파드가 죽는 경우는 미시험). 로컬 CoreDNS가 없는 노드의 파드는 DNS가 안 풀리는 부작용이 있다.
  - 롤백: `kube-dns` Service를 `internalTrafficPolicy: Cluster`로 되돌리고 `coredns`를 `--replicas=1`로 줄임. **줄인 뒤 남은 CoreDNS 파드가 서버 VM이 아니라 gpu-server에 있다**(원래는 서버 VM). 재적용: `kubectl -n kube-system scale deploy coredns --replicas=2` 후 Service에 `internalTrafficPolicy: Local` 패치.
- **롤백**: 시험용 Deployment/Service/ConfigMap 삭제, device plugin DaemonSet 삭제 후 재생성(+`runtimeClassName: nvidia`, `nodeSelector gpu=true` 패치). `nvidia.com/gpu` 1 복귀, `gpu-test` 파드 정상, Docker 컨테이너는 monitoring 3개만 남음. 변경 전 DaemonSet 백업은 작업 임시 폴더의 `ds-backup-before-timeslice.yaml`.

## Phase 5. kubeconfig를 맥미니에 + 확인

**개념.** kubeconfig = API 주소 + 인증서(신원). 서버가 만든 파일의 `server:`는 `127.0.0.1`이라 Floating IP로 바꿔야 외부에서 쓸 수 있다. 기존 `~/.kube/config`는 건드리지 않고 **별도 파일**로 둔다.

```bash
# MacBook 에서 파이프로 (파일 내용은 출력하지 않음)
FIP=<Floating IP>
ssh -i <개인키> ubuntu@$FIP 'sudo cat /etc/rancher/k3s/k3s.yaml' \
 | sed "s#https://127.0.0.1:6443#https://$FIP:6443#" \
 | ssh macmini 'umask 077; cat > ~/.kube/k8s-lab.yaml'
ssh macmini 'ls -l ~/.kube/k8s-lab.yaml; KUBECONFIG=~/.kube/k8s-lab.yaml kubectl get nodes -o wide'
```
- [예상] 파일 `-rw-------`, 노드 2개 Ready. 기존 `~/.kube/config`는 그대로(`ls -l`의 수정 시각 비교).
- 확인 질문: kubeconfig에 비밀이 들어 있나? 이 파일이 새어 나가면 무슨 일이 생기나?
- **Phase 5 롤백:** `rollback-macmini.sh`.

### Phase 5 실습 기록 (2026-11-03, 사용자가 직접 실행)
- MacBook에서 홈서버를 거쳐 서버 VM의 `~/.kube/config`(Phase 4에서 만든 사본)를 읽고, `sed`로 `https://127.0.0.1:6443`을 `https://<FloatingIP>:6443`으로 바꿔 맥미니의 `~/.kube/k8s-lab.yaml`로 파이프(출력 없음, `umask 077`).
- 결과: 파일 `-rw-------`, `grep -c 127.0.0.1` 0, 맥미니 `kubectl`(v1.35.2, 서버 v1.36.5)로 `get nodes` 2개 Ready, `kubectl -n kube-system logs ds/nvidia-device-plugin-daemonset --tail=3`이 gpu-server kubelet까지 경유해 출력됨. 맥미니에는 기존 `~/.kube/config`가 없었고(`cache` 폴더만), 별도 파일로만 두었다.
- 5-A(맥미니에서 `nc -zv <FloatingIP> 6443`)는 건너뛰었으나 `get nodes` 성공으로 같은 사실이 확인됨. 마이너 버전 차이 1(1.35 클라이언트, 1.36 서버)에서 조회는 정상이며, Kubernetes 버전 스큐 정책은 kubectl을 API 서버와 마이너 버전 하나 차이(위든 아래든)까지 지원한다고 설명한다. 복사한 kubeconfig의 클라이언트 인증서는 `O=system:masters, CN=system:admin`, 유효기간 1년(2027-11-02까지)이고, K3s 문서상 복사본은 자동 갱신되지 않으므로 만료 전에 다시 복사해야 한다(갱신 동작은 미시험).
- 롤백: `ssh macmini 'rm ~/.kube/k8s-lab.yaml'`. 사용 시 `KUBECONFIG=~/.kube/k8s-lab.yaml`을 붙인다.

---

## 3. 막혔던 점과 해결 (Phase 0 기준)
- gpu-server `/data/k3s-lab-backup`을 `sudo mkdir`로 만들고 `user` 권한으로 파일을 쓰려다 첫 시도의 첫 에러가 `iptables-save.pre.txt: Permission denied`. 원인: 디렉터리가 root 소유. 해결: `chown -R user:` 후 재실행(출력만 root 권한이 필요한 명령은 `sudo`로 실행하고 리다이렉트는 user 셸이 수행하도록).
- `verify-rollback.sh` 첫 시험에서 포트 비교 FAIL: 5자리 임시 UDP 포트(37005, 52864)가 스냅샷 이후 새로 생김. 해결: 임시 포트는 비교에서 제외.
- Phase 1~5는 아직 실행 전이므로 해당 항목 없음.

## 4. 확인한 것 vs 하지 못한 것
확인함: 접속 3곳, gpu-server `sudo` 무비밀번호, 사전 스냅샷 생성, FIP 풀 여유, 하이퍼바이저 여유, 버전(k3s/device plugin/CUDA 이미지), k3s `FailSwapOn=false`(소스), CoreDNS toleration(소스), 정적 매니페스트에 runtimeClassName 없음, 롤백 스크립트 dry-run(리소스 없는 상태), `verify-rollback.sh` PASS(변경 전 상태).
하지 못함: Phase 1~5 전부(실행하지 않음), 보안그룹이 실제로 강제되는지, 이미지 기본 사용자명 `ubuntu` 여부, NAT된 VXLAN 동작, device plugin 동작, 롤백 스크립트의 실제 삭제 동작.

## 5. 확인 질문 (답은 비워 둠)
1. 컨트롤 플레인에 taint를 건 이유는? 그래도 CoreDNS가 서버에 뜰 수 있는 이유는?
   - 답:
2. `nvidia.com/gpu`는 누가 노출하나? (kubelet? 드라이버? device plugin?)
   - 답:
3. `runtimeClassName: nvidia`가 필요한 이유는?
   - 답:
4. 컨트롤 플레인 VM이 꺼지면 이미 돌던 파드는? 새 파드는?
   - 답:
5. Docker로 띄운 vLLM과 k8s 파드가 GPU를 동시에 쓰면?
   - 답:

## 6. 현재 상태 요약 (Phase 0 종료 시점)
- 홈서버: 생성한 OpenStack 리소스 **없음** (`/root/k8s-lab/` 디렉터리, `manifest.txt`(비어 있음), `rollback.sh`만 생성). VM·FIP 없음.
- gpu-server: `/data/k3s-lab-backup/` 생성(스냅샷 + 스크립트 2종). k3s 미설치, 변경 없음. Docker/monitoring 3개 정상 Up.
- 맥미니: 변경 없음 (`~/.kube/k8s-lab.yaml` 없음).

---

## 실습 기록 — Phase 1 (2026-11-02, 사용자가 직접 실행)

생성한 리소스(ID 일부): 키페어 `k8s-cp-key`, 보안그룹 `k8s-cp-sg`(df50dc7d…), VM `k8s-cp-01`(bad2afe3…, `mgmt-net 10.0.0.68`), Floating IP `192.168.1.251`(bc15df85…). `rollback.sh --dry-run`이 위 순서(VM → FIP(ID) → 보안그룹 → 키페어 → 키 파일)로 지울 대상만 보여 줌을 확인(기존 vm-bastion/.204는 대상 아님).

연결 시험 결과(홈서버에서)
- `ping $FIP`: 2/2 응답, 0.34~1.56ms, **ttl=63**(라우터 1홉을 지나옴).
- `nc 22`: succeeded / `nc 6443`: Connection refused(허용된 포트, 아직 k3s 없음 → 정상) / **`nc 9999`: Connection refused** (예상은 timed out).
- VM 안: 호스트 `k8s-cp-01`, 커널 `6.8.0-87-generic`, `get.k3s.io` → HTTP/2 200, `System clock synchronized: yes`, 메모리 3,915MB, 디스크 38G(1.7G 사용).

### 발견: 이 OpenStack에서 보안그룹은 적용되지 않는다
- 9999(허용하지 않은 포트)가 timed out이 아니라 refused → 패킷이 VM 커널까지 도달했다는 뜻 = **보안그룹이 막지 않았다.**
- 홈서버 설정으로 확인: `ml2_conf.ini`의 `mechanism_drivers = openvswitch,l2population`, 그리고 `openvswitch_agent.ini`에 **`firewall_driver = noop`, `enable_security_group = False`**. (`linuxbridge_agent.ini`에는 iptables 방화벽 드라이버가 있지만, 쓰이는 메커니즘이 openvswitch라서 적용되지 않는다.)
- 의미: 규칙을 넣은 것(`k8s-cp-sg`)과 규칙이 적용되는 것은 다르다. 이 랩에서 VM은 LAN에서 **모든 포트**에 닿는다. 테넌트 격리를 보안그룹에 기대는 설계는 이 랩에서는 성립하지 않는다(Step 4에서 반드시 다시 볼 것).
- k3s 컨트롤 플레인의 실제 위험은 제한적이다(6443과 10250은 인증이 필요하고, 우분투 클라우드 이미지는 기본적으로 키 로그인). 다만 보강책으로 **VM 안 호스트 방화벽**(ufw/nftables)을 둘 수 있다. k3s와의 호환(파드·서비스 CIDR 10.42.0.0/16, 10.43.0.0/16 허용 등)은 설치 전에 확인이 필요하다. Neutron 설정(`firewall_driver`) 변경은 vm-bastion을 포함한 기존 VM에 영향을 주므로 하지 않는다.
- (참고) 1-E의 첫 규칙 확인 명령은 열 이름이 틀렸다: `-c Protocol`이 아니라 `-c "IP Protocol"`.


### 가이드 정정 (2026-11-02, Phase 3 직전)
- **에이전트 설정에서 `flannel-external-ip: true`를 뺐다.** k3s 공식 문서(Networking > Distributed hybrid or multicloud)에 따르면 이 옵션은 **서버에만** 주고, 에이전트는 `--node-external-ip`만 준다. 서버에는 이미 적용돼 있고(노드 annotation `flannel.alpha.coreos.com/public-ip=192.168.1.251` 확인), 에이전트는 자기 LAN 주소(192.168.1.10)가 곧 노드 IP다.
- 같은 문서는 멀티클라우드에서 `--flannel-backend=wireguard-native`와 함께 쓰라고 하지만, 지금 서버는 기본 VXLAN으로 설치했다. VXLAN(UDP 8472)이 NAT된 Floating IP를 건너 동작하는지는 Phase 4에서 직접 시험하고, 실패하면 서버를 wireguard-native로 재설치하는 것이 대안이다(UDP 51820 필요, 롤백은 `k3s-uninstall.sh`).
- Phase 2 확인 결과: 파드 3개 `Running`(NODE 모두 k8s-cp-01), CoreDNS toleration에 control-plane `Exists` 포함, 인증서 SAN에 `192.168.1.251` 포함, gpu-server에서 `/readyz` → 401, 토큰 파일 `-rw------- root root`(실제 파일 `token`, `node-token`은 링크).

---

## 출처 확인 기록 (2026-11-03) — 제 기억·추정으로 쓴 부분을 공식 자료로 확인하고 정정
| 항목 | 결과 | 출처 |
|---|---|---|
| `hide_hypervisor_id` 이름 | **정정**: 접두사 없는 이름은 Nova 문서상 **deprecated**, 권장은 `hw:hide_hypervisor_id`. 우리 flavor(`gpu.gtx660`)는 아직 접두사 없는 이름을 쓰며 동작은 함(마이그레이션은 하지 않음) | docs.openstack.org/nova/latest/configuration/extra-specs.html |
| 포럼 글의 성격 | **정정**: Quadro P400, 해결은 커뮤니티 사용자의 `cpu: host,hidden=1` 답변, 원인 설명은 글에 없음. "드라이버가 VM을 감지해 거부한다"는 **우리 가설** | forums.developer.nvidia.com/t/…/199720 |
| GeForce 패스스루 공식 안내 | R465 이상, **Windows 게스트** 대상 베타 → 우리 경우(Linux 게스트, 470, Kepler)와 범위 다름 | nvidia.custhelp.com/app/answers/detail/a_id/5173 |
| 470 드라이버와 GTX 660 | GTX 660(PCI ID 11C0)이 470.xx 레거시 목록에 있음("Kepler 마지막 브랜치"라는 문구는 페이지에 없음) | nvidia.com/en-us/drivers/unix/legacy-gpu/ |
| Floating IP | one-to-one NAT으로 구현 | docs.openstack.org/neutron/latest/admin/intro-nat.html |
| OVS 방화벽 | 네이티브 OVS 방화벽은 conntrack 필요, `[securitygroup] firewall_driver = openvswitch`로 켬. 우리는 `noop`, `br-int` 흐름 24개 중 `ct(` 0개 | docs.openstack.org/neutron/latest/admin/config-ovsfwdriver.html + 직접 확인 |
| 9999 `refused` | 홈서버·gpu-server·맥미니 세 곳 모두 확인(2026-11-03). 닫힌 포트의 RST 응답은 TCP 표준 | RFC 9293 3.10.7.1 |
| 6444 | `k3s-agent` 프로세스가 듣는 로컬 로드밸런서, 로그로 확인 | `ss`, `journalctl`, docs.k3s.io/architecture |
| `flannel-external-ip` | 서버 전용(에이전트는 `node-external-ip`) | docs.k3s.io/networking/distributed-multicloud |
| 단일 서버 기본 저장소 | SQLite | docs.k3s.io/datastore |
| MIG / time-slicing / vGPU | MIG는 SM·메모리 경로 분리, time-slicing은 replica 사이 메모리·장애 격리 없음, vGPU 지원 GPU 목록(Ubuntu 지원 페이지)에 GeForce 없음 | MIG User Guide, GPU Operator 문서, NVIDIA vGPU 문서 |
| GPU 사용률 정의 | "지난 샘플 구간 중 하나 이상의 커널이 실행되던 시간의 비율"(샘플 구간 1초~1/6초) | `nvidia-smi --help-query-gpu` |
| dcgm-exporter 수집 주기 | `--collect-interval` 기본 30000ms | `dcgm-exporter --help`(4.6.1-4.8.4) |
| 전력 | 전력 제한 285W(기본=현재, 범위 100~320W), Prometheus 3일 최대 265W, 온도 최대 62℃ | `nvidia-smi -q -d POWER`, Prometheus |
| Docker 저장소 | containerd 이미지 저장소는 Docker Engine 29.0 이상 **신규 설치**의 기본값 | docs.docker.com/engine/storage/containerd/ |

확인하지 못한 것: time-slicing의 지원 GPU 범위, time-slicing이 옆 파드 지연에 주는 영향의 실제 크기, `kubectl logs/exec`가 정확히 어떤 경로를 타는지(문서는 에이전트가 먼저 여는 웹소켓 연결까지만 설명).

### flavor 속성 이름 변경 완료 (2026-11-03, 사용자 승인)
- 변경 전 기록: 홈서버 `/root/flavor-gpu.gtx660-before-20261103.txt`(변경 전 `{'pci_passthrough:alias': 'gtx660:1', 'hide_hypervisor_id': 'true'}`).
- 변경: `openstack flavor set gpu.gtx660 --property hw:hide_hypervisor_id=true` 후 `openstack flavor unset gpu.gtx660 --property hide_hypervisor_id`. 최종 속성 `{'pci_passthrough:alias': 'gtx660:1', 'hw:hide_hypervisor_id': 'true'}`.
- 검증: 변경 전 이 flavor를 쓰는 VM 없음. 새 이름으로 시험 VM(`flavor-test-hw`)을 만들어 libvirt XML에 `<hidden state='on'/>`과 GPU `hostdev`를 확인(옛 이름 때와 같은 결과), 시험 VM 삭제, 남은 포트·볼륨 없음, 남은 VM은 `k8s-cp-01`·`vm-bastion`뿐.
- 되돌리려면: `openstack flavor unset gpu.gtx660 --property hw:hide_hypervisor_id` 후 `openstack flavor set gpu.gtx660 --property hide_hypervisor_id=true`.
