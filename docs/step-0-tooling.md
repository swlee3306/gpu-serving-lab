# Step 0. 기반 도구: 컨테이너에서 GPU 쓰기 (처음부터 직접)

장비: gpu-server (`ssh gpu-server`, Ubuntu 24.04, RTX 4070 Ti SUPER 16GB)

**시작 상태 (2026-10-30 롤백 완료):** NVIDIA 드라이버 580만 설치돼 있고, Docker·Container Toolkit·`/data` 하위 폴더는 없다. 모든 명령은 **내가 직접 입력**하고, 각 명령이 무엇을 하는지 이해한 뒤 다음으로 넘어간다.
접속: `ssh gpu-server` (Claude Code 안에서는 `! ssh gpu-server '명령'`)
막히면 오류 메시지를 그대로 붙여서 질문하면 같이 읽는다.

---

## 0. 왜 하나 (개념)

컨테이너는 호스트와 **커널을 공유**할 뿐 GPU 드라이버를 모른다. 컨테이너 안에서 GPU를 쓰려면 층이 필요하다.

```
[ vLLM 컨테이너 ]             ← 우리가 돌릴 앱 (CUDA 라이브러리는 이미지 안에)
      │
[ Docker (docker-ce) ]        ← 컨테이너를 만들고 관리
      │ runtime = nvidia
[ NVIDIA Container Toolkit ]  ← 컨테이너를 만들 때 GPU 장치와 드라이버 라이브러리를 "끼워 넣어 주는" 부분
      │
[ containerd / runc ]         ← 실제로 프로세스를 격리해서 실행
      │
[ NVIDIA 드라이버 580 + 커널 모듈 ]   ← 호스트에 이미 설치됨 (컨테이너 안에 설치하지 않는다)
      │
[ GPU ]
```

핵심: **드라이버는 호스트에, CUDA 라이브러리는 이미지에, 둘을 잇는 것이 Container Toolkit.**
실무 연결: "Docker, Kubernetes 실무". Step 3의 K8s GPU 지원도 이 구조 위에서 동작한다.

## 1. 시작 전 점검 (읽기만)

**1-1.** 호스트에서 GPU가 보이는지
```bash
nvidia-smi
```
- 예상: `RTX 4070 Ti SUPER`, `Driver Version: 580.178.04`, 메모리 16376MiB.
- `CUDA Version: 13.0`은 "이 드라이버가 **지원하는 최대** CUDA"다. 설치된 CUDA 도구가 아니다.
- 이게 안 되면 컨테이너도 안 된다.

**1-2.** 아직 아무것도 없는지
```bash
command -v docker; dpkg -l | grep -E "docker|nvidia-container"; df -h / /data
```
- 예상: `docker`는 아무것도 안 나옴, 패키지 목록 비어 있음. 루트(`/`) 약 147G, `/data` 약 344G.
- 질문: 루트와 `/data`를 왜 나눠 놓았을까? (모델·이미지가 크다)

## 2. 작업 디렉터리 준비

```bash
sudo mkdir -p /data/models /data/lab
sudo chown -R $USER:$USER /data/models /data/lab
ls -ld /data/models /data/lab
```
- `mkdir -p` 상위 폴더가 없어도 만든다. `chown` 내 계정이 쓸 수 있게 소유자를 바꾼다. `$USER`는 현재 로그인 사용자.
- 예상: 두 폴더가 `user user`로 보인다.
- 모델 파일(`/data/models`)은 수 GB~수십 GB가 된다. 루트 디스크에 두면 안 되는 이유가 이것이다.

## 3. Docker 설치

**3-1. 공식 저장소 등록 (왜?)** Ubuntu 기본 저장소의 `docker.io`는 버전이 오래돼서, Docker 공식 apt 저장소를 쓴다. apt가 패키지를 믿으려면 **서명 키**가 필요하다.
```bash
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
```
- `install -d` 권한을 지정해 디렉터리 생성. `curl -fsSL` 조용히(-s) 오류는 보이게(-S) 리디렉션 따라가며(-L) 실패하면 실패 처리(-f).

**3-2. 저장소 한 줄 추가**
```bash
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" | sudo tee /etc/apt/sources.list.d/docker.list
cat /etc/apt/sources.list.d/docker.list
```
- `$(dpkg --print-architecture)` → `amd64`, `$VERSION_CODENAME` → `noble`. `signed-by=`는 이 저장소는 위 키로만 검증하겠다는 뜻.
- 예상: `deb [arch=amd64 signed-by=...] https://download.docker.com/linux/ubuntu noble stable`

**3-3. 설치**
```bash
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```
- `docker-ce` 데몬, `docker-ce-cli` 명령어, `containerd.io` 컨테이너 실행 엔진, 나머지는 빌드·compose 플러그인.
- 확인: `docker --version` → `Docker version 29.x`, `sudo systemctl status docker --no-pager | head -5` → `active (running)`.

**3-4. sudo 없이 쓰려면 (선택)**
```bash
sudo usermod -aG docker $USER
```
- `docker` 그룹에 내 계정을 추가한다. **새 SSH 세션부터 적용**된다(`exit` 후 다시 접속).
- 보안 메모: `docker` 그룹 = 사실상 root 권한이다. 개인 실습 서버라서 허용하지만 운영 환경에서는 신중해야 한다.

## 4. NVIDIA Container Toolkit 설치

**4-1. 저장소 등록**
```bash
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
```
- 앞 줄은 키를 받아 apt가 읽을 수 있는 형식으로 변환(`--dearmor`). 뒷줄은 저장소 목록을 받아서 `signed-by` 키를 끼워 넣는다(`sed`).

**4-2. 설치**
```bash
sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit
nvidia-ctk --version
```
- 예상: `NVIDIA Container Toolkit CLI version 1.20.x`

**4-3. Docker에 연결 (핵심)**
```bash
sudo nvidia-ctk runtime configure --runtime=docker
cat /etc/docker/daemon.json
sudo systemctl restart docker
docker info | grep -i runtimes
```
- 이 명령이 `/etc/docker/daemon.json`에 **`nvidia` 런타임을 등록**한다. 이게 "끼워 넣어 주는" 연결고리다.
- 예상: `daemon.json`에 `"runtimes": {"nvidia": {...}}`, `docker info`에 `Runtimes: nvidia runc ...`

## 5. 첫 GPU 컨테이너

```bash
docker run --rm --gpus all nvidia/cuda:12.6.3-base-ubuntu24.04 nvidia-smi
```
- `--rm` 끝나면 컨테이너 삭제, `--gpus all` 모든 GPU 연결, 이미지는 CUDA 12.6 기반 최소 이미지.
- 예상: 호스트와 같은 GPU 정보가 컨테이너 안에서 출력된다.
- **생각해 보기:** 이 이미지에는 `nvidia-smi`도 드라이버도 없다. 그런데 어떻게 실행될까? → Toolkit이 컨테이너를 만들 때 호스트의 드라이버 파일을 끼워 넣기 때문.

**5-1. 일부러 망가뜨려 보기**
```bash
docker run --rm nvidia/cuda:12.6.3-base-ubuntu24.04 nvidia-smi
```
(`--gpus all`을 **뺀다**)
- 예상: `nvidia-smi: executable file not found` 같은 오류. 왜 이번에는 실패할까? (Toolkit이 언제 개입하는지 생각해 본다.)

## 6. 이미지는 어디에 쌓였나 (확인하는 습관)

수십 GB짜리 vLLM 이미지를 받기 **전에** 반드시 확인한다.
```bash
docker info | grep -i -E "docker root dir|storage driver|driver-type"
sudo du -sh /var/lib/docker /var/lib/containerd
df -h / /data
```
- 질문 1: 이미지 레이어가 실제로 쌓인 곳은 어디인가? 루트(`/`)인가, `/data`인가?
- 질문 2: Docker 29의 `Storage Driver`가 `overlayfs`이고 `driver-type: io.containerd.snapshotter.v1`이면 이미지를 누가 저장할까? (힌트: **containerd의 `root`** 설정을 본다.)
- 확인 명령: `sudo containerd config dump | grep -E "^root|^state"`

**해결 (직접 해 보기).** 목표는 이미지 저장 위치를 `/data`로 옮기는 것이다.
```bash
sudo systemctl stop docker docker.socket containerd
sudo mkdir -p /data/containerd
sudo rsync -a /var/lib/containerd/ /data/containerd/
sudo cp /etc/containerd/config.toml /etc/containerd/config.toml.bak
echo 'root = "/data/containerd"' | sudo tee -a /etc/containerd/config.toml     # ← 파일 맨 앞에 두는 것이 안전하다 (아래 주의)
sudo systemctl start containerd docker
sudo containerd config dump | grep -E "^root"
docker run --rm --gpus all nvidia/cuda:12.6.3-base-ubuntu24.04 nvidia-smi
```
- **주의(TOML 문법):** `root = ...` 같은 최상위 키는 `[plugins...]` 같은 표(table) **앞**에 있어야 한다. `config.toml`이 현재 `disabled_plugins = ["cri"]` 한 줄뿐이면 뒤에 붙여도 되지만, 표가 생기면 뒤에 붙인 줄은 그 표의 키로 해석돼 효과가 없다. 안전하게 하려면 맨 앞에 넣는다.
  ```bash
  { echo 'root = "/data/containerd"'; cat /etc/containerd/config.toml.bak; } | sudo tee /etc/containerd/config.toml
  ```
- 확인: `sudo containerd config dump | grep -E "^root"` → `root = '/data/containerd'`. 이미지가 그대로 보이는지 `docker images`.
- **되돌리기:** `sudo cp /etc/containerd/config.toml.bak /etc/containerd/config.toml` 후 `sudo systemctl restart containerd docker`.
- **정리(선택):** 새 위치가 정상일 때만 옛 복사본을 지운다.
  ```bash
  sudo systemctl stop docker docker.socket containerd
  sudo find /var/lib/containerd -mindepth 1 -delete      # 또는: sudo sh -c 'rm -rf /var/lib/containerd/*'
  sudo du -sm /var/lib/containerd                         # 반드시 다시 확인: 거의 0이어야 한다
  sudo systemctl start containerd docker
  ```
  - **함정 (실제로 겪음):** `sudo rm -rf /var/lib/containerd/*`는 **아무것도 지우지 않는다.** `*`는 `sudo`가 아니라 **내 셸이** 먼저 펼치는데, `/var/lib/containerd`는 `drwx------ root`라 내 계정은 목록을 못 본다. 그래서 `*`가 글자 그대로 남고, `-f` 때문에 오류도 안 난다. 삭제 명령은 실행 후 `du`로 **결과를 확인**한다.

## 7. 보조 도구 (선택)
```bash
sudo apt-get install -y nvtop python3-venv python3-pip
nvtop     # GPU 사용률 화면, 끝내려면 q
```

## 8. 확인 질문 (스스로 답해 보기)

1. 컨테이너 안에 NVIDIA 드라이버를 설치하면 안 되는 이유는? (힌트: 컨테이너와 호스트가 공유하는 것)
2. `nvidia-smi`의 `CUDA Version: 13.0`과 이미지의 `cuda:12.6.3`은 충돌하나? 어느 쪽이 더 높아도 되나?
3. `--gpus all`을 빼면 왜 GPU가 안 보이나? Toolkit은 정확히 언제 개입하나?
4. 이미지가 `/data`가 아닌 곳에 쌓일 수 있었던 이유를 한 문장으로.
5. K8s(Step 3)에서도 같은 구조가 필요할까? 어디에 대응될까?

## 9. 완료 기준
- [ ] 5번을 직접 실행해 컨테이너 안에서 GPU가 보인다
- [ ] 5-1에서 일부러 실패시키고 이유를 설명할 수 있다
- [ ] 6번에서 이미지 저장 위치를 확인하고 `/data`로 옮겼다
- [ ] 확인 질문 5개에 답했다

## 10. 처음 상태로 되돌리기 (언제든 다시 시작하려면)

> 지우는 작업이다. **이 단계에서 설치한 것만** 지운다(NVIDIA 드라이버와 기존 패키지는 건드리지 않는다).
```bash
sudo apt-get purge -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin docker-ce-rootless-extras nvidia-container-toolkit nvidia-container-toolkit-base libnvidia-container1 libnvidia-container-tools nvtop
sudo rm -rf /etc/docker /etc/containerd /var/lib/docker /var/lib/containerd /data/containerd /data/models /data/lab
sudo rm -f /etc/apt/sources.list.d/docker.list /etc/apt/sources.list.d/nvidia-container-toolkit.list /etc/apt/keyrings/docker.asc /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
sudo groupdel docker; sudo apt-get update
```
- 이미 한 번 이 방식으로 롤백해서 깨끗한 상태로 돌아간 것을 확인했다(2026-10-30).

## 내 답변 (2026-10-30 완료)
1. 컨테이너에 드라이버를 설치하지 않는 이유 → "호스트와 중복 설치 및 자원 공유를 위해". *피드백:* 맞는 이점. 더 근본적인 이유는 드라이버가 **커널 모듈**이고 컨테이너는 호스트 커널을 공유하므로, 컨테이너 안의 라이브러리가 호스트 커널 모듈과 버전이 어긋나면 동작하지 않기 때문.
2. `nvidia-smi`의 CUDA Version vs 이미지 CUDA → "nvidia-smi의 CUDA Version이 더 높아도 된다". *피드백:* 맞음. 드라이버가 지원하는 **상한선**이고 이미지의 CUDA가 그 이하여야 한다. 반대면 `CUDA driver version is insufficient` 오류.
3. K8s에서의 대응 → "멀티 테넌트"라고 답함(질문과 어긋남). *정정:* 호스트=노드, docker/containerd=kubelet/containerd, nvidia 런타임=RuntimeClass, `--gpus all`=`resources.limits.nvidia.com/gpu`, 그리고 device plugin이 GPU를 자원으로 알림. 멀티 테넌트는 "한 노드 드라이버를 여러 파드가 공유"해서 생기는 Step 4의 주제.

## 내가 막힌 곳 / 새로 알게 된 것
- `sudo` 뒤의 `*`는 내 셸이 먼저 펼친다. 권한이 없으면 글자 그대로 남고 `-f`가 오류를 숨겨서 **아무것도 안 지워진다.** 삭제는 결과를 다시 확인한다.
- Docker 29는 이미지를 **containerd가 저장**한다. `data-root`만 바꾸면 소용없고 containerd `root`를 옮겨야 한다.
- `tee`는 파일에 쓰면서 화면에도 출력한다(그래서 `cat` 결과가 두 번 보였다).
- `$(ARCH)`는 파일에 글자 그대로 남는 apt의 자리표시자이고, 셸의 `$(...)` 명령 치환과 다르다.
- 그룹 변경은 **새 로그인부터** 적용된다(`id`와 `id 이름`의 차이).
- 이미지에는 `nvidia-smi`가 없고, 컨테이너를 만들 때 Toolkit이 호스트의 파일을 끼워 넣는다.
