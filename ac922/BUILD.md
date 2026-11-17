# Build (ppc64le / POWER9 + V100 sm_70)

Toolchain that produced the final lane (all versions are what the box actually has):

| Component | Version |
| --- | --- |
| CPU arch | ppc64le (POWER9, dual socket, 20C SMT4 each) |
| OS | RHEL8-family (Anolis/EL8 class) |
| GCC (system) | 8.5.0 — **not** usable as the CUDA host compiler, see below |
| GCC (build) | 12.2.1 from `gcc-toolset-12` (C, C++ and nvcc's `-ccbin`) |
| CUDA | 12.4 (`nvcc` reports `cuda_12.4.r12.4`) |
| CMake | 3.26.5 |
| Python (server) | 3.11.13 |
| Engine release | strata **0.1.59** (`v0.2.13-nvfp4.1`) + the AC922 commits on top of it (`git log --oneline`) |

```bash
cd <repo>
T=/opt/rh/gcc-toolset-12/root/usr
cmake -B build -DCMAKE_BUILD_TYPE=Release -DCMAKE_CUDA_ARCHITECTURES=70 \
      -DCMAKE_C_COMPILER=$T/bin/cc -DCMAKE_CXX_COMPILER=$T/bin/c++ \
      -DCMAKE_CUDA_COMPILER=/usr/local/cuda-12.4/bin/nvcc \
      -DCMAKE_CUDA_HOST_COMPILER=$T/bin/c++
cmake --build build -j"$(nproc)"
./build/strata --version          # must print: strata 0.1.59
```

This exact command is what builds the shipping lane; it was re-verified on 2026-11-17 from a clean
tree (`BUILD-EXIT=0`, 0 errors, 176-core box). Two things in it are load-bearing on this platform:

- **the toolset has to be pinned for nvcc too.** The engine is C++20 (`CMAKE_CXX_STANDARD 20`). With
  the default host compiler, nvcc feeds those sources to the system GCC 8.5 and the build dies
  (`namespace "std" has no member "is_same_v"`, then ~100 `redefinition of ...` inside
  `/usr/include/c++/8/bits/random.tcc`, whose inline variables GCC 8 cannot compile at `-std=c++20`).
  `-DCMAKE_CUDA_HOST_COMPILER` alone is not enough once a build directory exists - the CUDA compiler
  identification is cached, so a build directory that already saw GCC 8.5 keeps it: configure a fresh
  build directory (or export `PATH=$T/bin:$PATH` before the first configure).
- **CUDA 12.4 has no `cuda_fp4.h`.** `src/prefill/mmq_nvfp4_a44.cu` (Blackwell-only W4A4 path) guards
  that include behind `BLACKWELL_MMA_AVAILABLE`, and the vendored ggml carries `ggml/src/ggml-cpu/arch/powerpc/quants.c`
  for the same reason (ggml-cpu lists it on ppc64le unconditionally). Both are described in
  `third_party/strata-ggml/UPSTREAM.md` (edit 4) and `DEVLOG.md`.

Notes specific to this platform:

- `sm_70` (Volta) is the only CUDA architecture used. The engine's Volta opt-in kernels are
  documented upstream in `docs/NVIDIA_V100.md`; the final lane enables the bit-exact ones
  (`STRATA_SM70_TABLE`, `STRATA_FUSE_DECODE`, `STRATA_ARGMAX_ROWS_SM70`) — see `DECISIONS.md`.
- The NUMA placement helper is a standalone `LD_PRELOAD` shim, compiled separately:
  ```bash
  gcc -O2 -shared -fPIC ac922/scripts/numabind.c -o numabind.so -ldl
  ```
- Python serving deps: `pip install -r requirements.txt` (plus `requirements-nvfp4-local.txt`
  if you regenerate assets locally). The eval harness in `ac922/eval/` is deliberately
  **torch-free** — graders run on plain stdlib + a vendored `nltk`/`pylibs` pair.
- ggml is vendored frozen under `third_party/strata-ggml` (see `third_party/ggml/VERSION.txt`);
  no download step is required or performed.

## Multimodal encoder (`strata-vision`, optional but ship default)

The vision tower weights are **not compiled here** — they are `mmproj-F16.gguf` from the model
download (see `RUN.md`). What you build is the CPU embedding helper `strata-vision`, which links
the ggml/mtmd stack pinned to this engine's llama.cpp commit:

```bash
cmake -B build-vision -DCMAKE_BUILD_TYPE=Release -DGGML_CUDA=OFF -DLLAMA_CURL=OFF
cmake --build build-vision -j --target strata-vision
# must be CPU-only: the process must not hold a CUDA context (vision.gpu=false is the adopted shape)
```

`-mcpu=power9` is picked automatically by ggml's native detection on this box (it implies VSX —
an explicit `-mvsx` is redundant, and do not expect POWER10/11 paths: this compiler's autovectorizer
targets ISA 2.07 + P9 scheduling).
