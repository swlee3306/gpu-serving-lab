#!/bin/bash
# 호스트 MPS 데몬을 띄우고(기본 파이프 디렉터리 /tmp/nvidia-mps) 같은 합성 이웃 실험을 돌린 뒤 데몬을 내린다.
sudo nvidia-cuda-mps-control -d; sleep 2
pgrep -a nvidia-cuda-mps | head -3
bash /tmp/synth_mps.sh
echo "== MPS 서버 목록(종료 직전)"; echo get_server_list | sudo nvidia-cuda-mps-control 2>&1 | head -3
echo quit | sudo nvidia-cuda-mps-control; sleep 2
pgrep -a nvidia-cuda-mps || echo "mps processes: none"
