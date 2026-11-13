**한국어** | [English](README.en.md)

# gpu-serving-lab

**소비자용 GPU 한 장(RTX 4070 Ti SUPER, 16 GB)으로 작은 LLM 서빙 플랫폼**을 단계별로 직접 만들어 보면서 남긴 실습 노트, 스크립트, 원본 측정 데이터입니다.

**단일 GPU 서빙 → 부하 테스트 → 최적화 → 관측 → Kubernetes GPU 노드 → 멀티테넌시**

이 저장소의 모든 수치는 아래에 설명한 하드웨어에서 측정한 값입니다. 대부분의 실험은 **GPU 한 장**에서 **한 번만** 실행했으며, 단계별 노트에는 중요한 곳마다 이를 적었고 *측정한 것*, *추정한 것*, *검증하지 못한 것*을 구분해 두었습니다.

> 단계별 노트는 한국어로 작성되어 있습니다(제 작업용 랩 노트입니다). 이 문서는 영어 README를 전체 번역한 한국어 버전이며, 영어 버전은 [README.en.md](README.en.md)입니다.

---

## 왜 만들었나

GPUaaS / LLMaaS 플랫폼은 몇 가지 어려운 질문에 답해야 합니다. GPU 한 장으로 어느 정도의 지연 시간에서 몇 명의 사용자를 처리할 수 있는지, 포화되었는지 알려면 무엇을 봐야 하는지, GPU가 어떻게 스케줄링 가능한 Kubernetes 리소스가 되는지, GPU를 공유하는 테넌트끼리 실제로 격리되는지입니다. 벤더의 주장이 아니라 실제 하드웨어에서 측정한 답을 얻고 싶었기 때문에, 각 단계는 표와 아직 모르는 것의 목록으로 끝납니다.

## 환경 (홈랩)

| 역할 | 하드웨어 / 소프트웨어 (노트에 기록된 대로) |
|---|---|
| GPU 서버 | Ubuntu 24.04, **RTX 4070 Ti SUPER 16 GB** (GeForce: MIG, vGPU 없음), 12 코어 / 30 GB RAM, NVIDIA 드라이버 580, Docker + NVIDIA Container Toolkit 1.20.1 |
| 서빙 | vLLM `vllm/vllm-openai` 0.32.0 (Kubernetes 안에서는 0.33.0), OpenAI 호환 API |
| 모델 | Qwen2.5-3B-Instruct (BF16), Qwen2.5-7B-Instruct AWQ / GPTQ-Int4, Qwen3.5-4B, Qwen3.5-9B AWQ 4-bit |
| 관측 | dcgm-exporter 4.6.1-4.8.4, Prometheus v3.15.1, Grafana 13.3.0 (Docker, 127.0.0.1에 바인딩) |
| Kubernetes | k3s v1.36.5+k3s1: 컨트롤 플레인은 별도 홈 서버의 OpenStack VM에 두고, GPU 서버를 agent로 조인함. NVIDIA k8s-device-plugin v0.20.1 (GPU Operator 미사용) |
| 기타 | Mac mini M4 32 GB (kubectl 클라이언트, `harness/`의 MLX 비교용) |

호스트 이름과 사설 주소는 일반화했습니다(`gpu-server`, `192.168.1.x`, `10.0.0.x`).

## 결과 한눈에 보기

모든 값은 단계별 노트에서 그대로 가져왔습니다. 조건과 주의 사항은 링크를 따라가서 확인하세요.

**1. 단일 GPU 서빙과 포화** ([step-1b](docs/step-1b-load-testing.md)), Qwen2.5-3B, 동일한 128 토큰 요청 (이상화된 조건, prefix cache 적중):

| 동시 요청 수 | 처리량 (tok/s) | 사용자당 tok/s | TTFT p95 |
|---|---|---|---|
| 1 | 91.4 | 91.4 | 15 ms |
| 64 | 4,853 | 76.6 | 88 ms |
| 256 | 10,506 | 41.7 | 268 ms |
| 512 | 10,677 | 21.0 | 3,202 ms (실행 중 256 + 대기 256) |

**2. 현실적인 부하** (포아송 도착, 프롬프트/답변 길이 혼합, `bench/bench_real.py`): 처리량은 약 **1,300 tok/s**에서 포화되어 이상화된 벤치마크보다 **약 8배 낮고**, 한계는 **KV cache(99.6 %)**가 됩니다. SLO(TTFT p95 ≤ 300 ms, 사용자당 ≥ 30 tok/s)는 대략 **5–10 req/s**까지 유지됩니다.

**3. 4 req/s에서의 모델 / 설정 비교** ([step-1d](docs/step-1d-summary-table.md), [step-1c](docs/step-1c-optimization.md)):

| 설정 | KV 슬롯 (토큰) | 처리량 tok/s | TTFT p95 | KV 최대 사용률 | 정확도 (객관식 1,257문항) | SLO를 통과하는 도착률 |
|---|---|---|---|---|---|---|
| Qwen2.5-3B fp16 KV | 177,120 | 410 | 308 ms | 3.2 % | 54.5 % | ~8 req/s |
| Qwen2.5-3B fp8 KV | 332,640 | 411 | 279 ms | 2.4 % | ~45 %* | ~8 req/s |
| Qwen2.5-7B GPTQ | 115,296 | 427 | 831 ms | 11.4 % | 67.0 % | ~2 req/s (경계선) |
| Qwen2.5-7B AWQ | 115,184 | 426 | 838 ms | 10.1 % | 64.4 % | ~2 req/s |
| Qwen3.5-4B bf16 | 56,971 | 381 | 505 ms | 50.0 % | 69.2 % | ~2 req/s |
| Qwen3.5-9B AWQ 4-bit | 56,226 | 401 | 982 ms | 77.3 % | 75.5 % | 2 req/s에서 실패 (336 ms) |
| Qwen3.5-9B FP8 | – | – | – | – | – | 16 GB에서는 시작되지 않음 |

\* fp8 KV 정확도는 200문항 세트 기준입니다. 확인한 점: `nvidia-smi`의 GPU 사용률은 모든 설정에서 100 %로 읽혀서 용량 지표로는 쓸모가 없었고, 실제 신호는 KV 사용률, 큐 길이, TTFT였습니다. 보정하지 않은 fp8 KV는 정확도를 8.5 pp(3B) 떨어뜨렸지만 SLO 범위 안에서는 얻은 이점이 없었습니다. 1,200 단어짜리 공통 시스템 프롬프트에 prefix caching을 적용하자 4 req/s에서 TTFT p50이 11.1 s에서 128 ms로 줄었습니다.

**4. 관측** ([step-2](docs/step-2-observability.md)): Grafana 대시보드(패널 11개)와 Prometheus 알림 규칙 8개를 만들었고, 여기에는 "GPU가 유휴인데 VRAM을 점유 중" 규칙도 있습니다. 이 규칙은 모델이 올라가 있지만 유휴인 vLLM에서는 울리지 않도록 설계했습니다. 대시보드의 평균값, VRAM, KV 사용률은 Step-1 측정값과 일치했습니다. 짧은 피크(전력, 실행 중 요청 수)는 15 s 스크레이프 간격에서는 과소 집계됩니다.

**5. Kubernetes GPU 노드** ([step-3](docs/step-3-k8s-gpu-node.md)):
- device plugin을 통해 GPU를 `nvidia.com/gpu`로 노출했습니다. `runtimeClassName: nvidia`가 없는 파드는 `Failed to infer device type` 오류로 죽습니다.
- GeForce 카드에서 time-slicing(`replicas: 4`): vLLM 파드 두 개를 각각 단독으로 돌리면 ~2,400 tok/s, p50 1.60 s였고, **둘을 함께 돌리면 1,157 / 1,154 tok/s, p50 3.40 s**였습니다. 총 처리량은 그대로이고 각 파드의 지연은 두 배가 됩니다. time-slicing은 GPU를 공유하게 해 줄 뿐 테넌트를 격리하지는 않습니다.
- 컨트롤 플레인 VM 장애: 실행 중인 파드는 계속 서빙하지만, 재시작된 컨테이너는 CoreDNS가 컨트롤 플레인 VM에만 있었기 때문에 5분 넘게 시작하지 못했습니다. `internalTrafficPolicy: Local`로 CoreDNS 레플리카를 두 개 실행하자 90 s 장애 동안에도 DNS 조회 93/93건이 성공했고, vLLM은 약 55 s 만에 복구되었습니다.

**6. 멀티테넌시** ([step-4](docs/step-4-multitenancy.md), [research](docs/research-multitenancy.md)):
- Namespace + ResourceQuota(`requests.nvidia.com/gpu`) + NetworkPolicy + RBAC + Pod Security `baseline`: 쿼터 초과는 admission 단계에서 거부되고, 테넌트 간 트래픽은 차단되며, 테넌트가 자기 제한을 스스로 풀 수 없고, `hostPath` 탈출은 거부됩니다.
- Noisy neighbor: time-slicing에서는 두 테넌트 모두 쿼터 안에 있었는데도 피해자의 p50이 **1.44 s → 3.06 s (≈2.1배)**로 늘었고 처리량은 ~46 % 떨어졌습니다. 이웃의 부하를 동시 요청 1에서 64로 바꿔도 차이가 없었습니다. GeForce에서 MPS도 동작했지만 거의 같은 ≈2배 느려짐을 보였습니다.
- 공유 엔진 + vLLM `--scheduling-policy priority`: 피해자 p50이 **5.88 s (fcfs) → 2.51 s**로 줄었고, 이웃의 처리량은 그대로였습니다(1,311 → 1,308 tok/s). 큐가 생기는 설정(`--max-num-seqs 16`)에서 측정했습니다.
- 공유 엔진에서의 prefix-cache 타이밍: 반복된 프롬프트가 340 ms 대신 25 ms에 응답되어, 다른 테넌트가 최근에 그 프롬프트를 보냈다는 사실이 드러납니다. 테넌트별 `cache_salt`를 쓰면 이 신호가 사라졌습니다.
- HAMi-core의 컨테이너별 제한은 GeForce/CUDA 13에서 로드되었지만, **CUDA graph를 쓰는 vLLM에는 적용되지 않았습니다**(`--enforce-eager`일 때만 적용).

**7. 곁가지 실험: 검증 보조 도구로서의 로컬 LLM** ([harness/RESULTS.md](harness/RESULTS.md)): 합성한 테스트 실패 사례 24건. Qwen3.5-4B/9B는 88–92 %를 올바르게 분류하지만, 둘 다 "부분 실행 / 오래된 증거"를 `ok`로 통과시킵니다. GPU와 Mac mini(MLX)의 속도와 에너지도 비교했습니다.

## 측정한 것 vs 계획한 것

| Step | 상태 |
|---|---|
| 0 도구 준비 (Docker, NVIDIA Container Toolkit) | 완료 |
| 1 단일 GPU 서빙, 부하 테스트, 최적화, 요약표 | 완료 (대부분 단일 실행) |
| 2 관측 (DCGM, vLLM 메트릭, Prometheus, Grafana, 알림 규칙) | 완료. Alertmanager 알림 전달은 **연결하지 않음** |
| 3 k3s GPU 노드, time-slicing, 장애 테스트 | 완료. 롤링 업데이트 / 롤백은 **테스트하지 않음** |
| 4 멀티테넌시 | 쿼터, ingress NetworkPolicy, RBAC, Pod Security, noisy neighbor, MPS, priority, cache_salt, HAMi-core는 완료. **미완료:** egress 정책, `PriorityClass`, vCluster 비교, 테넌트 온보딩 자동화, 3개 이상의 테넌트 |
| 5 사용량 측정 / 과금 + GPUaaS API | **계획만 있음** |
| 6 장애 및 복구 시간표 | **계획만 있음** (일부 장애 테스트는 Step 3에 있음) |
| 7 KVM/OpenStack GPU 패스스루 정리 | **계획만 있음** |

이 체크박스가 들어 있는 원래 계획은 [docs/plan.md](docs/plan.md)에 있습니다.

## 저장소 구조

```
docs/
  plan.md                      원래의 단계별 계획 (2026-11-03 기준 상태)
  step-0-tooling.md            Docker + NVIDIA Container Toolkit 처음부터 설치
  step-1-single-gpu-serving.md 첫 vLLM 서버, VRAM 예산, 장애 훈련
  step-1b-load-testing.md      동시성 스윕, 포화, 현실적 부하, 7B AWQ, fp8 KV
  step-1c-optimization.md      옵션 탐색, 1,257문항 정확도 평가, prefix cache, Qwen3.5
  step-1d-summary-table.md     한 페이지짜리 VRAM / 처리량 / 전력 표, max-model-len
  step-2-observability.md      dcgm-exporter + Prometheus + Grafana, 알림 규칙, 교차 검증
  step-3-k8s-gpu-node.md       k3s 컨트롤 플레인 VM + GPU 워커, device plugin, time-slicing, 장애
  step-4-multitenancy.md       쿼터, NetworkPolicy, RBAC, PSA, noisy neighbor, MPS, priority, HAMi-core
  research-multitenancy.md     문헌/문서 조사 + 후속 실험 + AI 리뷰/토론 기록
  serving-explained.html       LLM 서빙 용어를 한 페이지로 시각화한 설명 (한국어)
bench/
  bench.py                     closed-loop 동시성 벤치마크 (표준 라이브러리만 사용, 스트리밍)
  bench_real.py                길이를 섞고 SLO를 검사하는 open-loop 포아송 부하 생성기
  eval_mc.py                   OpenAI 호환 서버를 대상으로 하는 객관식 정확도 평가
  fetch_evalset.py             Hugging Face Hub에서 평가 세트를 내려받음 (evalset.json 200문항 / evalset_big.json 1,257문항 객관식,
                               KMMLU CS + MMLU 일부. 문제 자체는 이 저장소에 저장하지 않음)
scripts/
  run_profile.sh               vLLM을 시작하고, 시작 시 KV 수치와 고정 부하 중의 nvidia-smi를 기록
  run_candidate.sh, run_candidate2.sh   모델/설정 하나에 대해 평가 x2 + 부하 스윕
k8s/
  k3s-lab/                     k3s 실험용 롤백 + 검증 스크립트
  k8s-tenancy/                 테넌트 매니페스트, 테스트 스크립트, results/ (원본 출력, AI 리뷰 노트)
harness/                       로컬 LLM 검증 보조 도구 평가 (harness/, src/, tests/, results/)
```

## 재현 방법

사전 요구 사항: NVIDIA GPU, 드라이버, Docker, NVIDIA Container Toolkit이 있는 Linux 호스트([step-0](docs/step-0-tooling.md) 참고). Python 도구는 표준 라이브러리만 사용합니다.

```bash
# 1) 서버 시작 (노트와 같은 플래그)
docker run -d --gpus all --ipc=host --name vllm \
  -v /data/models/hf:/root/.cache/huggingface -p 127.0.0.1:8000:8000 \
  vllm/vllm-openai:latest --model Qwen/Qwen2.5-3B-Instruct \
  --max-model-len 8192 --gpu-memory-utilization 0.85

# 2) 이상화된 동시성 스윕
python3 bench/bench.py --levels 1,4,16,64 --max-tokens 128

# 3) SLO 검사를 포함한 현실적인 open-loop 부하 (TTFT p95 <= 300 ms, 사용자당 >= 30 tok/s)
python3 bench/bench_real.py --rates 5,10,20 --duration 30

# 4) 정확도 (먼저 평가 세트를 내려받으세요: 이 저장소에는 포함되어 있지 않습니다)
python3 bench/fetch_evalset.py
python3 bench/eval_mc.py --model Qwen/Qwen2.5-3B-Instruct --set bench/evalset_big.json --out /tmp/eval.json
```

`scripts/*.sh`는 모델/설정 하나를 처음부터 끝까지 자동화합니다. 이 스크립트들은 랩 서버에서 쓰던 디렉터리 구조(도구는 `/data/lab/bench`, 모델 캐시는 `/data/models/hf`)를 가정하므로, `bench/*`를 그곳에 복사하거나 먼저 `B=`를 수정하세요. `k8s/k8s-tenancy/`의 Kubernetes 매니페스트는 NVIDIA device plugin이 있고 노드에 `gpu=true` 레이블이 붙은 k3s를 기준으로 작성되었으며, `teardown.sh`는 그 매니페스트가 만든 모든 것을 제거합니다. `put.sh`와 `harness/harness/orchestrate*.sh` / `power_*.sh` 도우미는 제 랩의 SSH 별칭(`gpu-server`, `macmini`, `openstack-host`)을 사용하므로 각자의 호스트에 맞게 수정하세요.

이 저장소에 없는 것: Prometheus/Grafana 설정 파일과 대시보드 JSON(step-2에 설명은 있지만 서버에만 있었습니다), 스크린샷, 모델 가중치, Step 1의 원본 `/data/lab/bench/*.json` 출력. 노트에 언급된 Step-4 도우미 `dose1.sh`는 보존하지 못했습니다.

## 데이터 고지

평가 문제는 이 저장소에 **포함되어 있지 않습니다**. `bench/fetch_evalset.py`가 Hugging Face Hub에서 문제를 내려받아, 측정에 사용한 세트와 같은 형식과 순서로 `bench/evalset.json`과 `bench/evalset_big.json`(git에서 무시됨)을 만듭니다. 이 스크립트는 자신이 쓴 파일의 SHA-256을 측정에 사용한 세트의 해시와 비교하므로, 원본 데이터가 바뀌었는지 알 수 있습니다.

| 출처 | 라이선스 | 사용한 부분 |
|---|---|---|
| [KMMLU](https://huggingface.co/datasets/HAERAE-HUB/KMMLU) (HAE-RAE, Son et al. 2024) | CC BY-ND 4.0 | Computer Science test, 앞 600행 |
| [MMLU](https://huggingface.co/datasets/cais/mmlu) (Hendrycks et al. 2021) | MIT | 여섯 과목의 test split (machine learning, computer security, college and high-school computer science, electrical engineering, college mathematics) |

데이터셋을 인용하고 각 라이선스를 따라 주세요. 이 저장소의 MIT 라이선스는 코드와 노트에만 적용됩니다.

## 노트에 대하여

노트는 AI 코딩 어시스턴트(Claude Code, Codex CLI)와 함께 작업하면서 작성했습니다. 명령어는 제가 직접 입력하고 검증했습니다. 결론에 대한 AI의 리뷰와 토론도 어시스턴트가 틀렸던 부분을 포함해 그대로 남겨 두었습니다([research §9–11](docs/research-multitenancy.md), `k8s/k8s-tenancy/results/*codex*.md`). 일부 노트는 함께 연재한 블로그 시리즈("홈랩 일지")를 언급합니다.

---

## 라이선스

코드와 노트: [MIT](LICENSE) © 2026 swlee3306. 평가 문제: *데이터 고지*를 참고하세요.
