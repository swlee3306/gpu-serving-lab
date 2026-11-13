# Step 1. 단일 GPU 모델 서빙 (vLLM)

장비: gpu-server (`ssh gpu-server`) · GPU: RTX 4070 Ti SUPER 16GB · 사전 완료: Step 0 (Docker + NVIDIA Container Toolkit, 이미지 저장소 `/data/containerd`)
실무 연결: **GPUaaS/LLMaaS의 가장 기본 단위 = "GPU 한 장에서 모델을 띄우고 요청을 받는 서버".** 이후 모든 단계(K8s, 과금, 관측성)가 이 서버 위에 쌓인다.

> 원칙은 Step 0와 같다: 개념 → 직접 입력 → 예상 결과 → 확인 질문 → 일부러 망가뜨려 보기 → 되돌리기 → 기록.
> 명령은 `ssh gpu-server`로 접속해서 직접 친다. 막히면 출력을 그대로 붙여서 물어본다.
> 모델 이름과 일부 옵션은 vLLM 버전에 따라 달라질 수 있다. 출력이 다르면 **오류 메시지를 읽는 것**부터 한다.

---

## 0. 왜 하나 (개념)

### LLM 서빙이란
학습된 모델 파일(가중치)을 GPU 메모리에 올려 두고, 사용자의 요청(질문)이 올 때마다 답을 한 토큰씩 생성해서 돌려주는 서버를 만드는 것. **vLLM**은 이 일을 빠르고 메모리 효율적으로 해 주는 서빙 엔진이다. 호환되는 **OpenAI 형식 API**를 제공해서, 클라이언트는 ChatGPT API와 같은 방식으로 호출할 수 있다.

### VRAM(GPU 메모리)은 어디에 쓰이나
```
┌──────────────── GPU 메모리 16GB ────────────────┐
│ 모델 가중치  │   KV cache (대화 기억)   │ 기타(활성값 등) │
│  (고정)      │   (요청 수 × 길이에 비례)│                 │
└─────────────────────────────────────────────────┘
        ▲                  ▲
   모델 크기·정밀도가 결정   `--gpu-memory-utilization`으로 전체 예산을 정하고
   (3B FP16 ≈ 6GB)          가중치를 뺀 나머지를 vLLM이 KV cache로 씀
```
- **가중치:** 파라미터 수 × 숫자 하나의 크기. 30억(3B) 파라미터를 16비트(2바이트)로 저장하면 약 6GB. 4비트 양자화(AWQ)면 약 1/4.
- **KV cache:** 모델이 이미 처리한 토큰들의 중간 결과(key/value)를 저장해 둔 것. 이게 있어야 다음 토큰을 만들 때 앞부분을 다시 계산하지 않는다. **동시 요청이 많을수록, 대화가 길수록 커진다.** 서빙의 동시 처리 능력은 사실상 "KV cache를 얼마나 확보했나"로 결정된다.
- **PagedAttention:** KV cache를 운영체제의 메모리 페이지처럼 작은 블록으로 쪼개 관리해서 낭비(단편화)를 줄이는 vLLM의 핵심 기법.
- **Continuous batching:** 요청을 한 묶음씩 기다렸다 처리하지 않고, 토큰 단위로 새 요청을 계속 끼워 넣어 GPU를 놀리지 않는 방식.

### 이번 단계에서 쓰는 주요 옵션
| 옵션 | 의미 |
|---|---|
| `--model` | 불러올 모델 (Hugging Face 이름 또는 경로) |
| `--max-model-len` | 한 요청이 가질 수 있는 최대 토큰 길이(입력+출력). 클수록 KV cache 예약이 커짐 |
| `--gpu-memory-utilization` | vLLM이 쓸 GPU 메모리 비율(기본 0.9). 가중치 + KV cache가 이 안에 들어가야 함 |
| `--ipc=host` (docker) | 컨테이너가 호스트의 공유 메모리를 쓰게 함 (PyTorch가 프로세스 간 통신에 공유 메모리를 씀) |

## 1. 시작 전 점검 (읽기만)

```bash
nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv
df -h / /data
ss -ltn | grep ':8000' || echo "8000 포트 비어 있음"
```
- 예상: VRAM 사용 거의 0 / 16376 MiB, `/data` 여유 약 326G, 8000 포트 비어 있음.
- `ss -ltn`: 열려 있는(LISTEN) TCP 포트 목록. 이미 쓰는 포트로 서버를 띄우면 충돌하기 때문에 먼저 확인.
- 질문: 이미지와 모델이 어디에 저장될지 지금 미리 말할 수 있나? (힌트: Step 0의 6번)

## 2. 모델 캐시 폴더 준비

모델 파일은 처음 한 번 내려받아 두고 재사용한다. 컨테이너를 지우면 내부 파일도 사라지므로 **호스트 폴더를 컨테이너에 연결(mount)** 한다.
```bash
mkdir -p /data/models/hf
ls -ld /data/models/hf
```
- `/data/models`는 Step 0의 2번에서 `sudo mkdir` + `chown`으로 이미 만들었고 `user` 소유다. **새 폴더를 만들 수 있는지는 바로 위 부모 폴더의 권한으로 결정**되므로 `sudo` 없이 `hf`를 만들 수 있다. (`/data` 자체는 root 소유 — `ls -ld /data /data/models`로 비교해 보자.)
- 이 폴더가 컨테이너 안의 `/root/.cache/huggingface`로 연결된다(아래 `-v`).
- 질문: 컨테이너는 `root`로 실행되는데, 그러면 이 폴더에 생기는 파일의 소유자는 누구일까? (6번에서 확인)

## 3. vLLM 이미지 받기

```bash
docker pull vllm/vllm-openai:latest
```
- `latest`는 최신 버전을 가리키는 태그라서 **시점마다 내용이 바뀐다.** 재현이 필요하면 구체적인 버전 태그를 쓰는 것이 좋다(받은 뒤 `docker images`의 ID/digest를 기록해 둘 것).
- 이미지가 **크다.** 실제로 받아 보니 **다운로드 9.21GB(압축), 디스크 점유 33.1GB(풀린 크기), 층 약 38개**였다(2026-10-30 기준 `latest`, digest `sha256:befccf0e7e40…`). 다운로드 크기와 디스크 점유를 혼동하면 용량 계산이 틀린다. 받는 동안 다른 터미널에서 확인해 보자(`watch -n 2 'sudo du -sm /data/containerd'`).
```bash
sudo du -sm /data/containerd /var/lib/containerd      # 어느 쪽이 늘어나나? (Step 0의 증거를 다시 확인)
df -h / /data
```
- 예상: **`/data/containerd`만** 늘고 `/var/lib/containerd`는 거의 0. 루트는 안 찬다.
- 질문: 받기가 끝나면 `docker images`의 `DISK USAGE`와 `CONTENT SIZE`는 각각 어떤 크기인가?

## 4. 첫 서빙: 작은 모델 (Qwen2.5-3B-Instruct)

`tmux`로 세션을 만들어 두면 SSH가 끊겨도 서버가 안 죽는다(처음이면 아래 한 줄 설명 참고).
```bash
tmux new -s vllm            # 새 세션. 나가려면 Ctrl+b 다음 d, 다시 들어가려면 tmux attach -t vllm
```
```bash
docker run --rm --gpus all --ipc=host \
  --name vllm \
  -v /data/models/hf:/root/.cache/huggingface \
  -p 127.0.0.1:8000:8000 \
  vllm/vllm-openai:latest \
  --model Qwen/Qwen2.5-3B-Instruct \
  --max-model-len 8192 \
  --gpu-memory-utilization 0.85
```
| 부분 | 설명 |
|---|---|
| `--gpus all` | GPU 연결 (Step 0에서 배운 대로 nvidia 런타임 사용) |
| `--ipc=host` | 호스트 공유 메모리 사용 (PyTorch 필수) |
| `--name vllm` | 컨테이너 이름 고정 (나중에 `docker logs vllm`, `docker stop vllm`) |
| `-v 호스트:컨테이너` | **볼륨 마운트.** 모델 캐시를 호스트 `/data/models/hf`에 둠 |
| `-p 127.0.0.1:8000:8000` | 컨테이너 8000 포트를 **호스트의 로컬(127.0.0.1) 8000**으로 연결. 로컬만 열어 둔 이유: 이 API에는 **인증이 없어서** 바깥에 열면 누구나 쓸 수 있다 |
| `vllm/vllm-openai:latest` | 이미지 |
| `--model ...` 이하 | **이미지 이름 뒤는 컨테이너 안의 vLLM에 전달되는 인자** (docker 옵션과 순서가 다르다) |

처음 실행하면 **모델 다운로드**가 먼저 일어난다(약 6GB). 로그에서 아래 단계를 찾아보자.
1. 모델 파일 다운로드 (`Downloading ...`)
2. 가중치 로딩 (`Loading weights ...`, 걸린 시간·메모리)
3. **KV cache에 확보된 메모리/토큰 수** (`GPU KV cache size: ... tokens` 같은 줄) ← 매우 중요한 숫자
4. `Application startup complete` ← 서버 준비 완료

- 질문: 3번의 "KV cache 토큰 수"를 `--max-model-len 8192`와 비교하면, **동시에 몇 개의 최대 길이 요청**을 담을 수 있을까?
- 모델 설정 확인(KV cache 크기 계산용). 다운로드된 뒤 다른 터미널에서:
```bash
find /data/models/hf -name config.json | head
cat $(find /data/models/hf -name config.json -path '*Qwen2.5-3B*' | head -1) | grep -E "num_hidden_layers|num_key_value_heads|num_attention_heads|hidden_size|torch_dtype"
```
  - `num_hidden_layers`(층 수), `num_key_value_heads`(KV 헤드 수), `hidden_size`와 `num_attention_heads`로 **헤드 크기**(= hidden_size ÷ num_attention_heads)를 구한다.
  - **토큰 하나의 KV cache 크기** ≈ 2(K와 V) × 층 수 × KV 헤드 수 × 헤드 크기 × 바이트 수(16비트면 2). 직접 계산해서 로그의 토큰 수와 맞춰 보자. (제 지식 기준 3B는 층 36, KV 헤드 2, 헤드 크기 128 근처. **파일에서 직접 확인할 것.**)

## 5. 요청 보내기 (다른 터미널)

새 SSH 세션 또는 tmux의 다른 창에서:
```bash
curl -s http://127.0.0.1:8000/v1/models | python3 -m json.tool
```
- 서버가 알려 주는 모델 목록. `Qwen/Qwen2.5-3B-Instruct`가 보여야 한다.

```bash
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"Qwen/Qwen2.5-3B-Instruct","messages":[{"role":"user","content":"쿠버네티스를 한 문장으로 설명해줘."}],"max_tokens":100}' | python3 -m json.tool
```
- `-H`: 헤더, `-d`: 요청 본문(JSON). `messages`는 대화 기록. `max_tokens`는 **최대 생성 토큰 수**.
- 응답에서 확인할 것: `choices[0].message.content`(답), `usage.prompt_tokens`/`completion_tokens`/`total_tokens`(토큰 수). **과금 단위가 이 토큰 수**다(Step 5에서 쓴다).
- 질문: `usage`의 숫자를 어디에 쓸 수 있을까? (힌트: 요금, 쿼터)

**관찰:** 요청을 보내는 동안 `watch -n 1 nvidia-smi`로 GPU 사용률과 VRAM을 본다(Ctrl+C로 종료).
- 질문: 요청이 없을 때와 처리 중일 때 `GPU-Util`이 어떻게 다른가? VRAM은 요청에 따라 변하나, 일정한가? (이유는? 힌트: vLLM은 시작할 때 KV cache를 **미리 확보**한다.)

## 6. 일부러 망가뜨려 보기 (오류 읽는 연습)

각 실험 전에 `Ctrl+C`로 서버를 끄거나 `docker stop vllm`. **결과를 예상한 뒤에** 실행한다.

**실험 A. 메모리 예산을 너무 크게**
```bash
... --gpu-memory-utilization 0.99
```
- 예상? 실제? 오류 메시지에서 "요청한 비율"과 "가능한 양"을 읽어 보자. (다른 프로세스나 화면 출력이 쓰는 메모리 때문에 0.99가 안 될 수 있다.)

**실험 B. 모델이 VRAM보다 클 때**
```bash
... --model Qwen/Qwen2.5-7B-Instruct --max-model-len 8192 --gpu-memory-utilization 0.9
```
- 7B FP16 가중치는 약 15GB. 예산(16GB×0.9 ≈ 14.7GB)보다 크다. 어떤 오류가 나는가? 로그에서 **가중치가 차지하는 메모리**와 **남은 KV cache 공간**을 찾아보자.

**실험 C. 길이를 줄여서 해결**
```bash
... --model Qwen/Qwen2.5-7B-Instruct-AWQ --max-model-len 8192 --gpu-memory-utilization 0.9
```
- AWQ(4비트 양자화)면 가중치가 약 5GB라서 KV cache 여유가 크다. B와 비교해 **KV cache 토큰 수**가 얼마나 늘었는지 확인한다.
- 질문: 같은 7B 모델인데 왜 AWQ는 들어가고 FP16은 안 들어갈까? 대신 잃는 것은?

**실험 D. 포트 충돌**
- 서버가 떠 있는 상태에서 같은 `-p 127.0.0.1:8000:8000`으로 컨테이너를 하나 더 띄워 본다. 오류 메시지를 읽어 보자.

## 7. 파일 소유자와 정리

```bash
ls -la /data/models/hf | head
sudo du -sh /data/models/hf
docker ps -a
```
- 컨테이너가 root로 파일을 만들었다면 호스트에서 `user`가 지우려 할 때 `Permission denied`가 난다. 왜 나는지, 어떻게 해결할지(`sudo` 사용, 또는 `docker run --user`로 소유자 지정) 생각해 보자.

서버 정리:
```bash
docker stop vllm        # --rm으로 띄웠다면 멈추면 컨테이너가 자동 삭제됨
docker ps -a
```
- 모델 캐시(`/data/models/hf`)는 **지우지 말고 둔다.** 다음 실험에서 재사용한다.

## 8. 확인 질문 (스스로 답해 보기)

1. VRAM 16GB는 무엇으로 나뉘어 쓰이나? (가중치, KV cache, 그리고?)
2. `--gpu-memory-utilization`을 올리면 무엇이 늘고, 위험은 무엇인가?
3. `--max-model-len`을 키우면 동시에 받을 수 있는 요청 수는 어떻게 되나? 왜?
4. 같은 7B 모델이 FP16에서 안 되고 AWQ에서 되는 이유는? 대신 잃는 것은?
5. 컨테이너에 `-p 127.0.0.1:8000:8000`과 `-p 8000:8000`의 차이와, 왜 앞의 것을 썼나?
6. API 응답의 `usage` 필드는 나중에 어디에 쓰이나?
7. `docker run`에서 이미지 이름 앞과 뒤의 인자는 각각 누가 받나?

## 9. 완료 기준
- [ ] 3B 모델을 띄우고 `/v1/chat/completions`로 답을 받았다
- [ ] 로그에서 "가중치 로딩"과 "KV cache 토큰 수"를 찾아서 설명할 수 있다
- [ ] `config.json`으로 토큰당 KV cache 크기를 직접 계산해 봤다
- [ ] 실험 A~D 중 최소 B와 C를 해 보고 결과를 설명할 수 있다
- [ ] 이미지와 모델이 `/data`에 쌓이는 것을 확인했다
- [ ] 확인 질문 7개에 답했다

> 다음 파트(Step 1-2): **부하 테스트.** 동시 요청 1·4·16·64로 tokens/s, TTFT, 지연 분포를 측정해서 "이 GPU 한 장에 동시에 몇 명을 받을 수 있나"를 숫자로 답한다.

## 10. 되돌리기
```bash
docker stop vllm 2>/dev/null
docker rmi vllm/vllm-openai:latest          # 이미지 삭제 (컨테이너가 없어야 함)
rm -rf /data/models/hf                       # 모델 캐시 삭제 (root 소유 파일이면 sudo 필요, 삭제 후 du로 확인)
```

## 내 답변
(여기에 적기)

## 실험 기록 (2026-10-31, gpu-server, vLLM 0.32.0, Qwen2.5-3B-Instruct, max-model-len 8192)

**첫 서빙(util 0.85):** 다운로드 5.8GB, 가중치 5.79 GiB, KV cache 6.08 GiB = **177,120 토큰**, 8,192토큰 요청 **21.62개** 동시, 서버 준비 약 2분 25초(대부분 다운로드), VRAM 13.2GiB.
- 설정 파일 계산과 일치: 토큰당 KV = 2×36층×2KV헤드×128×2B = **36,864 B**, 177,120 × 36,864 = 6.08 GiB.
- **VRAM은 부하가 아니라 "예약"**: 요청이 없어도 13GB, 요청 중에도 메모리는 그대로, GPU-Util만 0%↔100%.
- 요청당 시간은 토큰 수에 비례: 10토큰 0.174s, 45토큰 0.572s, 300토큰 3.36s → 약 **88~89 tokens/s**(요청 1개일 때), 고정 비용 약 0.06s.
- `prompt_tokens` 45 = 질문 16 + 채팅 템플릿/기본 시스템 문구 29. `finish_reason`: `stop`(모델이 끝냄) / `length`(max_tokens에 걸려 잘림).

**실험 A (util 0.99):** `ValueError: Free memory on device (15.32/15.54 GiB) on startup is less than desired GPU memory utilization (0.99, 15.38 GiB)`. 가중치 로딩 **전**(`init_device`, 2초)에 실패. 마지막 줄 `RuntimeError: ... See root cause above`는 요약이고 **첫 ERROR 줄이 원인.** 시작 전에 이미 0.22 GiB 사용 중 → 최대 util ≈ 0.986.

**실험 B (util 0.40):** `ValueError: No available memory for the cache blocks`. 가중치 로딩 **후** KV cache 계산 단계에서 실패. 직전에 정보 메시지: `CUDA graph memory profiling is enabled (default since v0.21.0). 0.4000 is equivalent to 0.3730 without CUDA graph memory profiling` → vLLM이 그래프 메모리(약 0.027×15.54 ≈ 0.42 GiB)를 예산에서 미리 뺀다(**버전에 따라 같은 util이 다른 KV 크기를 만든다**).
- 예측식: 고정 비용 = 6.12(가중치+필수) + 1.01(그래프 등) = **7.13 GiB**, KV = util×15.54 − 7.13.

**검증 (util 0.50):** 예측 KV 0.64 GiB·약 18,600토큰·2.3x ↔ 실제 **0.64 GiB, 18,736토큰, 2.29x** (오차 약 0.5%). → 고정 비용이 util과 무관하게 일정하다는 가정이 맞았다.
- 허용 범위(이 모델): 약 **0.46 ≤ util ≤ 약 0.985**. 아래로 넘으면 KV 자리 없음(B), 위로 넘으면 시작 시 남은 메모리 초과(A). 고치는 방향이 서로 반대.
- 오류 문구의 성격: A는 숫자(15.32 vs 15.38)를 알려 주고, B는 숫자를 안 알려 줘서 모델 크기로 직접 계산해야 한다.

## 내가 막힌 곳 / 새로 알게 된 것
(여기에 적기)
