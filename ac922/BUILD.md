# Build (ppc64le / POWER9 + V100 sm_70)

Toolchain that produced the final lane (all versions are what the box actually has):

| Component | Version |
| --- | --- |
| CPU arch | ppc64le (POWER9, dual socket, 20C SMT4 each) |
| OS | RHEL8-family (Anolis/EL8 class) |
| GCC | 8.5.0 |
| CUDA | 12.4 (`nvcc` reports `cuda_12.4.r12.4`) |
| CMake | 3.26.5 |
| Python (server) | 3.11.13 |
| Engine pin | this checkout, `b08a70078` = strata **0.1.59** (`v0.2.13-nvfp4.1`) |

```bash
cd <repo>
cmake -B build -DCMAKE_BUILD_TYPE=Release -DCMAKE_CUDA_ARCHITECTURES=70
cmake --build build -j"$(nproc)"
./build/strata --version          # must print: strata 0.1.59
```

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
