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
| Engine release | strata **0.1.61** (`v0.2.15-nvfp4.2`) + the AC922 commits on top of it (`git log --oneline`) |

```bash
cd <repo>
T=/opt/rh/gcc-toolset-12/root/usr
cmake -B build -DCMAKE_BUILD_TYPE=Release -DCMAKE_CUDA_ARCHITECTURES=70 \
      -DCMAKE_C_COMPILER=$T/bin/cc -DCMAKE_CXX_COMPILER=$T/bin/c++ \
      -DCMAKE_CUDA_COMPILER=/usr/local/cuda-12.4/bin/nvcc \
      -DCMAKE_CUDA_HOST_COMPILER=$T/bin/c++ \
      -DSTRATA_ENABLE_CUDA=ON -DSTRATA_BUILD_TESTS=OFF \
      -DSTRATA_NATIVE_EXPERTS=ON -DSTRATA_Q6K_EXPERTS=ON -DSTRATA_EXPERIMENTAL_SM60=ON \
      -DSTRATA_MMQ_KQUANTS=ON -DSTRATA_MMQ_TQ_OVERLAY=ON -DSTRATA_AMD_COMMUNITY_TUNING=ON
cmake --build build -j"$(nproc)"      # production is live: nice -n 19 ionice -c3
./build/strata --version          # must print: strata 0.1.61
```

Seven of those eight `STRATA_*` flags are already `option(... ON)`; they are spelled out because
`STRATA_EXPERIMENTAL_SM60` is **not** an `option()` - it is a plain variable read by
`cmake/strata_gpu_engine.cmake:14`, so omitting it silently drops the `STRATA_EXPERIMENTAL_SM60=1`
compile definition from `strata_core`. The shipping `build/CMakeCache.txt` has it `ON`, and the
`CUDA_ARCHITECTURES`/flag set above is exactly what the production cache carries (verified key by
key on 2026-11-17).

This exact command is what builds the shipping lane. It has been re-verified twice on 2026-11-17:
once from a clean upstream tree (`BUILD-EXIT=0`, 0 errors, 176-core box) and once from this delivery
tree at `b68aaf6aca` into a separate `build-new/` (`BUILD-EXIT=0`, 0 errors, ~10 min at `-j 64` while
the 4-card lane stayed live). The switch note in `DEVLOG.md` records why the second one mattered: the
production `build/` predates this repository - its `CMakeCache.txt` still points at the pre-delivery
source tree, so nothing in the lane had actually been compiled from the shipped sources until then.

Two things in the command are load-bearing on this platform:

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
T=/opt/rh/gcc-toolset-12/root/usr
cmake -S tools/vision -B build-vision -DCMAKE_BUILD_TYPE=Release -DGGML_CUDA=OFF -DLLAMA_CURL=OFF \
      -DCMAKE_C_COMPILER=$T/bin/cc -DCMAKE_CXX_COMPILER=$T/bin/c++
cmake --build build-vision -j --target strata-vision
# must be CPU-only: the process must not hold a CUDA context (vision.gpu=false is the adopted shape)
```

**`-S tools/vision` is not optional.**  The vision program is its own CMake project (`strata_vision`),
so `cmake -B build-vision` from the repository root silently configures the *engine* tree and the
build then dies with `No rule to make target 'strata-vision'` - which is how it was rediscovered the
hard way on 2026-11-19 while bumping to `0.2.15-nvfp4.2`.  `CMAKE_HOME_DIRECTORY` in the shipping
`build-vision/CMakeCache.txt` reads `.../tools/vision`, which is the proof of the shape.

`LLAMA_DIR` defaults to the vendored `third_party/strata-llama` (llama + mtmd + ggml at the engine's
pin, nothing downloaded), with an override "kept for two releases".  **On ppc64le the vendored path
configures since the `0.2.15-nvfp4.2` bump**: our ppc64le shim (the forwarding `quants.c` plus
`-U__POWER9_VECTOR__`, originally `a2f280b16` for `third_party/strata-ggml`) has been replicated into
`strata-llama`, because the vision project compiles its own second copy of ggml.  `check_vendored`
tracks both rows (1 edited / 1 added, pin `3cf03257f`); no `LLAMA_DIR` override is needed, and the
plain command above is what produced the shipping `build-vision` on 2026-11-19 (H.8).

The toolset pin is load-bearing here as well, for a different reason than the engine build.
`ggml-backend-reg.cpp` uses `std::filesystem`, and GCC 8.5 keeps that in a separate `libstdc++fs`:
configured with the system compiler every compile step passes and only the link fails, with
`undefined reference to std::filesystem::status(...)` / `... directory_iterator::increment(...)`
and `collect2: error: ld returned 1 exit status`. GCC 12 folded it into libstdc++, which is what the
shipping `build-vision` is configured with. Re-verified 2026-11-17.

`-mcpu=power9` is picked automatically by ggml's native detection on this box (it implies VSX —
an explicit `-mvsx` is redundant, and do not expect POWER10/11 paths: this compiler's autovectorizer
targets ISA 2.07 + P9 scheduling).

Since `1f62fbc6d` this no longer covers only ggml: **`cmake/strata_ppc_cpu.cmake` lifts the whole
build to the machine's POWER generation.**  RHEL's gcc-toolset-12 bakes `-mcpu=power8 -mtune=power8`
into the compiler itself, and the engine targets (the CPU half of every decode window: orchestration,
exchange, quantizers) carried no `-mcpu` at all, so they silently compiled at the POWER8 floor.  The
module - `STRATA_PPC_CPU=auto|power8|power9|none`, default auto - reads the generation from
`/proc/cpuinfo` (`cpu : POWER9` / pvr 004e), probes whether the compiler default already defines
`_ARCH_PWR9` (a toolchain that is already correct adds nothing), verifies each flag with
check_cxx_compiler_flag, and applies `-mcpu/-mtune` to CXX, C and the nvcc host pass (`-Xcompiler`).
It is a no-op off ppc64le and never reaches past what the hardware proves (no POWER10 MMA).
Codegen changes still go through the quality gate (det x3 + quant_quality + divprobe) before adoption,
and after a workspace reset the build directory must be reconfigured, not just recompiled - the
November link failure (`q80p_*` undefined after a hard-reset) is standing lesson 20 in `DEVLOG.md`.
