# GPU 인프라 학습 계획표 (원격 순차 실습)

작성: 2026-10-30 (체크 표시는 작성 이후 갱신한 항목만 반영, 마지막 갱신 2026-11-03). 목표 범위: GPUaaS/LLMaaS 플랫폼의 가상화 레이어·K8s 오케스트레이션·멀티테넌시·옵저버빌리티·self-healing·빌링.
**이 문서는 "무엇을 어떤 순서로 하는가"를 정리한 원래 실행표입니다.** 실제 수행 기록과 측정값은 각 단계 문서(`step-*.md`)에 있습니다. Step 5 이후는 아직 계획 단계입니다.

한 줄 목표: **GPU 한 장을 여러 테넌트에게 나눠 팔고(멀티테넌시), 보이게 하고(관측성), 고장 나도 살리고(self-healing), 쓴 만큼 계산하는(과금) 작은 GPUaaS를 직접 만든다.** 그 과정의 측정값·설계 문서·코드가 포트폴리오입니다.

---

## 0. 지금 원격으로 쓸 수 있는 환경

| 장비 | 상태 | 이 계획에서의 역할 |
|---|---|---|
| **gpu-server** (`gpu-server`) | Ubuntu 24.04, RTX 4070 Ti SUPER 16GB, 드라이버 580 확인함, RAM 30GB, `/data` 350GB(비어 있음), Docker 없음 | GPU 서빙·K8s GPU 노드. **Windows로 부팅하면 원격이 끊기므로 계속 Ubuntu 유지** |
| **homeserver** (`openstack-host`) | OpenStack, RAM 31GB 중 약 16GB 여유, VM은 bastion뿐 | K8s 컨트롤 플레인 VM, 관측성 보조, KVM 이해 |
| **Mac mini** (`macmini`) | 32GB, Docker(colima)·kubectl·helm 있음 | Prometheus/Grafana 허브, 과금 서비스 개발, kubectl 원격 제어 |

원격으로 못 하는 것: BIOS 조작, Windows 부팅 상태의 작업(gpu-server를 Ubuntu 밖으로 보내는 일은 하지 않음), gated 모델용 HF 토큰 발급(처음엔 토큰이 필요 없는 모델만 사용).

## 1. 진행 방식 (직접 실습 + 이해 우선)

**원칙: 모든 과정은 내가 직접 실행하고 이해할 수 있어야 한다. Claude가 대신 해 버리는 것은 "시연"이지 학습이 아니다.**

각 단계(와 그 안의 작은 실습)는 아래 틀로 진행합니다.

1. **왜 하나 (개념):** 이 단계가 풀려는 문제와 핵심 개념을 짧게 설명한다. 실무의 어떤 문제와 연결되는지도 말한다.
2. **직접 실행:** 내가 gpu-server에서 칠 **명령어를 한 번에 하나씩** 받는다. 명령마다 "이 명령이 무엇을 하는가, 옵션이 무슨 뜻인가"를 붙인다. 실행은 `ssh gpu-server`로 접속해서 내가 직접 한다. (Claude Code에서는 `! ssh gpu-server '<명령>'`로도 가능)
3. **예상 결과:** 성공하면 이렇게 보인다는 출력 예시를 준다. 다르게 나오면 어디를 보면 되는지 알려 준다.
4. **확인 질문:** 이해했는지 스스로 점검하는 질문 2~3개. (예: "왜 이미지가 `/data`가 아니라 `/var/lib/containerd`에 쌓였을까?") 답을 말해 보고 틀린 부분은 고쳐 준다.
5. **일부러 망가뜨려 보기:** 가능한 단계에서는 한 가지를 일부러 틀리게 해서 오류 메시지를 읽는 연습을 한다.
6. **되돌리기:** 바꾼 것을 원래대로 돌리는 명령을 같이 적는다.
7. **기록:** 내 말로 정리한 요약을 `docs/step-N-*.md`에 남긴다. (명령, 출력, 배운 점, 막힌 곳, 내가 한 답변)

Claude가 대신 실행하는 것은 ① 내가 막혔을 때의 진단 ② 읽기 전용 점검 ③ 반복 작업(내가 이미 한 번 직접 해 본 것) 정도로 제한합니다. 새 설정·설치는 **내가 직접 입력하는 것을 기본**으로 합니다.

- **세션** = 저녁 1~2시간 단위. 한 단계는 보통 1~4세션.
- 각 단계 끝의 **완료 기준**을 내 힘으로 설명할 수 있어야 다음으로 넘어간다.
- 설정을 바꾸기 전에는 백업, 되돌리는 방법 기록.
- 결과물 위치: `docs/step-N-*.md`, 코드는 `bench/`, `scripts/`, `k8s/`, `harness/`.

---

## 2. 단계별 계획

### Step 0. 기반 도구 (0.5세션)
**목표:** gpu-server에서 컨테이너로 GPU를 쓸 수 있는 상태.
- **상태:** 2026-10-30 완료(체크박스는 갱신하지 않음). 기록은 `docs/step-0-tooling.md`.
- [ ] Docker 설치, NVIDIA Container Toolkit 설치 및 `--gpus all` 확인
- [ ] 디렉터리 정리: `/data/models`, `/data/containers`(Docker data-root), `/data/lab`
- [ ] 보조 도구: `tmux`, `nvtop`, `htop`, Python venv, `uv`(또는 pip)
- **완료 기준:** `docker run --rm --gpus all nvidia/cuda:… nvidia-smi`가 RTX 4070 Ti SUPER를 출력, Docker 이미지가 `/data`에 쌓임
- **배울 것:** 컨테이너가 GPU를 보는 원리(드라이버 ↔ Container Toolkit ↔ 런타임)

### Step 1. 단일 GPU 모델 서빙 (2~3세션)
**목표:** vLLM으로 LLM을 서빙하고 성능을 숫자로 말할 수 있게 된다.
- [ ] Ollama로 빠르게 첫 응답 확인(워밍업, 선택)
- [x] vLLM 컨테이너로 7~8B급 양자화 모델 서빙(토큰 불필요한 모델, 예: Qwen 계열 AWQ — **모델은 세션에서 확정**)
- [x] 부하 테스트 스크립트(Python): 동시 요청 1·4·16·64
- [x] 측정: **tokens/s, TTFT, 지연 분포(p50/p95), VRAM 사용량, GPU 사용률** (VRAM·GPU 사용률은 `docs/step-1d-summary-table.md`)
- [x] 변수 실험: 모델 크기/양자화(FP16 vs AWQ), `max-model-len`, `gpu-memory-utilization` (완료: 2026-11-02, Ollama 워밍업은 선택이라 생략)
- **완료 기준:** 모델·설정별 VRAM과 처리량 표 1장(`docs/step-1d-summary-table.md`), "이 GPU 한 장에 동시에 몇 명을 받을 수 있나"에 숫자로 답할 수 있음 — **충족(2026-11-02)**
- **배울 것:** KV cache, continuous batching, PagedAttention, 처리량과 지연의 트레이드오프

### Step 2. GPU 관측성 1차 — 베어메탈 (1~2세션)
**목표:** GPU 지표가 Grafana에서 보인다. (내 강점인 관측성을 K8s보다 먼저 확보)
- [x] `dcgm-exporter` 컨테이너로 GPU 지표 노출(사용률, VRAM, 온도, 전력, 클럭)
- [x] vLLM `/metrics`(요청 수, 대기열, KV cache 사용률) 수집
- [~] Prometheus + Grafana, gpu-server를 스크레이프 — **계획과 다르게 gpu-server 안(Docker, 127.0.0.1)에 구성**(Mac mini 보호). 옮기는 방법은 `docs/step-2-observability.md` 17절
- [x] 대시보드 1장: GPU 사용률·VRAM·온도·전력 + 서빙 지표 (11개 패널)
- [x] 알람 규칙 초안: "GPU 사용률 0%인데 VRAM 점유"(좀비 점유), 온도·전력 이상 (8개 규칙, 정상 대기 중 vLLM 오탐 방지 시험 통과; Alertmanager 알림 발송은 미연결)
- **완료 기준:** 부하 테스트 중 대시보드의 그래프와 Step 1의 측정값이 일치 — **평균·VRAM·KV는 일치, 순간 최대값(전력·running)은 15초 스크레이프 때문에 낮게 나옴(2026-11-02)**
- **배울 것:** DCGM 지표 의미, "값 0"과 "데이터 없음" 구분(기존 모니터링 설계를 재사용)

### Step 3. K8s GPU 노드 (3~4세션)
**목표:** GPU를 K8s 자원으로 요청해서 쓰고, 한 GPU를 여러 파드가 공유한다.
- **결정 필요(세션에서):** 컨트롤 플레인 위치
  - **A. gpu-server 단일 노드 k3s (추천 시작점):** 가장 빠르고 전부 원격. 이후 B로 확장
  - **B. homeserver VM 컨트롤 플레인 + gpu-server 워커:** 장애 도메인이 분리돼 failover 실습에 좋음. VM이 gpu-server와 통신하려면 LAN에서 닿는 주소(Floating IP 등)가 필요 — **네트워크 설계 확인 후 진행**
- [x] k3s 설치(B안: 서버 VM + gpu-server 워커), NVIDIA device plugin v0.20.1, `nvidia.com/gpu` 노출 확인 (2026-11-03, GPU Operator는 쓰지 않음)
- [x] vLLM을 Deployment로 올려 `nvidia.com/gpu: 1` 요청, Service 노출 (2026-11-03)
- [x] **time-slicing** 설정(`replicas: 4`)으로 vLLM 파드 2개 공유, 공유 전후 처리량·지연 비교 (합계 처리량 거의 같고 지연 약 2.1배, 각 1회 측정)
- [~] startupProbe(모델 로딩 시간 반영)는 사용, 롤링 업데이트와 롤백은 미시험
- [x] Mac mini에서 kubeconfig로 원격 제어 (2026-11-03)
- **완료 기준:** 공유 전/후 처리량 비교표, "time-slicing은 격리가 아니다"를 실험으로 설명할 수 있음
- **배울 것:** device plugin 구조, 스케줄러가 GPU를 보는 방식, time-slicing·MIG·vGPU·passthrough 비교(4070 Ti SUPER는 MIG 미지원 — 제 지식 기준, 확인 필요)

### Step 4. 멀티테넌시 (2~3세션)
**목표:** 테넌트별로 GPU 쿼터와 격리를 건다.
- [x] 테넌트별 Namespace(`tenant-a`, `tenant-b`), `ResourceQuota`(`requests.nvidia.com/gpu`, CPU, 메모리), `LimitRange` (2026-11-03)
- [~] `NetworkPolicy`(ingress 차단)와 RBAC, Pod Security(baseline)는 완료, `PriorityClass`·egress 차단은 미시험
- [x] **noisy neighbor 실험:** 이웃이 무거운 부하를 걸자 피해자의 지연 약 2.1배, 처리량 약 46% 감소 (`docs/step-4-multitenancy.md`)
- [ ] 격리 수준 비교(Namespace vs vCluster 등, 조사 후 하나 선택) — 이번에는 Namespace만 사용
- [ ] 테넌트 온보딩 스크립트(Go): 네임스페이스·쿼터·네트워크 정책·권한을 한 번에 생성 (매니페스트는 `k8s/k8s-tenancy/`에 있음, 자동화는 아직)
- **완료 기준:** 쿼터 초과 요청이 거부되고, 테넌트 간 통신이 막히는 테스트가 자동으로 통과
- **배울 것:** 격리 3축(네트워크·데이터·모델), 보안 프레임워크 관점

### Step 5. 사용량 과금 + GPUaaS API (4~5세션, 차별화)
**목표:** 쓴 만큼 계산하는 Go 서비스와 간단한 API.
- [ ] 사용량 수집: DCGM(K8s 모드)의 파드·네임스페이스 라벨 또는 `nvidia.com/gpu` 점유 시간으로 **테넌트별 GPU-seconds** 집계
- [ ] 요금표(GPU-hour 단가, 최소 과금 단위) 정의, 청구서 계산
- [ ] **정확성:** 중복 집계 방지(idempotent), 늦게 도착한 데이터 재집계(backfill), 수집 공백 구간 복구, 시계 오차
- [ ] 이상 감지: 사용량 급증·0 사용인데 점유 등(임계값 또는 z-score)
- [ ] REST API: GPU 워크로드 생성/삭제, 쿼터 조회, 사용량·청구 조회. 저장소는 SQLite→Postgres
- [ ] 테스트: 집계 정확성 단위 테스트, 장애 후 재처리 테스트
- **완료 기준:** 하루치 사용량을 재집계해도 같은 금액이 나오고, 일부러 만든 누락 구간이 복구됨
- **배울 것:** 결제·빌링 시스템의 트랜잭션 무결성, 기존 집계·재처리·lease/fencing 경험 재사용
- **포트폴리오 핵심:** 이 단계의 레포와 설계 문서

### Step 6. 장애·복구 (1~2세션)
**목표:** 고장 나는 장면과 복구 시간을 측정한다.
- [ ] 파드 강제 삭제, 노드 drain, 모델 로딩 중 장애, 잘못된 배포 롤백
- [ ] `PodDisruptionBudget`, 레플리카, 프로브 튜닝으로 서빙 중단 시간 줄이기
- [ ] gpu-server 재부팅 후 서비스 자동 복구(원격 재부팅, Ubuntu로만)
- [ ] 복구 시간 표: 장애 종류별 감지→복구 소요
- **한계(솔직히):** GPU 노드가 한 대뿐이라 "다른 GPU 노드로 failover"는 실제로 못 합니다. 대신 컨트롤 플레인 장애·파드 레벨 복구를 실험하고, 다중 GPU 노드 설계는 문서로 정리합니다.
- **완료 기준:** 시나리오별 복구 시간 표와 개선 전/후 비교

### Step 7. 가상화(KVM/OpenStack) 정리 (1~2세션, 보조)
**목표:** 이미 한 passthrough 작업을 정리하고 개념을 보강한다.
- [ ] 한 작업 문서화: IOMMU, vfio 바인딩, Nova `[pci]`, flavor, 실패 원인 추적(드라이버·머신 타입·초기화 방식을 하나씩 배제한 과정)
- [ ] KVM/libvirt 기본(`virsh`, QEMU 옵션, hostdev), passthrough vs vGPU vs MIG 비교 문서
- [ ] (직접 방문 시) homeserver BIOS 기본 디스플레이 확인 → 성공하면 GTX 660 시험 재개
- **완료 기준:** 절차서 + 트러블슈팅 기록 1편

### Step 8. 정리·포트폴리오 (2세션)
- [ ] 아키텍처 문서(그림 포함), 측정값 모음, 결정 기록(왜 이 선택을 했나)
- [ ] GitHub 레포 정리(README, 재현 방법), 블로그 글 1~2편(이미 있는 `/blog` 활용)

---

## 3. 전체 일정 (대략)

| 단계 | 세션 | 누적 | 비고 |
|---|---|---|---|
| 0. 기반 도구 | 0.5 | 0.5 | **바로 시작 가능** |
| 1. 단일 GPU 서빙 | 2~3 | ~3 | **바로 시작 가능** |
| 2. GPU 관측성 1차 | 1~2 | ~5 | 내 강점 |
| 3. K8s GPU 노드 | 3~4 | ~9 | 컨트롤 플레인 위치 결정 필요 |
| 4. 멀티테넌시 | 2~3 | ~12 | |
| 5. 과금·GPUaaS API | 4~5 | ~17 | 차별화 포인트 |
| 6. 장애·복구 | 1~2 | ~19 | |
| 7. 가상화 정리 | 1~2 | ~21 | 단계 2 이후 언제든 병행 가능 |
| 8. 포트폴리오 | 2 | ~23 | |

세션당 1.5시간이면 약 35시간입니다. 주 3~4회 진행하면 6~8주 규모이고, 단계 5까지가 핵심입니다. 시간이 부족하면 **0 → 1 → 2 → 3 → 5**만으로도 "GPU 서빙 + 관측성 + 과금"이라는 포트폴리오가 성립합니다.

## 4. 시작 전에 정할 것

1. **모델:** Step 1에서 쓸 모델(토큰이 필요 없는 7~8B급, 16GB VRAM 기준). 세션에서 후보를 보고 고름.
2. **컨트롤 플레인 위치:** Step 3에서 A(gpu-server 단일 노드) 또는 B(homeserver VM). 추천은 A로 시작, 필요하면 B로 확장.
3. **관측성 스택 위치:** Mac mini에 Prometheus/Grafana(추천). 상시 켜져 있고 메모리 여유가 큼.
4. **포트폴리오 형식:** GitHub 레포 + 문서 중심으로 갈지, 블로그 글 중심으로 갈지.

## 5. 지켜야 할 규칙

- gpu-server는 **Ubuntu로만** 운영합니다(Windows로 부팅하면 원격이 끊김).
- 모델·이미지 파일은 `/data`에 둡니다(루트 150GB 보호).
- 설정을 바꾸기 전 백업, 되돌리는 방법 기록. homeserver 재부팅은 vm-bastion이 잠시 내려가므로 사전에 알립니다.
- 측정값은 "측정함"과 "추정"을 구분해서 적습니다.

## 6. 환경 변화로 폐기·변경된 가정 (기존 계획서 대비)

- 폐기: 기존 `k8s-master-01`/워커 2대 재활용안 → VM k8s 삭제로 새로 구축.
- 변경: gpu-server는 Windows가 아니라 **Ubuntu 듀얼부팅 완료**, 드라이버 설치 완료(단계 0의 OS 부분 해결).
- 변경: homeserver 메모리 여유 증가(VM 삭제).
- 변경: OpenStack GPU passthrough **성공**(2026-10-31). 게스트 드라이버 초기화 실패의 원인은 하이퍼바이저 표지였고 flavor 속성 `hide_hypervisor_id=true`로 해결. Step 7은 이 과정을 문서로 정리하는 단계가 됨.
