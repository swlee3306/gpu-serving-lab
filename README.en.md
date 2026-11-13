[한국어](README.md) | **English**

# gpu-serving-lab

Hands-on notes, scripts and raw measurements from building a small **LLM serving platform on a single consumer GPU** (RTX 4070 Ti SUPER, 16 GB), step by step:

**single-GPU serving → load testing → optimization → observability → Kubernetes GPU node → multi-tenancy**

Every number in this repository was measured on the hardware described below. Most experiments were run **once** on **one GPU**; the step notes say so wherever it matters, and they separate *measured*, *estimated* and *not verified*.

> The step notes are written in Korean (they are my working lab notebook). This is the English version of the README; the Korean original is [README.md](README.md).

---

## Why

GPUaaS / LLMaaS platforms have to answer a few hard questions: how many users one GPU can serve at what latency, what to watch to know it is saturated, how a GPU becomes a schedulable Kubernetes resource, and whether tenants that share a GPU are actually isolated. I wanted measured answers on real hardware rather than vendor claims, so each step ends in a table and a list of what is still unknown.

## Environment (home lab)

| Role | Hardware / software (as recorded in the notes) |
|---|---|
| GPU server | Ubuntu 24.04, **RTX 4070 Ti SUPER 16 GB** (GeForce: no MIG, no vGPU), 12 cores / 30 GB RAM, NVIDIA driver 580, Docker + NVIDIA Container Toolkit 1.20.1 |
| Serving | vLLM `vllm/vllm-openai` 0.32.0 (0.33.0 inside Kubernetes), OpenAI-compatible API |
| Models | Qwen2.5-3B-Instruct (BF16), Qwen2.5-7B-Instruct AWQ / GPTQ-Int4, Qwen3.5-4B, Qwen3.5-9B AWQ 4-bit |
| Observability | dcgm-exporter 4.6.1-4.8.4, Prometheus v3.15.1, Grafana 13.3.0 (Docker, bound to 127.0.0.1) |
| Kubernetes | k3s v1.36.5+k3s1: control plane in an OpenStack VM on a separate home server, GPU server joined as agent; NVIDIA k8s-device-plugin v0.20.1 (no GPU Operator) |
| Extra | Mac mini M4 32 GB (kubectl client; MLX comparison in `harness/`) |

Host names and private addresses have been generalized (`gpu-server`, `192.168.1.x`, `10.0.0.x`).

## Results at a glance

All values are copied from the step notes; follow the link for conditions and caveats.

**1. Single-GPU serving and saturation** ([step-1b](docs/step-1b-load-testing.md)), Qwen2.5-3B, identical 128-token requests (idealized, prefix cache hits):

| Concurrency | Throughput (tok/s) | Per-user tok/s | TTFT p95 |
|---|---|---|---|
| 1 | 91.4 | 91.4 | 15 ms |
| 64 | 4,853 | 76.6 | 88 ms |
| 256 | 10,506 | 41.7 | 268 ms |
| 512 | 10,677 | 21.0 | 3,202 ms (256 running + 256 waiting) |

**2. Realistic load** (Poisson arrivals, mixed prompt/answer lengths, `bench/bench_real.py`): throughput saturates around **1,300 tok/s, about 8× lower** than the idealized benchmark; the limit becomes the **KV cache (99.6 %)**, and the SLO (TTFT p95 ≤ 300 ms, ≥ 30 tok/s per user) holds up to roughly **5–10 req/s**.

**3. Model / config comparison at 4 req/s** ([step-1d](docs/step-1d-summary-table.md), [step-1c](docs/step-1c-optimization.md)):

| Config | KV slots (tokens) | Throughput tok/s | TTFT p95 | Peak KV use | Accuracy (1,257 MC questions) | SLO-passing arrival rate |
|---|---|---|---|---|---|---|
| Qwen2.5-3B fp16 KV | 177,120 | 410 | 308 ms | 3.2 % | 54.5 % | ~8 req/s |
| Qwen2.5-3B fp8 KV | 332,640 | 411 | 279 ms | 2.4 % | ~45 %* | ~8 req/s |
| Qwen2.5-7B GPTQ | 115,296 | 427 | 831 ms | 11.4 % | 67.0 % | ~2 req/s (borderline) |
| Qwen2.5-7B AWQ | 115,184 | 426 | 838 ms | 10.1 % | 64.4 % | ~2 req/s |
| Qwen3.5-4B bf16 | 56,971 | 381 | 505 ms | 50.0 % | 69.2 % | ~2 req/s |
| Qwen3.5-9B AWQ 4-bit | 56,226 | 401 | 982 ms | 77.3 % | 75.5 % | fails at 2 req/s (336 ms) |
| Qwen3.5-9B FP8 | – | – | – | – | – | does not start on 16 GB |

\* fp8-KV accuracy is from the 200-question set. Findings: `nvidia-smi` GPU utilization read 100 % for every config, so it is useless for capacity; KV usage, queue length and TTFT are the real signals. Uncalibrated fp8 KV cost 8.5 pp accuracy (3B) for no gain inside the SLO range. A shared 1,200-word system prompt with prefix caching turned TTFT p50 from 11.1 s into 128 ms at 4 req/s.

**4. Observability** ([step-2](docs/step-2-observability.md)): Grafana dashboard (11 panels) and 8 Prometheus alert rules, including a "VRAM held while GPU is idle" rule designed not to fire on an idle-but-loaded vLLM. Dashboard averages, VRAM and KV usage matched the Step-1 measurements; short peaks (power, running requests) are under-reported with a 15 s scrape interval.

**5. Kubernetes GPU node** ([step-3](docs/step-3-k8s-gpu-node.md)):
- GPU exposed as `nvidia.com/gpu` via the device plugin; pods without `runtimeClassName: nvidia` crash with `Failed to infer device type`.
- Time-slicing (`replicas: 4`) on a GeForce card: two vLLM pods, each alone ~2,400 tok/s, p50 1.60 s; **together 1,157 / 1,154 tok/s, p50 3.40 s**. Total throughput is unchanged and each pod's latency doubles: time-slicing shares the GPU but does not isolate tenants.
- Control-plane VM outage: running pods keep serving, but a restarted container could not start for more than 5 minutes because CoreDNS lived only on the control-plane VM. Running two CoreDNS replicas with `internalTrafficPolicy: Local` kept 93/93 DNS lookups succeeding during a 90 s outage, and vLLM recovered in about 55 s.

**6. Multi-tenancy** ([step-4](docs/step-4-multitenancy.md), [research](docs/research-multitenancy.md)):
- Namespace + ResourceQuota (`requests.nvidia.com/gpu`) + NetworkPolicy + RBAC + Pod Security `baseline`: quota overruns are rejected at admission, cross-tenant traffic is blocked, tenants cannot lift their own limits, and `hostPath` escapes are refused.
- Noisy neighbor: with time-slicing, the victim's p50 went **1.44 s → 3.06 s (≈2.1×)** and throughput dropped ~46 %, even though both tenants were within quota. Neighbor load from 1 to 64 concurrent requests made no difference. MPS on GeForce worked but gave about the same ≈2× slowdown.
- Shared engine + vLLM `--scheduling-policy priority`: victim p50 **5.88 s (fcfs) → 2.51 s**, with neighbor throughput unchanged (1,311 → 1,308 tok/s). Measured in a queue-forming setup (`--max-num-seqs 16`).
- Prefix-cache timing on a shared engine: a repeated prompt answered in 25 ms instead of 340 ms, which reveals that another tenant sent it recently; a per-tenant `cache_salt` removed this signal.
- HAMi-core per-container limits loaded on GeForce/CUDA 13, but **did not apply to vLLM with CUDA graphs** (only with `--enforce-eager`).

**7. Side experiment: local LLMs as verification assistants** ([harness/RESULTS.md](harness/RESULTS.md)): 24 synthetic test-failure cases. Qwen3.5-4B/9B classify 88–92 % correctly, but both pass "partial run / stale evidence" as `ok`. Also compares GPU and Mac mini (MLX) speed and energy.

## Measured vs planned

| Step | Status |
|---|---|
| 0 Tooling (Docker, NVIDIA Container Toolkit) | Done |
| 1 Single-GPU serving, load test, optimization, summary table | Done (mostly single runs) |
| 2 Observability (DCGM, vLLM metrics, Prometheus, Grafana, alert rules) | Done. Alertmanager notification delivery **not connected** |
| 3 k3s GPU node, time-slicing, failure tests | Done. Rolling update / rollback **not tested** |
| 4 Multi-tenancy | Done for quota, ingress NetworkPolicy, RBAC, Pod Security, noisy neighbor, MPS, priority, cache_salt, HAMi-core. **Not done:** egress policy, `PriorityClass`, vCluster comparison, tenant onboarding automation, 3+ tenants |
| 5 Usage metering / billing + GPUaaS API | **Planned only** |
| 6 Failure and recovery-time table | **Planned only** (some failure tests are in Step 3) |
| 7 KVM/OpenStack GPU passthrough write-up | **Planned only** |

The original plan with these checkboxes is in [docs/plan.md](docs/plan.md).

## Repository map

```
docs/
  plan.md                      original step plan (status as of 2026-11-03)
  step-0-tooling.md            Docker + NVIDIA Container Toolkit from scratch
  step-1-single-gpu-serving.md first vLLM server, VRAM budget, failure drills
  step-1b-load-testing.md      concurrency sweep, saturation, realistic load, 7B AWQ, fp8 KV
  step-1c-optimization.md      option search, 1,257-question accuracy eval, prefix cache, Qwen3.5
  step-1d-summary-table.md     one-page VRAM / throughput / power table, max-model-len
  step-2-observability.md      dcgm-exporter + Prometheus + Grafana, alert rules, cross-check
  step-3-k8s-gpu-node.md       k3s control plane VM + GPU worker, device plugin, time-slicing, failures
  step-4-multitenancy.md       quota, NetworkPolicy, RBAC, PSA, noisy neighbor, MPS, priority, HAMi-core
  research-multitenancy.md     literature/docs review + follow-up experiments + AI review/debate log
  serving-explained.html       one-page visual explainer of LLM serving terms (Korean)
bench/
  bench.py                     closed-loop concurrency benchmark (stdlib only, streaming)
  bench_real.py                open-loop Poisson load generator with mixed lengths and SLO check
  eval_mc.py                   multiple-choice accuracy eval against an OpenAI-compatible server
  fetch_evalset.py             downloads the evaluation sets (evalset.json 200 / evalset_big.json 1,257 MC questions,
                               KMMLU CS + MMLU subsets) from the Hugging Face Hub; the questions are not stored here
scripts/
  run_profile.sh               start vLLM, record startup KV numbers + nvidia-smi during fixed load
  run_candidate.sh, run_candidate2.sh   eval x2 + load sweep for one model/config
k8s/
  k3s-lab/                     rollback + verify scripts for the k3s experiment
  k8s-tenancy/                 tenant manifests, test scripts, results/ (raw outputs, AI review notes)
harness/                       local-LLM verification-assistant eval (harness/, src/, tests/, results/)
```

## Reproducing

Prerequisites: a Linux host with an NVIDIA GPU, driver, Docker and the NVIDIA Container Toolkit (see [step-0](docs/step-0-tooling.md)). The Python tools use the standard library only.

```bash
# 1) start a server (same flags as the notes)
docker run -d --gpus all --ipc=host --name vllm \
  -v /data/models/hf:/root/.cache/huggingface -p 127.0.0.1:8000:8000 \
  vllm/vllm-openai:latest --model Qwen/Qwen2.5-3B-Instruct \
  --max-model-len 8192 --gpu-memory-utilization 0.85

# 2) idealized concurrency sweep
python3 bench/bench.py --levels 1,4,16,64 --max-tokens 128

# 3) realistic open-loop load with SLO check (TTFT p95 <= 300 ms, >= 30 tok/s per user)
python3 bench/bench_real.py --rates 5,10,20 --duration 30

# 4) accuracy (first download the evaluation sets: they are not part of this repository)
python3 bench/fetch_evalset.py
python3 bench/eval_mc.py --model Qwen/Qwen2.5-3B-Instruct --set bench/evalset_big.json --out /tmp/eval.json
```

`scripts/*.sh` automate one model/config end to end. They assume the layout used on the lab server (`/data/lab/bench` for the tools, `/data/models/hf` for the model cache), so copy `bench/*` there or edit `B=` first. Kubernetes manifests in `k8s/k8s-tenancy/` are written for k3s with the NVIDIA device plugin and a node labelled `gpu=true`; `teardown.sh` removes everything they create. `put.sh` and the `harness/harness/orchestrate*.sh` / `power_*.sh` helpers use SSH aliases (`gpu-server`, `macmini`, `openstack-host`) from my lab, so adjust them to your hosts.

Not in this repo: the Prometheus/Grafana config files and dashboard JSON (they are described in step-2 but lived on the server), screenshots, model weights, and the raw `/data/lab/bench/*.json` outputs of Step 1. The Step-4 helper `dose1.sh` mentioned in the notes was not preserved.

## Data notice

The evaluation questions are **not** included in this repository. `bench/fetch_evalset.py` downloads them from the Hugging Face Hub and writes `bench/evalset.json` and `bench/evalset_big.json` (git-ignored) in the same format and order as the sets used for the measurements. The script checks the SHA-256 of the files it writes against the hashes of the measured sets, so you can tell whether the upstream data has changed.

| Source | License | Used |
|---|---|---|
| [KMMLU](https://huggingface.co/datasets/HAERAE-HUB/KMMLU) (HAE-RAE, Son et al. 2024) | CC BY-ND 4.0 | Computer Science test, first 600 rows |
| [MMLU](https://huggingface.co/datasets/cais/mmlu) (Hendrycks et al. 2021) | MIT | test split of six subjects (machine learning, computer security, college and high-school computer science, electrical engineering, college mathematics) |

Please cite the datasets and follow their licenses. The MIT license of this repository covers the code and notes only.

## About the notes

The notes were written while working with AI coding assistants (Claude Code, Codex CLI). Commands were typed and verified by me. AI reviews and debates of the conclusions are kept, including the points where the assistant was wrong ([research §9–11](docs/research-multitenancy.md), `k8s/k8s-tenancy/results/*codex*.md`). Some notes mention a companion blog series ("홈랩 일지").

---

The Korean README ([README.md](README.md)) is the main version.

## License

Code and notes: [MIT](LICENSE) © 2026 swlee3306. Evaluation questions: see *Data notice*.
