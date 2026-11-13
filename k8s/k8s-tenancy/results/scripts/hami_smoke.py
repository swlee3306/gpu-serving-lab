# HAMi-core 스모크 테스트: (A) 로드와 메모리 표시, (C) 행렬 곱 처리량 10초, (B) 메모리 할당 한도.
import subprocess, sys, time, torch
tag = sys.argv[1]
dev = torch.device("cuda")
free, total = torch.cuda.mem_get_info()
print(f"[{tag}] torch mem_get_info: free={free/2**30:.2f}GiB total={total/2**30:.2f}GiB", flush=True)
try:
    out = subprocess.run(["nvidia-smi", "--query-gpu=memory.total,memory.used", "--format=csv,noheader"], capture_output=True, text=True, timeout=30).stdout.strip()
    print(f"[{tag}] nvidia-smi: {out}", flush=True)
except Exception as e:
    print(f"[{tag}] nvidia-smi failed: {e}", flush=True)
a = torch.randn(8192, 8192, dtype=torch.float16, device=dev); b = torch.randn(8192, 8192, dtype=torch.float16, device=dev)
for _ in range(3): torch.matmul(a, b)
torch.cuda.synchronize()
t0 = time.time(); n = 0
while time.time() - t0 < 10:
    torch.matmul(a, b); n += 1
    if n % 5 == 0: torch.cuda.synchronize()
torch.cuda.synchronize(); dt = time.time() - t0
print(f"[{tag}] gemm: {2 * 8192**3 * n / dt / 1e12:.1f} TFLOP/s over {dt:.1f}s", flush=True)
del a, b; torch.cuda.empty_cache()
chunks = []; tot = 0
try:
    while tot < 13 * 2**30:
        chunks.append(torch.empty(256 * 2**20, dtype=torch.uint8, device=dev)); tot += 256 * 2**20
        torch.cuda.synchronize()
    print(f"[{tag}] alloc: {tot/2**30:.1f}GiB allocated without error", flush=True)
except Exception as e:
    print(f"[{tag}] alloc: stopped after {tot/2**30:.2f}GiB -> {type(e).__name__}: {str(e)[:90]}", flush=True)
