#!/bin/bash
LIB=/data/lab/hami-src/src/build/libvgpu.so
IMG=vllm/vllm-openai:latest
run() { # tag [env args...]
  tag=$1; shift
  docker run --rm --gpus all --entrypoint bash -v $LIB:/libvgpu.so:ro -v /tmp/hami_smoke.py:/s.py "$@" $IMG -c "mkdir -p /tmp/vgpulock; python3 /s.py $tag" 2>&1 | grep -E "^\[|Error|error|Traceback" | head -8
}
echo "== baseline (LD_PRELOAD 없음)"; run baseline
P="-e LD_PRELOAD=/libvgpu.so"
echo "== hami, 한도 없음"; run hami-nolimit $P
echo "== hami, 메모리 4g"; run hami-mem4g $P -e CUDA_DEVICE_MEMORY_LIMIT=4g
echo "== hami, 메모리 4g + SM 25 (기본 정책)"; run hami-sm25-default $P -e CUDA_DEVICE_MEMORY_LIMIT=4g -e CUDA_DEVICE_SM_LIMIT=25
echo "== hami, 메모리 4g + SM 25 (force)"; run hami-sm25-force $P -e CUDA_DEVICE_MEMORY_LIMIT=4g -e CUDA_DEVICE_SM_LIMIT=25 -e GPU_CORE_UTILIZATION_POLICY=force
echo "== hami, 메모리 4g + SM 50 (force)"; run hami-sm50-force $P -e CUDA_DEVICE_MEMORY_LIMIT=4g -e CUDA_DEVICE_SM_LIMIT=50 -e GPU_CORE_UTILIZATION_POLICY=force
