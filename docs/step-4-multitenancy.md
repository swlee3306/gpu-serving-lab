# Step 4. 멀티테넌시 (2026-11-03 실습 기록)

목표: 테넌트별 GPU 쿼터와 격리. 환경: k3s(서버 VM + gpu-server), GPU 1장(RTX 4070 Ti SUPER). 매니페스트·스크립트는 `k8s/k8s-tenancy/`.
수치는 직접 측정, 시험은 대부분 1회 관찰. 롤백: `teardown.sh`(서버 VM에서 실행, 네임스페이스·PV 삭제 + time-slicing 해제).

## 구성
- 테넌트 2개: 네임스페이스 `tenant-a`, `tenant-b`(`tenant-*.yaml`). 각각 `ResourceQuota`(`requests.nvidia.com/gpu: 1`, CPU 4/8, 메모리 8Gi/16Gi, 파드 10개), `LimitRange`(기본 요청·상한), `pod-security.kubernetes.io/enforce: baseline` 라벨.
- 네트워크: `NetworkPolicy` 2개씩(default-deny ingress + 같은 네임스페이스 허용)(`netpol-tenant-*.yaml`).
- 권한: `ServiceAccount tenant-admin` + `Role` + `RoleBinding`(`rbac-tenant-*.yaml`). 자기 네임스페이스의 워크로드는 다룰 수 있고, 쿼터·LimitRange·NetworkPolicy는 읽기만 가능.
- 저장소: 모델 캐시는 관리자가 만든 `local` PV(`Retain`, gpu-server 한정)를 테넌트가 PVC로 사용(`storage-*.yaml`). baseline PSA가 테넌트의 `hostPath`를 막기 때문.

## 시험 1: GPU 쿼터 (공식 문서: 확장 자원은 `requests.` 접두사만 쿼터 가능, 오버커밋이 없어서)
| 시도 | 결과 |
|---|---|
| tenant-a 파드, GPU 1개 | 생성, Running |
| tenant-a 두 번째 파드, GPU 1개 | `Forbidden: exceeded quota: quota, requested: requests.nvidia.com/gpu=1, used: ...=1, limited: ...=1` |
| tenant-a 파드, GPU 2개 | `Forbidden: exceeded quota` (requested 2) |
| tenant-b 파드, GPU 1개 (쿼터는 여유) | 생성됐지만 **Pending**: `1 Insufficient nvidia.com/gpu` |
- 해석: 쿼터는 "허용량"이고 실제 용량과 별개다. 쿼터 합(2)이 물리 GPU(1)를 넘어도 설정은 된다. 쿼터는 admission에서 거부하고, 용량 부족은 스케줄러에서 Pending.

## 시험 2: 네트워크 격리 (k3s 문서: kube-router 기반 네트워크 정책 컨트롤러 내장, 기본 활성)
tenant-b에 web 파드 2개(gpu-server, 서버 VM), tenant-a/tenant-b에 client 파드. 시험 스크립트 `nettest.sh`.
| 구분 | 정책 없음 | default-deny + 같은 네임스페이스 허용 |
|---|---|---|
| tenant-a → tenant-b web(같은 노드) | 허용 | **차단** |
| tenant-a → tenant-b web(다른 노드, VXLAN) | 허용 | **차단** |
| tenant-b → tenant-b web(같은 노드) | 허용 | 허용 |
| tenant-b → tenant-b web(다른 노드) | 허용 | 허용 |
- **차단 결과가 `timed out`이 아니라 `Connection refused`**였다. gpu-server의 iptables에 `REJECT --reject-with icmp-port-unreachable`(체인 `KUBE-POD-FW-*`)과 `DROP by policy` 로그 규칙이 있었다. 즉 **방화벽 REJECT도 `refused`를 만든다.** 홈랩 일지 7("refused는 패킷이 VM까지 도착했다는 뜻")의 해석은 단정할 수 없게 되었고, 같은 글의 결론은 원인 추적(`firewall_driver=noop`, conntrack 흐름 0개)에서 나온 것이다.
- 이번에는 ingress만 막았다. egress 차단은 하지 않았다.
- 첫 시도의 "적용 전/후" 결과는 스크립트 버그(`-n a`)로 무효였고, 정책을 지웠다가 다시 시험했다.

## 시험 3: RBAC (ServiceAccount 토큰으로 접속하는 임시 kubeconfig, `rbactest.sh`)
| 시도(tenant-a 관리자) | 결과 |
|---|---|
| tenant-a pods 조회, pod 생성 | 허용 |
| tenant-a 쿼터 조회 | 허용(읽기) |
| 쿼터 삭제, NetworkPolicy 삭제 | `Forbidden` |
| tenant-b pods 조회 | `Forbidden` |
| 노드 조회, 네임스페이스 생성, `kube-system` secrets 조회 | `Forbidden` |
- 의미: 테넌트가 자기 쿼터와 격리 정책을 스스로 풀 수 없다.

## 시험 4: Pod Security (RBAC의 구멍)
pod 생성 권한이 있으면 `hostPath` 파드로 노드 파일을 읽을 수 있다(`psatest.sh`, `/etc/hostname` 한 파일 읽기 전용).
- enforce 라벨 없음: 파드 생성됨, 로그에 `gpu-server`(호스트 이름) 출력.
- `enforce=baseline` 적용 후: `violates PodSecurity "baseline:latest": hostPath volumes`로 거부.
- 부작용: 지금까지의 vLLM 매니페스트(모델 캐시를 `hostPath`로 마운트)가 테넌트에서는 안 돌아간다 → 관리자 PV + 테넌트 PVC 방식으로 바꿨다.

## 시험 5: noisy neighbor (time-slicing `replicas: 4`, 각 테넌트 vLLM util 0.48)
각 테넌트 쿼터는 GPU 1개, 노드 할당 2/4. 부하는 각 vLLM 파드 안에서 localhost로(128토큰, ignore_eos). B=피해자(요청 96개, 동시 4), A=시끄러운 이웃(요청 1,200개, 동시 64). `noisy.sh`.
| 구성 | B 지연 p50 / p95 | B 처리량 | A |
|---|---|---|---|
| B 단독 (2회) | 1.44s / 1.44s | 354~355 tok/s | - |
| A 무거운 부하 중 B (2회) | 3.06~3.07s / 3.07~3.08s | 192~193 tok/s | 2,415~2,451 tok/s, p50 3.63~3.70s |
| 끝난 뒤 B 단독 | 1.44s / 1.44s | 355 tok/s | - |
- 이웃이 무거운 부하를 걸자 B의 지연이 약 2.1배, 처리량이 약 46% 감소. 쿼터는 둘 다 만족하고 있었다 → **쿼터는 GPU 개수를 제한할 뿐 GPU 시간을 보장하지 않는다.**
- 이웃의 부하(동시 64)와 이전 시험의 동일 부하 두 파드(동시 32씩) 모두에서 지연이 약 2.1배였다. 가설: 프로세스별 시간 분할이라 이웃의 강도가 아니라 이웃의 존재가 지연을 정한다(이웃 1개 구성만 시험, 3개 이상은 미시험).
- 이웃 부하 도중 B의 응답은 끝까지 성공했고, 서로의 서비스는 중단되지 않았다.

## 시험 6: MPS로 성능 격리를 시도 (2026-11-03)
- **정적 매니페스트로는 불가**: `sharing.mps` 설정을 ConfigMap으로 주자 device plugin이 `unable to validate flags: using MPS requires --mps-root to be specified`로 종료, 노드 allocatable이 0이 되어 즉시 원복했다.
- **공식 Helm 차트로 가능**: Helm v3.16.4(체크섬 확인)로 `helm template`(차트 v0.20.1)만 쓰고 렌더링 결과를 검토한 뒤 `kubectl apply`(`mps-helm-rendered.yaml`, 설정 `mps-config.yaml`, `replicas: 2`). 차트는 device plugin DaemonSet 외에 **MPS 컨트롤 데몬 DaemonSet**(`hostPID: true`, privileged, `/run/nvidia/mps` 호스트 마운트)을 배포한다. 이 데몬은 노드 라벨 `nvidia.com/mps.capable=true`가 있어야 뜨는데(원래 GFD가 붙임) 직접 붙였다. 적용 뒤 로그 `MPS daemon is healthy`, `nvidia.com/gpu` 2, 파드에 `CUDA_MPS_PIPE_DIRECTORY=/mps/nvidia.com/gpu/pipe`. 데몬의 기본 활성 스레드 비율 조회 결과 **50.0**(메모리 한도 조회는 빈 응답이라 확인하지 못함).
- **GeForce(RTX 4070 Ti SUPER)에서도 MPS가 동작했다**(두 vLLM 모두 기동, 합계 14,615MiB).
- noisy neighbor(`noisy.sh`와 같은 부하): B 단독 p50 1.45s, **A가 부하 거는 중 B p50 2.97~2.99s(약 2.05배), 처리량 351→223~225 tok/s(약 36% 감소)**, A는 3,291~3,343 tok/s(time-slicing의 2,415~2,451보다 높음).
- 이웃 강도별(`dose.sh`, 이웃 동시 4/16/64): B p50 1.45s → 2.54s / 2.60s / 3.07s.
- **해석**: MPS는 데몬의 기본 활성 스레드 비율이 50.0으로 조회됐는데도(클라이언트에 실제 적용됐는지와 GPU 활성률은 확인하지 못함) 피해자의 지연이 time-slicing(약 2.1배)과 비슷하게 늘었다. MPS(replica 2)와 time-slicing(replica 4)은 설정이 달라 직접 비교로 일반화하지는 않는다. 합계 처리량은 오히려 높았다(A 3,300 + B 225 vs time-slicing 2,430 + 193). 가설(미시험): vLLM decode는 메모리 대역폭이 병목(NVIDIA 블로그의 "memory-bound")이라, SM을 나눠도 메모리 대역폭을 공유하는 한 지연은 줄지 않는다.
- **롤백**: 렌더링한 리소스 삭제, 노드 라벨 제거, 정적 device plugin 복구. **호스트에 `/run/nvidia/mps/shm` tmpfs 마운트가 남았다**(`sudo umount /run/nvidia/mps/shm; sudo rm -rf /run/nvidia/mps`로 정리, `findmnt | grep nvidia/mps`로 확인). 서버 VM에 Helm과 소스 폴더(`~/helm-dl`, `~/nvdp-src`)가 남아 있다.

## 시험 7: 이웃의 부하를 줄이면 피해자 지연이 돌아오나 (이웃 강도별, `dose.sh`, `dose1.sh`)
피해자 B(요청 48개, 동시 4)의 지연 p50. 이웃 A는 시험 중 계속 부하를 거는 상태.
| 이웃 A의 동시 요청 | time-slicing | MPS |
|---|---|---|
| 없음 (B 단독) | 1.44s | 1.45s |
| 1 (약 74 tok/s) | **3.05s** | 미시험 |
| 4 | **3.07s** | 2.54s |
| 16 | **3.10s** | 2.60s |
| 64 | **3.06s** | 3.07s |
- **time-slicing에서는 이웃의 강도와 무관**: 동시 1개(약 74 tok/s)짜리 이웃만 있어도 지연이 약 2.1배, 그 이상 세게 몰아도 더 늘지 않았다. 지연은 이웃이 얼마나 쓰는지가 아니라 이웃이 **있는지**로 정해졌다(이웃 1개 구성). 따라서 이웃의 요청 속도를 제한하는 방식은 이웃을 거의 멈추지 않는 한 피해자를 지켜 주지 못한다.
- 정정과 보강(Codex 토론, 2026-11-03): ① 동시 1개 이웃 시험의 피해자 요청 수는 **32개**이고 다른 강도(4/16/64)는 48개다(`dose1.sh`). p50은 3.05s로 같지만 표본이 같지 않다. ② 피해자 단독 기준선은 이웃 엔진이 **떠 있되 유휴**인 상태에서 측정했다(`dose.sh`의 첫 단계, 결과 파일에는 상태가 기록돼 있지 않음). ③ 이웃의 tok/s는 자원 사용량의 척도가 아니다: 배치 1 decode는 토큰마다 가중치(약 6.2GB)를 읽으므로 단독 91 tok/s는 약 565GB/s로 추정되고, 이는 이론 대역폭 672GB/s의 약 84%다(가정이 많은 추정, 대역폭은 직접 재지 않음). 같은 식으로 동시 64(약 2,900 tok/s)는 약 280GB/s다. 즉 "동시 1 이웃이 가볍다"는 전제가 틀릴 수 있고, "활성 이웃이 있으면 번갈아 실행된다"와 "메모리 대역폭을 공유해서 각자 절반"이라는 두 설명을 지금 데이터로는 구분하지 못한다. ④ NVML `utilization.memory`는 모든 동시성(1/4/16/64)에서 94~99%였지만 이는 메모리가 바쁜 시간 비율이라 포화를 뜻하지 않는다(`results/bwprobe.txt`).
- MPS는 가벼운 이웃에서는 약간 낫고(+75%) 이웃이 세지면 time-slicing과 비슷해졌다(+112%).
- 한계: 각 구성 1회, 이웃 1개, 이웃 동시 0~1 사이는 미시험.

## 시험 8: 공유 엔진 + 우선순위 스케줄링 (서빙 단계 제어, `prio.sh`)
테넌트마다 vLLM을 따로 두는 대신 **vLLM 하나를 두 테넌트가 공유**(`--max-num-seqs 16`, 다음 두 모드만 다름). A는 무거운 부하(요청 800개, 동시 64), B는 가벼운 부하(요청 48개, 동시 4). `--scheduling-policy`는 vLLM 문서상 `fcfs`(도착 순서) 또는 `priority`(요청의 `priority` 값이 낮을수록 먼저).
| 모드 | B 지연 p50 / p95 | B 처리량 | A 처리량 |
|---|---|---|---|
| B 단독(기준선) | 1.44s / 1.61s | 352 tok/s | - |
| `fcfs`, A 부하 중 | **5.88s** / 6.97s (약 4.1배) | 88 tok/s | 1,311 tok/s |
| `priority`(A 10, B 0), A 부하 중 | **2.51s** / 2.94s (약 1.7배) | 235 tok/s | 1,308 tok/s |
- 같은 엔진에서 우선순위를 주자 B의 지연이 5.88s에서 2.51s로 약 57% 줄었고 A의 처리량은 같았다(1,311 → 1,308 tok/s).
- 기준선(1.44s)까지는 돌아오지 않았다. 이미 실행 중인 시퀀스를 중간에 끊지는 않고, 배치가 커지면 한 스텝이 길어지는 것이 이유일 것으로 추정한다(미시험).
- **주의**: `--max-num-seqs 16`으로 슬롯을 제한해 A의 요청이 대기열에 쌓이게 만든 설정이라, A 처리량(약 1,300 tok/s)이 시험 5(약 2,400 tok/s)와 직접 비교되지는 않는다. 슬롯이 넉넉해서 대기열이 없는 상태에서의 효과는 시험하지 않았다.
- 이 결과는 `--max-num-seqs 16`으로 대기열이 생기고 A를 먼저 시작해 8초 뒤 B를 보낸 조건의 효과다. 다른 슬롯 수, 도착 순서, 반복, 테넌트별 p99·기아 여부는 시험하지 않았다. 우선순위는 **한 엔진 안의 요청 사이에서만** 작동한다. 테넌트마다 엔진(프로세스)이 따로면 time-slicing 규칙을 따른다. 대신 이 방식은 테넌트 간 데이터·설정 격리가 약하다(같은 프로세스, 같은 KV 캐시 공간)는 점은 다루지 않았다.

## 시험 9: 공유 엔진의 프리픽스 캐시 격리 (`cachesalt.py`)
공유 vLLM에서 테넌트 B가 A와 같은 프롬프트(약 4,232토큰, `max_tokens=1`)를 보내면 salt가 없을 때 p50 25ms, 새 프롬프트는 340ms였고(5회, 약 13.6배), 서로 다른 `cache_salt`를 쓰면 337ms로 캐시 없음과 같았다. 지연만으로 "누가 최근에 이 프롬프트를 보냈다"를 알 수 있다는 신호가 있고 salt가 이를 막는다(실제 공격 재현은 아님). 자세한 해석은 `research-multitenancy.md` 7-3.

## 시험 10, 11: time-slice 길이, sleep mode 시간 분할 독점 (2026-11-03, Docker, vLLM 0.32.0)
`nvidia-smi compute-policy --set-timeslice` 0~3은 지연에 영향이 없었고(이웃 있을 때 B p50 3.05~3.06s), vLLM sleep mode로 한 번에 한 테넌트만 깨우면 이 구성(3B, 요청 48개·동시 4, 전환 몇 차례)에서 지연이 단독 기준선과 같았다(1.43s, 전환 약 0.97s). SLA 보장이 아니며 진행 중 요청·큰 모델·K8s는 미시험이다. Codex 검토 반영 내용은 리서치 문서 9절. 자세한 표와 한계는 `research-multitenancy.md` 8절, 원본은 `results/timeslice_length.txt`, `results/sleepmux.txt`.

## 시험 12: 합성 이웃 실험 (copy / gemm / idle, 2026-11-03, Docker, vLLM 0.32.0)
기본(시간 분할) 모드에서 이웃이 연산만 쓰는 행렬 곱이어도 피해자 지연이 2.24배(대역폭만 쓰는 복사 이웃은 2.08배), 유휴 이웃은 영향 없음(1.01배), 이웃 가동률 25%에서 1.20~1.39배였다. 간섭은 이웃의 GPU 바쁜 시간 비율에 비례해 보인다. MPS 모드는 요청 정지(p95 약 100초)와 피해자 vLLM의 GPU 오류(Xid 31)가 발생해 비교가 성립하지 않았다(원인 불명). 표와 해석, 한계는 `research-multitenancy.md` 12절, 원본은 `results/synth_neighbor_*.txt`, `results/mps_fault_evidence.txt`.

## 시험 13: HAMi-core(컨테이너별 GPU 한도) 시험 (2026-11-03, Docker, vLLM 0.32.0)
GeForce와 CUDA 13에서 로드되고 메모리 한도(4g에서 3.75GiB 이후 OOM)와 SM 한도(행렬 곱 25% → 약 23% 처리량)가 걸렸다. 이웃에 컴퓨트 한도를 걸면 피해자 보호는 비선형이었고(행렬 곱 이웃 50%는 개선 없음, 25% 1.31배, 10% 1.13배), **실제 vLLM 이웃은 CUDA graph(기본)에서 한도가 전혀 적용되지 않았고**(소스: `cuGraphLaunch`는 속도 제한 없이 통과) `--enforce-eager`에서만 걸렸다(이웃 25% → 피해자 1.41~1.55배, 10% → 1.28배, 한도 없음 2.18배). 표와 한계는 `research-multitenancy.md` 13절, 원본은 `results/hami_*.txt`.

## 시행착오
- 두 vLLM을 거의 동시에 올리면 둘 다 `No available memory for the cache blocks`로 실패한다(서로의 메모리 사용을 보며 프로파일링). 한쪽이 준비된 뒤 다른 쪽을 올려야 한다. 매니페스트에 `startupProbe`를 넣어 `1/1`이 준비 완료를 뜻하게 했다.
- 대기 루프가 `Terminating` 중인 이전 파드를 보고 통과해서 B를 일찍 올린 실수가 있었다(`grep -v Terminating`으로 고침).

## 한계
- 테넌트 2개, 각 시험 1~2회. GPU 1장.
- 격리 3축 중 데이터(저장소)·모델 측면은 다루지 않았다. egress 차단, 서비스 인증, 메모리 격리·장애 전파(time-slicing)는 미시험.
- 테넌트 수는 2개까지만(이웃 1개). 3개 이상은 미시험. `PriorityClass`(K8s 스케줄러 우선순위)는 다루지 않았다(시험 8의 우선순위는 vLLM 엔진 내부 스케줄링).
- 테넌트 온보딩 자동화(Go)는 아직 없다(Step 4 마지막 항목).

## 현재 상태
테넌트 네임스페이스 `tenant-a`, `tenant-b`(쿼터, 정책, RBAC)는 남아 있고 워크로드·PV는 없다. time-slicing과 MPS는 꺼서 `nvidia.com/gpu`는 1, 호스트 MPS 마운트 없음. 전체 제거는 `teardown.sh`(MPS를 다시 쓰면 위 롤백 절 참고). 원본 출력은 `k8s/k8s-tenancy/results/`.
