# dawn

Checkpoint/restore tool for serverless GPU inference. Wraps CRIU + NVIDIA cuda-checkpoint with sane defaults derived from real testing.

## Why

Setting up CRIU + cuda-checkpoint correctly involves a dozen gotchas (io_uring sysctl, the right CRIU flag set, the CUDA plugin path, /dev/shm semaphore handling, finding the actual CUDA PID inside vLLM's process tree). `dawn` hides all of that.

## Install

```bash
pip install -e .

# One-time system setup (installs CRIU 4.0, cuda-checkpoint, configures kernel)
sudo dawn setup
```

## Quick Start

```bash
# Check if your system is ready
dawn doctor

# List CUDA processes
dawn status

# Checkpoint a running vLLM process
sudo dawn dump --vllm --output /dev/shm/ckpt-llama3

# Restore it (after the original process is gone)
sudo dawn restore /dev/shm/ckpt-llama3 --wait-port 8000

# Compress for cross-node transfer (~88% smaller)
dawn compress /dev/shm/ckpt-llama3 /mnt/nfs/llama3.lz4

# Restore from a compressed archive on a new node
sudo dawn restore /mnt/nfs/llama3.lz4 --decompress-to /dev/shm/ckpt
```

## Library Usage

```python
from dawn import Checkpoint, gpu, process

pid = process.find_vllm_pid()
result = Checkpoint.dump(pid=pid, output_dir="/dev/shm/ckpt")
print(result.duration_ms, result.size_bytes)

restored = Checkpoint.restore(checkpoint_dir="/dev/shm/ckpt", pre_warm=True)
print(restored.duration_ms, restored.vram_mb)
```

## Requirements

- Linux x86_64
- NVIDIA driver >= 550
- Bare-metal access or container with `CAP_SYS_PTRACE`, `CAP_CHECKPOINT_RESTORE`, `CAP_SYS_ADMIN`
- Permissive seccomp (RunPod containers won't work — use bare metal providers)
