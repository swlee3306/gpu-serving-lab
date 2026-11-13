# Step 2. 관측: GPU와 vLLM을 그래프로 보기

장비: gpu-server (`ssh gpu-server`) · 설치 위치 `/data/monitoring/` · vLLM 0.32.0 / Qwen2.5-3B-Instruct
실무 연결: GPUaaS/LLMaaS 운영의 기본. "지금 GPU가 놀고 있나, 막혀 있나, 사용자가 기다리나"를 사람이 로그를 읽지 않고 알려 주는 계층.

---

## 0. 왜 관측하나

Step 1b에서는 `docker logs`로 `KV cache usage 99%`, `Waiting: 256` 같은 줄을 **눈으로** 읽었다. 문제는 (1) 부하가 끝나면 사라지고, (2) 시간에 따른 변화를 못 보고, (3) 사람이 지켜봐야 한다는 점이다. 관측 스택은 같은 숫자를 15초마다 자동으로 모아 시계열로 저장하고 그래프로 보여준다.

핵심: **원인(GPU/KV) 지표와 증상(TTFT) 지표를 같은 시간축에 겹쳐 본다.**

## 1. 구성도 (전부 gpu-server 한 대 안, 모두 127.0.0.1에만 바인딩)

```
 [GPU 4070 Ti S] --NVML/DCGM--> dcgm-exporter :9400 --+
                                                      |  15초마다 스크레이프(pull)
 [vLLM 컨테이너] ---- /metrics :8000 -----------------+--> Prometheus :9090 (보존 7일)
                                                                   |
                                                         Grafana :3000 (대시보드)
                                                                   ^
 내 Mac --ssh -L 3000:127.0.0.1:3000--------------------------------+
```

- 세 컨테이너는 `network_mode: host`. 이유: vLLM이 `127.0.0.1:8000`에만 열려 있어 bridge 네트워크의 컨테이너는 접근할 수 없다. host 네트워크에 두고 각자 `127.0.0.1`에만 listen시켜 외부 노출은 없다.

## 2. 구성요소 개념

| 요소 | 역할 | 비고 |
|---|---|---|
| dcgm-exporter | NVIDIA DCGM으로 GPU 지표(사용률, VRAM, 온도, 전력, 클럭)를 `/metrics` 텍스트로 노출 | 컨테이너가 GPU를 볼 수 있어야 함(`runtime: nvidia`) |
| vLLM `/metrics` | 서버가 스스로 노출하는 서빙 지표(KV 사용률, 요청 수, TTFT 히스토그램 등) | 별도 설치 없음 |
| Prometheus | 주기적으로 대상 URL을 긁어(scrape) 시계열 DB에 저장, PromQL로 질의 | pull 방식. 대상이 죽으면 `up=0` |
| Grafana | Prometheus를 데이터소스로 그래프를 그림 | 설정은 파일(provisioning)로 재현 가능 |

**히스토그램**: TTFT는 `..._bucket{le="0.5"}`처럼 "0.5초 이하 요청의 누적 개수"를 구간별로 센 값이다. `histogram_quantile(0.95, rate(..._bucket[1m]))`로 p95를 추정한다.

## 3. 이미지 버전 (latest 금지, 고정)

| 서비스 | 이미지 |
|---|---|
| dcgm-exporter | `nvidia/dcgm-exporter:4.6.1-4.8.4` |
| Prometheus | `prom/prometheus:v3.15.1` |
| Grafana | `grafana/grafana:13.3.0` |
| (부하 중 사용) vLLM | `vllm/vllm-openai:latest` (Step 1에서 받은 것 그대로) |

## 4. 만든 파일

```
/data/monitoring/
  docker-compose.yml                 # 세 서비스 정의
  prometheus.yml                     # 스크레이프 설정 (15초, dcgm / vllm / prometheus 자신)
  provisioning/datasources/prometheus.yml
  provisioning/dashboards/dashboards.yml
  dashboards/gpu-vllm.json           # 대시보드 11패널 (Step 2 마무리에서 보강)
  alert_rules.yml                    # 알람 규칙 8개 (Step 2 마무리)
  tests/alert_rules_test.yml         # promtool 단위 시험(합성 시계열)
  *.bak-step2                        # 이번 수정 전 백업(compose, prometheus.yml, 대시보드)
  prometheus/  grafana/              # 데이터(볼륨). prometheus는 uid 65534, grafana는 uid 472 소유
  .grafana-admin                     # 관리자 계정/비밀번호 (mode 600)
  before-state.txt                   # 작업 전 docker/포트 상태 기록
```

핵심 설정 발췌:
- 보존 7일: Prometheus `--storage.tsdb.retention.time=7d`
- dcgm-exporter `-c 1000`: **수집 주기를 기본 30초에서 1초로.** 기본값이면 25초짜리 부하의 GPU util이 그래프에 늦게/뭉개져 나온다(처음 실행에서 실제로 겪음: 부하 중 util 0, 부하 후 100으로 보임).
- `restart: unless-stopped`: **의도적 선택.** 재부팅/도커 재시작 후 자동으로 올라와 지표 공백이 없다. 이 장비는 학습용이지만 관측은 "늘 켜져 있어야" 과거 데이터가 남는다. 수동으로 `docker compose stop`한 것은 재부팅 후에도 다시 뜨지 않는다. 원치 않으면 compose에서 `restart: "no"`로 바꾸고 `docker compose up -d`.
- 이번 재부팅 후 자동 기동은 **시험하지 않았다**(정책 설정만 확인).

## 5. 만든 명령

```bash
ssh gpu-server
cd /data/monitoring
docker compose pull          # 이미지 받기
docker compose up -d         # 기동
docker compose ps            # 상태
curl -s 127.0.0.1:9090/api/v1/targets   # 스크레이프 대상 상태
```

`/data`가 root 소유라 `/data/monitoring` 생성만 `sudo mkdir` + `chown user`로 했다(host 패키지 설치는 없음).

## 6. 지표 이름 확인 (추측 금지, 실제 `/metrics`에서)

vLLM 0.32.0 실제 출력에서 확인한 이름:

| 용도 | 이름 |
|---|---|
| KV 사용률 (0~1) | `vllm:kv_cache_usage_perc` |
| 실행/대기 요청 | `vllm:num_requests_running`, `vllm:num_requests_waiting` (추가로 `..._waiting_by_reason{reason="capacity"/"deferred"}`) |
| TTFT 히스토그램 | `vllm:time_to_first_token_seconds_bucket` |
| 생성 토큰 | `vllm:generation_tokens_total` (counter → `rate()`) |
| 선점 | `vllm:num_preemptions_total` |
| 프리픽스 캐시 | `vllm:prefix_cache_hits_total`, `vllm:prefix_cache_queries_total` |

DCGM: `DCGM_FI_DEV_GPU_UTIL`, `DCGM_FI_DEV_FB_USED`, `DCGM_FI_DEV_FB_FREE`(MiB), `DCGM_FI_DEV_GPU_TEMP`, `DCGM_FI_DEV_POWER_USAGE`(W) 모두 기본 설정에서 나옴을 확인.

예전 버전의 `vllm:gpu_cache_usage_perc`는 이 버전에 없다. 버전이 바뀌면 이름도 바뀔 수 있으니 대시보드가 "데이터 없음"이면 먼저 `/metrics`를 확인한다.

## 7. 실제 검증 결과 (2026-11-02, gpu-server)

(a) 컨테이너 3개 Up, restart policy 모두 `unless-stopped`.
(b) Prometheus targets: `dcgm up`, `vllm up`(vLLM이 떠 있을 때), `prometheus up`. vLLM을 내린 뒤에는 `vllm down`으로 바뀐다. **이것이 정상**(대상이 없으니 down). "0"과 "데이터 없음/down"을 구분하는 것이 계획표의 학습 포인트.
(c) 부하 중 변화: `bench_real.py --rates 6,10 --duration 25`(도착 6, 10건/초)를 돌리며 Prometheus HTTP API로 15초 간격 샘플:

| 시점 | KV 사용률 | running | waiting | GPU util | 전력 | 생성 tok/s(30s 평균) |
|---|---|---|---|---|---|---|
| 부하 전 | 0 | 0 | 0 | 0% | 약 50W | 0 |
| 6건/s 구간 | 3.8~7.1% | 12~18 | 0 | 100% | 216~227W | 115~750 |
| 10건/s 구간 | 3.1~18.3% | 10~42 | 0 | 100% | 218~243W | 600~920 |
| 부하 후 | 0 | 0 | 0 | 0% | 52W | 감소 |

부하 도구 결과: 6건/s 처리량 681 tok/s, TTFT p95 246ms(통과); 10건/s 처리량 990 tok/s, TTFT p95 476ms, 1인 29.6 tok/s(SLO 실패). Step 1b 표(618~1016 tok/s, TTFT p95 257/453ms)와 같은 수준.
- 이번 부하에서 **waiting은 한 번도 0을 넘지 않았고 KV는 최대 약 18%** 였다. Step 1b에서 본 "KV 99% → waiting 증가"는 더 센 부하(20건/s 이상)에서 나왔고, 이번에는 그 구간을 재현하지 않았다.
- 대시보드 스크린샷: 촬영했으나 이 저장소에는 포함하지 않음 (13절에서 전체 패널이 보이게 다시 촬영).

## 8. 대시보드 읽는 법

패널 9개: GPU 사용률 / VRAM / 온도·전력 / KV 사용률 / running·waiting / 선점률 / TTFT p50·p95 / 생성 처리량 / 프리픽스 캐시 적중률.

Step 1b의 "KV 99%일 때 waiting 증가 → TTFT 폭증"을 그래프로 보는 순서:
1. **KV 사용률** 선이 100%에 붙는지 본다. 이것이 원인 후보.
2. 같은 시각에 **waiting**이 0에서 솟는가? 솟으면 새 요청이 줄 서기 시작.
3. 같은 시각 이후 **TTFT p95**가 올라가는가? 대기 시간이 TTFT에 더해진 증상.
4. **선점률**이 0보다 크면 KV가 모자라 실행 중 요청이 밀려난 것(더 나쁜 신호).
5. **처리량(tok/s)** 이 평평해지는데 GPU util이 100%면 계산 포화, util이 낮은데 waiting이 높으면 다른 한계(KV나 동시 상한)를 의심.
- 원인(KV)이 증상(TTFT)보다 **먼저** 움직이는 시간차를 보는 것이 포인트.
- 주의: GPU util 100%는 "SM이 일하고 있었다"는 샘플링 값이지 "한계에 도달"이 아니다. 부하 중 5건/s에서도 100%였다.
- 주의: VRAM used는 vLLM이 `gpu-memory-utilization 0.85`로 미리 잡아서 요청 수와 무관하게 약 13.5GB로 평평하다. 요청이 늘어도 안 올라간다. 실제 소비를 보는 것은 KV 사용률.
- 좀비 점유(계획표 알람 초안)는 "VRAM used 높음 + util 0% + vLLM running 0"인 상태. 예: 부하 직후 그래프(util 0%, VRAM 13.5GB)는 서버가 살아 있으니 정상, 서버가 죽었는데 이 상태면 좀비.

## 9. 확인 질문 (답을 적어 보세요)

1. dcgm-exporter 수집 주기를 30초 그대로 두면 25초짜리 부하의 GPU util이 어떻게 보이나? 왜 `-c 1000`을 줬나?
   - 내 답:
2. VRAM 사용량 그래프가 요청이 늘어도 평평한 이유는? 그럼 "KV 여유"는 어떤 지표로 보나?
   - 내 답:
3. KV 사용률 99%, waiting 증가, TTFT p95 폭증이 그래프에서 어떤 순서로 나타나야 "KV가 원인"이라고 말할 수 있나? 그 순서가 아니면 다른 원인은 무엇이 있나?
   - 내 답:
4. vLLM을 내리면 `vllm` 타겟이 down이 되고 KV 그래프에 선이 끊긴다. "값 0"과 "데이터 없음"은 운영에서 왜 다르게 다뤄야 하나?
   - 내 답:

## 10. 접근 / 중지 / 삭제 / 되돌리기

```bash
# 접근 (Mac에서, 터널을 열어 둔 동안만)
ssh -L 3000:127.0.0.1:3000 -L 9090:127.0.0.1:9090 gpu-server
#  -> 브라우저 http://127.0.0.1:3000 (admin / 비밀번호는 서버의 /data/monitoring/.grafana-admin)
#  -> 대시보드: "GPU + vLLM 관측"
# 비밀번호 확인 (필요할 때만, 화면에 남기지 말 것)
ssh gpu-server 'grep PASSWORD /data/monitoring/.grafana-admin'

# 중지(데이터 유지) / 재시작
ssh gpu-server 'cd /data/monitoring && docker compose stop'
ssh gpu-server 'cd /data/monitoring && docker compose up -d'

# 완전 삭제(데이터 포함, 되돌릴 수 없음)
ssh gpu-server 'cd /data/monitoring && docker compose down && sudo rm -rf /data/monitoring'

# vLLM을 띄워 관측 확인
docker run -d --rm --gpus all --ipc=host --name vllm -v /data/models/hf:/root/.cache/huggingface \
  -p 127.0.0.1:8000:8000 vllm/vllm-openai:latest --model Qwen/Qwen2.5-3B-Instruct \
  --max-model-len 8192 --gpu-memory-utilization 0.85
docker stop vllm
```

작업 전 상태(`docker ps -a` 비어 있음, 포트 3000/9090/9400 미사용)는 `/data/monitoring/before-state.txt`. 되돌리려면 위 "완전 삭제"로 작업 전 상태가 된다(이미지 3개는 `docker image rm`으로 별도 삭제).

## 11. 보안 메모

- 모든 포트는 `127.0.0.1`에만 listen(`ss -ltn`으로 3000/9090/9400이 127.0.0.1임을 확인). 외부에서 직접 접속 불가, ssh 터널로만.
- Grafana 관리자 비밀번호는 무작위 생성, `/data/monitoring/.grafana-admin`(mode 600). 문서·채팅에 값을 적지 않는다. 회원가입 비활성, 익명 접근 없음, 업데이트 확인/통계 전송 끔.
- dcgm-exporter에 `SYS_ADMIN` capability를 줬다(DCGM 프로파일링용 권장 설정; 필요 최소인지는 따로 검증하지 않음).
- Prometheus/Grafana 데이터 폴더는 컨테이너 uid 소유라 host에서 쓰려면 sudo가 필요.

## 12. 확인한 것 vs 확인하지 못한 것

확인함: 이미지 3개 고정 버전 기동, 타겟 3개 up, vLLM 지표 이름 실측, 부하 중 KV/running/GPU util/전력/처리량 변화, Grafana 데이터소스 헬스 OK와 대시보드 프로비저닝, 스크린샷 촬영, 127.0.0.1 바인딩, vLLM 정리.
(위 7절 시점의 기록. 아래 13~17절에서 일부 해소: 알람 규칙, 전체 패널 스크린샷, KV 99%/waiting/선점 재현.)
아직 못 함: 재부팅 후 자동 기동(시험 안 함), 14일 이상 보존 시 디스크 사용량, Alertmanager 연동(알림 발송), TTFT 급등과 KV 99%의 시간차를 대시보드에서 정밀하게 읽는 연습.

---

# Step 2 마무리 (2026-11-02): 알람, 측정값 일치 검증, 대시보드 보강

## 13. 알람 규칙 (`/data/monitoring/alert_rules.yml`, `prometheus.yml`의 `rule_files`로 연결)

Alertmanager는 붙이지 않았다. 규칙 상태는 Prometheus Alerts 화면(`/alerts`, ssh 터널 9090)이나 API(`/api/v1/alerts`)와 대시보드 "알람 상태" 패널로만 본다. 규칙 파일은 compose에서 읽기 전용으로 마운트하므로 `docker compose up -d`로 prometheus만 재생성했다(데이터 볼륨은 그대로).

| 규칙 | 식(요약) | 시험용 for | 운영 권장 for | 임계값 근거 |
|---|---|---|---|---|
| GpuZombieMemory | `(FB_USED > 1024 and GPU_UTIL == 0) unless on() (up{job="vllm"} == 1)` | 2m | 10m | 정상 vLLM은 유휴 때도 VRAM 약 13GiB를 잡고 util 0%라서 vLLM 타깃이 up이면 제외 |
| GpuTemperatureHigh | `GPU_TEMP > 75` | 2m | 5m | 실측 최대 약 58C. 드라이버 값 target 84 / max operating 90 / slowdown 92 / shutdown 97C (`nvidia-smi -q -d TEMPERATURE`) |
| GpuTemperatureCritical | `GPU_TEMP > 85` | 1m | 2m | target 84C 초과 = 팬 곡선이 못 따라간다는 뜻 |
| GpuPowerOverLimit | `POWER_USAGE > 300` | 1m | 2m | 전력 제한 285W, 실측 최대 약 281W. 제한 근처는 정상이고 제한 초과가 이상 |
| GpuIdlePowerHigh | `POWER_USAGE > 120 and GPU_UTIL == 0` | 5m | 10m | 유휴 약 50W. 전력 최소 제한값(100W)보다 높은데 util 0%면 이상 |
| VllmKvCacheHigh | `vllm:kv_cache_usage_perc > 0.9` | 1m | 5m | 99%에서 waiting이 늘기 시작(Step 1b) |
| VllmRequestsWaiting | `vllm:num_requests_waiting > 0` | 1m | 5m | 줄 서기가 계속되면 용량 부족 |
| VllmTargetDown | `up{job="vllm"} == 0` | 5m | 5m | vLLM 지표 규칙은 타깃이 죽으면 "데이터 없음"이라 침묵하므로 그 자체를 따로 감시 |

임계값(75/85C, 300W, 120W, 90%)은 이 장비의 실측값과 드라이버 한도에서 정한 **초안**이다. 운영하면서 평소 분포를 보고 조정해야 한다.

### 좀비 점유 규칙의 함정과 설계
- 단순하게 `FB_USED > X and GPU_UTIL == 0`만 쓰면 요청이 없을 때의 **정상 vLLM**(VRAM 13,234 MiB, util 0%)이 오탐된다.
- `unless on() (up{job="vllm"} == 1)`: `on()`(라벨 없음)으로 비교하므로 vLLM이 살아 있으면 GPU 시계열 전체가 제외된다. 한계: 같은 장비에 vLLM과 별개의 좀비가 함께 있으면 가려진다(vLLM이 up인 동안). GPU가 1장인 이 장비에서만 의미가 단순한 설계다.
- vLLM이 기동 중일 때(모델 로딩 약 55초, 큰 모델은 수 분)는 `up=0`인데 VRAM은 잡혀 있어 조건이 참이 된다. `for`가 로딩 시간보다 길어야 한다. 시험용 2m은 3B 기준이고, 운영 권장 10m은 7B 이상 로딩 시간까지 감안한 값.

### 시험 결과 (실기 + 단위 시험)

| 시험 | 방법 | 기대 | 관찰 |
|---|---|---|---|
| ① vLLM 유휴 | vLLM 3B 기동 후 요청 없이 대기(VRAM 13,234 MiB, util 0%, `/health` 200) | GpuZombieMemory 발화 금지 | 기동 중(up=0, VRAM 6.4~13GiB) 약 30초간 pending이었다가, 타깃이 up이 되자 곧바로 inactive로 돌아갔고 약 2.5분 유휴 동안 발화 없음 (10초 간격 폴링 기록) |
| ② vLLM 없이 VRAM만 점유 | `docker run --entrypoint python3 vllm/vllm-openai:latest -c "torch로 3GiB 할당 후 sleep"` (이미 있던 이미지 사용, 컨테이너명 zombie-test). VRAM 3,290 MiB, util 0% | 발화 | pending → 약 2분 후 **firing**. 컨테이너 중지 후 20초 안에 해소(ALERTS에서 사라짐) |
| ③ 단위 시험 | `promtool test rules tests/alert_rules_test.yml` (합성 시계열 5건: 유휴 vLLM 미발화, vLLM down+점유 발화, 온도 90C Critical, KV 95%+waiting 5 발화, KV 30초 스파이크 미발화) | 전부 통과 | SUCCESS |
| ④ KV/waiting 실기 | 아래 16절의 고부하(도착 60건/s) | VllmKvCacheHigh, VllmRequestsWaiting 발화 | 두 규칙 모두 pending → firing 확인(ALERTS 시계열) |

- 문법 검증은 `promtool check rules`(prom/prometheus 이미지 안)로 8개 규칙 SUCCESS.
- 온도/전력 규칙(GpuTemperature*, GpuPower*, GpuIdlePowerHigh)은 정상 부하에서 발화하지 않았고(실측 최대 54C/231W), 임계값을 넘기는 실기 시험은 하지 않았다(GPU를 일부러 과열시키지 않음). 온도 Critical만 단위 시험으로 검증.

## 14. "대시보드 = Step 1 측정값" 일치 검증

같은 명령으로 vLLM(3B, util 0.85, max-model-len 8192)을 띄우고 `bench_real.py --rates 4 --duration 25`를 실행하면서, 같은 구간을 (1) 서버에서 `nvidia-smi -l 1`로 1초 간격 기록, (2) Prometheus HTTP API(`/api/v1/query`의 구간 선택자와 `/api/v1/query_range`)로 조회했다. 시도 1회로 일치했다(재시도 불필요).

부하 도구 결과: 처리량 410 tok/s, TTFT p95 291ms, 최대 동시 15, 100건 성공. 기존 `profile-P1_3B.json`(410 tok/s, p95 308ms, 최대 동시 15)과 같은 수준.

| 항목 | Step 1 profile-P1_3B | 이번 nvidia-smi 1초(재측정) | Prometheus (15초 스크레이프) | 해석 |
|---|---|---|---|---|
| GPU 사용률 평균 | 100.0% | 96.8% (31샘플, 첫 샘플 0 포함) | 100% (부하 중 샘플 2개) | 같음. 구간 경계 샘플 포함 여부 차이 |
| GPU 사용률 최대 | 100% | 100% | 100% | 같음 |
| 전력 평균 | 205.7 W | 198.5 W | 201.7 W (208.7, 194.7) | 3% 이내, 샘플 2개라 통계적 의미는 약함 |
| 전력 최대 | 231.0 W | 230.5 W | 208.7 W | **15초에 1점**이라 순간 최대를 놓침 |
| VRAM 최대 | 13,452 MiB | 13,452 MiB | 13,452 MiB | 정확히 같음 (미리 잡은 값이라 평평) |
| 온도 최대 | 53 C | 54 C | 47 C | 스크레이프 지연 + 온도는 천천히 오름 |
| KV 사용률 최대 | 3.2% | (vLLM 지표라 해당 없음) | 3.1% | 같음 |
| running 최대 | 최대 동시 15(클라이언트 관점) | - | 7 | 15초에 1점이라 정점을 못 봄 |
| 생성 처리량 | 410 tok/s(클라이언트) | - | `rate(...[30s])` 최대 381 tok/s | 30초 창 평균이 25초 부하+꼬리로 희석됨. 구간 총 토큰 `increase` = 14,471개 |
| waiting | 0 | - | 0 | 같음 |

차이의 원인(확인된 것):
1. 스크레이프 15초 vs nvidia-smi 1초: 25초짜리 부하에서 Prometheus는 부하 안에 샘플이 2개뿐이다. **평균/VRAM/KV처럼 안정된 값은 일치**하고, **최대값(전력, running)처럼 순간값은 낮게 나온다.** 짧은 부하를 검증하려면 `-c 1000`(dcgm 수집 1초)만으로는 부족하고 스크레이프 주기도 짧아야 한다(예: 5초). 운영 모니터링에서는 15초가 일반적인 값이고, 그래서 대시보드는 "추세", Step 1의 nvidia-smi 1초는 "정밀 측정"으로 역할이 다르다.
2. rate 창: `rate(counter[30s])`는 창 안 평균이라 부하가 25초면 희석된다. 처리량은 `increase`/구간 시간이나 클라이언트 측 값이 더 정확.
3. 온도는 열 관성으로 부하 시작 후에도 몇 초~수십 초 늦게 오른다.
- 확인하지 못한 것: Prometheus 샘플 시각과 부하 구간의 초 단위 정렬(스크레이프 타임스탬프 기준으로 구간 필터링만 했다).

### "값 0"과 "데이터 없음"의 구분 (vLLM 중지 후 직접 관찰)
vLLM 컨테이너를 내리고 40초 뒤:
- `/api/v1/targets`: dcgm `up`, prometheus `up`, **vllm `down`**
- `up{job="vllm"}` = **0** (값이 있다: 대상이 죽었다는 사실 자체가 데이터)
- `vllm:kv_cache_usage_perc` = **빈 결과**(데이터 없음. 0이 아님)
- `DCGM_FI_DEV_GPU_UTIL` = **0**, `DCGM_FI_DEV_FB_USED` = 0 (GPU는 살아 있고 실제로 0)
- 알람: VllmTargetDown만 pending. KV/waiting 규칙은 "데이터 없음"이라 침묵한다.
요점: "KV 0%"는 한가하다는 뜻이지만 "KV 데이터 없음"은 측정 자체가 안 된다는 뜻이므로 별도 알람(`up == 0`)이 필요하다. 대시보드의 "스크레이프 대상 up" 패널은 `noValue`를 "데이터 없음"으로 표시해 둘을 구분한다.

## 15. 보강한 대시보드 패널 (11개, `/data/monitoring/dashboards/gpu-vllm.json`)

기존 9패널에서 다음을 바꿨다(수정 전 파일은 `gpu-vllm.json.bak-step2`).
- **추가** "GPU 사용률 vs 전력": 사용률(왼쪽 %) + 전력(오른쪽 W) + 전력 제한 285W 점선. Step 1의 "util 100%는 용량 지표가 못 된다"를 보이게 한 패널. 같은 100%에서 전력이 중간 부하 구간 약 200W대, 최대 약 230W로 달라지고, 유휴는 util 0%에 약 50W.
- **통합** "vLLM: KV 사용률 / running / waiting": 기존 KV 패널과 running/waiting 패널을 하나로. 오른쪽 축에 KV%(0~100%)와 알람 임계 90% 점선.
- **추가** "알람 상태": firing/pending 개수(스탯, 0이면 초록).
- **추가** "스크레이프 대상 up": 타깃별 UP/DOWN, 없으면 "데이터 없음".
- 온도/전력 패널은 전력을 오른쪽 축(W)으로 분리. 모든 선에서 점 표시 제거.
- 높이를 7로 줄여 4행 x 3열로 재배치(12칸 -> 전체 28칸). 전체가 한 화면에 보이도록 뷰포트 높이 1250px, kiosk 모드로 촬영.
- 스크린샷: 저장소에는 포함하지 않음 (부하를 걸고 있는 동안 촬영, 11개 패널 전부 보임: 전력/KV/running/처리량/TTFT가 올라가는 모습).
- 알람 상태 패널은 `ALERTS` 시계열(Prometheus가 자동 생성)을 쓴다. 스크린샷 시점에는 규칙이 발화하지 않아 0.

## 16. KV 99% -> waiting 증가: 이번에는 재현함

앞의 부하(최대 KV 18%)로는 못 봤지만, 이번에 같은 서버(util 0.85)에 도착 60건/s를 25초(`--max-inflight 2000`) 걸어 한 번 시도했다. Prometheus에서 조회한 구간 최댓값:

| 항목 | 최댓값 |
|---|---|
| `vllm:kv_cache_usage_perc` | **99.2%** |
| `vllm:num_requests_waiting` | **797** |
| `vllm:num_requests_running` | 216 |
| 선점 횟수(`increase(vllm:num_preemptions_total)`) | 약 10 |
| 알람 | VllmKvCacheHigh, VllmRequestsWaiting이 pending -> firing |

- 확인한 것: KV가 99%에 닿고 waiting이 수백으로 솟고 선점도 일어난다. 알람 두 개가 실제로 발화한다. 이전 기록(util 0.85에서 17.8%에서 가득 차지 않음)은 부하 강도의 문제였다(동시 요청이 매우 많은 짧은 폭주 부하에서는 가득 참).
- 확인하지 못한 것: 3번 확인 질문에 필요한 "KV가 TTFT보다 먼저 움직이는 시간차"를 초 단위로 정리하지 못했다(15초 스크레이프 해상도의 한계). 이 폭주 부하의 `--out` 결과 JSON은 남지 않았다(`timeout 100`으로 감싸 실행했고 파일이 생성되지 않음). 폭주 구간 대시보드 스크린샷도 찍지 않았다.

## 17. 계획표와 달라진 점: Prometheus + Grafana를 Mac mini가 아니라 gpu-server에 구성

계획표는 "Mac mini에 Prometheus + Grafana, gpu-server를 스크레이프"인데 실제로는 gpu-server 안에 세 컨테이너를 모두 두었다.

이유:
- Mac mini는 상시 운영 서비스가 올라가 있는 장비라 보호가 우선이다(이전 결정, 메모리의 "Mac mini 복원력과 서비스 배치" 참고). 새 상시 서비스와 7일치 시계열 쓰기를 거기 추가하지 않는 것이 위험이 적다.
- 작업 범위를 gpu-server 한 대 안의 `/data/monitoring/`으로 한정해 되돌리기 쉽게 하려 했다(`docker compose down` + 디렉터리 삭제로 원상복구).
- 모두 127.0.0.1 바인딩으로, 네트워크 설계(다른 장비와의 연결, 포트 개방)를 바꾸지 않고 시작할 수 있다.

장단점:
- 장점: 설정이 한 곳에 있고 localhost 스크레이프라 네트워크/인증 이슈가 없다. 다른 장비에 영향이 없다.
- 단점 1: **gpu-server가 꺼지거나 Ubuntu에서 Windows로 부팅 전환하면 모니터링도 같이 꺼진다.** 정작 장애 시점의 데이터를 못 본다(Mac mini에 있으면 "스크레이프 실패 = down"이라는 증거가 남는다). 듀얼부팅 장비라 이 점이 특히 크다.
- 단점 2: 같은 GPU 장비 현황을 보는 기존 장비 현황 페이지과 수집이 이중이 될 수 있다. 같은 기준(util 0%인데 VRAM 점유)을 두 곳에서 따로 정의하면 어긋날 수 있다. 지금은 겹치는 수집을 줄이거나 통합하지 않았고 비교도 하지 않았다.
- 단점 3: gpu-server 디스크(`/data`)를 시계열이 쓴다(7일 보존).

나중에 Mac mini로 옮길 때 바꿀 곳(옮기는 작업은 하지 않음):
1. `prometheus.yml`의 스크레이프 대상 `127.0.0.1:9400`(dcgm), `127.0.0.1:8000`(vllm)을 gpu-server가 Mac mini에서 닿는 주소로 바꾼다. Tailscale IP/호스트명 또는 ssh 터널의 로컬 포트.
2. gpu-server 쪽 바인딩 변경: 지금은 dcgm-exporter(`-a 127.0.0.1:9400`)와 vLLM(`-p 127.0.0.1:8000`)이 loopback에만 열려 있어 밖에서 못 긁는다. 옮기려면 (a) Tailscale 인터페이스 IP에만 바인딩(외부 LAN 노출 금지) 또는 (b) Mac mini에서 `ssh -L`/autossh 터널로 포워딩 중 하나를 골라야 한다. 노출 범위를 다시 검토해야 한다.
3. `docker-compose.yml`을 Mac mini용으로 분리: prometheus와 grafana만 두고 dcgm-exporter는 gpu-server에 남긴다. `network_mode: host`는 Mac(Docker Desktop)에서는 의미가 다르니 포트 매핑으로 바꾼다.
4. `alert_rules.yml`의 `unless on() (up{job="vllm"} == 1)`은 job 이름에 의존하므로 job 이름을 유지하거나 규칙을 같이 수정한다.
5. 장점이 생기는 것: gpu-server가 꺼졌을 때 `up == 0` 알람(Alertmanager 필요)을 Mac mini에서 보낼 수 있다. 이때 GPU 규칙들은 "데이터 없음"으로 침묵하므로 `up{job="dcgm"} == 0` 규칙을 추가해야 한다.
6. Grafana 데이터(`grafana/`)와 Prometheus 데이터(`prometheus/`)는 uid 소유 때문에 단순 복사 대신 볼륨 재설정 또는 새로 시작이 낫다(과거 7일은 포기해도 되는지 먼저 판단).

## 18. 재부팅 후 자동 기동 (읽기 전용 확인만, 시험은 하지 않음)

- `systemctl is-enabled docker` = `enabled`
- 컨테이너 3개의 restart 정책 = `unless-stopped`
- **재부팅 시험은 하지 않았다**(듀얼부팅 장비라 재부팅하지 않음). 따라서 "재부팅 후 실제로 세 컨테이너가 뜨고 GPU 지표가 이어지는지"는 확인하지 못한 가정이다.

## 19. 이번 마무리 확인 질문 (답을 적어 보세요)

5. "GPU 사용률 0%인데 VRAM 점유" 규칙에 `unless on() (up{job="vllm"} == 1)`을 넣은 이유는? 이 설계가 놓치는 경우는?
   - 내 답:
6. 같은 util 100%인데 전력이 다른 이유는? 이 패널을 보고 "아직 용량 여유가 있다/없다"를 판단할 수 있나?
   - 내 답:
7. 15초 스크레이프로 25초짜리 부하의 전력 최대값이 낮게 나온 이유는? 짧은 이벤트를 놓치지 않으려면 무엇을 바꿔야 하나?
   - 내 답:
