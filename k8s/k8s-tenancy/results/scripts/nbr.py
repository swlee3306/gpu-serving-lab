# 합성 이웃: idle | copy | gemm, duty(0~1), 지속 시간(초). 통계는 마지막에 출력한다.
import sys, time, torch
mode, duty, secs = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])
dev = torch.device("cuda")
period = 0.1
if mode == "idle":
    x = torch.empty(256 * 1024 * 1024, dtype=torch.float32, device=dev); torch.cuda.synchronize()
    print("idle: context held", flush=True); time.sleep(secs); sys.exit(0)
if mode == "copy":
    x = torch.empty(256 * 1024 * 1024, dtype=torch.float32, device=dev); y = torch.empty_like(x)   # 각 1GiB
    unit_bytes = 2 * x.numel() * 4                                                                  # 읽기 + 쓰기
    def work(): y.copy_(x)
else:
    a = torch.randn(8192, 8192, dtype=torch.float16, device=dev); b = torch.randn(8192, 8192, dtype=torch.float16, device=dev)
    unit_flops = 2 * 8192 ** 3
    def work(): torch.matmul(a, b)
for _ in range(3): work()
torch.cuda.synchronize()
t_end = time.time() + secs; units = 0; busy = 0.0
while time.time() < t_end:
    t0 = time.time()
    while time.time() - t0 < duty * period:
        work(); units += 1; torch.cuda.synchronize()
    busy += time.time() - t0
    rest = period - (time.time() - t0)
    if rest > 0: time.sleep(rest)
wall = secs
if mode == "copy": print(f"copy duty={duty}: 평균 {units * unit_bytes / wall / 1e9:.0f} GB/s (벽시계 기준), 가동 중 {units * unit_bytes / busy / 1e9:.0f} GB/s", flush=True)
else: print(f"gemm duty={duty}: 평균 {units * unit_flops / wall / 1e12:.1f} TFLOP/s (벽시계 기준), 가동 중 {units * unit_flops / busy / 1e12:.1f} TFLOP/s", flush=True)
