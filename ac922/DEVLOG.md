# Development log — how this lane came to be

Condensed from a ~7,000-line operational runbook kept during 2026-11-08 → 11-16. Phases in order;
each entry is a *result*, not a diary.

## Phase A — hardware truth (11-08 → 11-09)

- AC922 (8335-GTW), dual POWER9 20C, 6× V100-16G SXM2. First bring-up revealed the machine was
  not what its labels said: **real DDR was 31.5 GiB** — the remaining ~90 GiB reported as online
  memory were V100 HBM windows mapped into the System Physical Address space. Trusting
  `/proc/meminfo` caused a chain of freezes (PHB link kills under sustained read *and* write,
  an EEH recovery path hitting `kernel BUG`, a 35-second-after-boot kernel Oops later pinned to
  NVIDIA RM adapter init, reproducible from a vmcore).
- Storage was the first-class reliability risk (8+ incidents on the delivered SATA disk);
  resolution: migrate to an NVMe (Intel PM1725a) with a planned, verified layout — not a raw
  whole-disk clone (partition-size mismatch would have been fatal), and the "work out of a RAM
  tmpfs" era ended once real capacity existed.
- Memory was upgraded in stages to **256 GiB of real DDR**. The box still reports ~341 GiB
  "online" in `/proc/meminfo` — the excess is V100 HBM windows mapped into the System Physical
  Address space (the same trap as the 31.5 GiB finding above), and no capacity plan ever counts
  it as RAM. Multi-channel behavior was verified by DIMM presence table + bandwidth probe. OS
  was reinstalled clean (EL8 family, the distro whose stock 4.18 kernel carries the NPU/ATS
  behavior the accelerator stack expects); GPU acceptance run after every hardware action.
- Ops rules that stuck: one variable per change; never prewarm disk caches by hand; swap policy
  written explicitly; SHM/VM tunings derived from real memory, scripted, not guessed.

## Phase B — reference stacks (11-09 → 11-12)

- **llama.cpp "Monster" tree** (IQ3_XXS + MTP): traced the fork's upstream base, re-implemented
  its delta as our own fork (`ac922-llama.cpp`), first champion geometry run; hybrid PLE admission
  formula reverse-verified from the shipped `libllama.so` strings and confirmed on-machine both
  ways. Established the 4-card boundary: 262K context needs `-ngl 47` + tight per-card traffic
  shaping and leaves < 1.2 GiB free — "memory fits, speed can't be bought" (path ② verdict).
- **1Cat-vLLM stack** studied as the paper anchor; **Strata int8 custom** (`eelgaev/Strata-AC922`
  lineage) evaluated; TurboQuant integration assessed statically and parked (quality cost).
- **DFlash2 drafter**: weight→pack conversion completed end-to-end (CPU-only), kept as a
  candidate drafter upgrade for the NVFP4 lane.

## Phase C — the NVFP4 lane (11-12 → 11-15)

- Model assets: `Maximilian228/Qwen3.8-Flash-Next-NVFP4-GPTQ-Strata` (17 files) downloaded via
  mirror lanes with resume + hardlink dedup discipline; one hard power cut left a sparse
  fake-complete 47 GiB file (right size, wrong bytes) — after that, **sha256 is the only accepted
  evidence**, sizes are not.
- Path ladder P1→P5 (each adopted after the previous baseline was taken): KV host-tier +
  block-key-pool analysis → 262K+MTP exploration → `llama.cpp-v100` baseline comparison →
  NVFP4 cold-cache OMLX-style mechanism (recompute→restore −97% via conversation-cache budget
  ×5.3, zero pp/tg/VRAM regression) → final-slot elasticity (short contexts fast, long contexts
  correct).
- Wave campaigns (A→K, plus T/U/V/X/Z series): paired same-boot A/B for every ticket. Notable
  outcomes: `--aux-cpus auto` wall −13% (today's cheapest big win); five-knob decode waves mostly
  neutral; TurboQuant and k8v4 rejected on exchange rate; QPN2 port cancelled with three
  independent pieces of evidence.
- **Four "mechanism self-sabotage" discoveries** — cases where our config, not the engine, was
  the ceiling: (1) `--batch-groups auto` silently produced groups-of-1, invalidating every
  earlier "batching doesn't help" verdict; (2) the pipelined window never ran MTP drafts until
  `STRATA_PIPED_MTP` was set; (3) pipelined rings never adapt the VRAM expert tier until
  `STRATA_PIPE_ADAPT_EVERY`; (4) a retention-default eviction loop (same failure independently
  hit and fixed upstream in the 1Cat line) was poisoning cold/warm asymmetry readings.
- **Concurrency revolution**: correct ring shape (`--batch 6 --batch-groups 3`) turned FIFO
  (agg/single = 0.98-1.00) into aggregate +74%; capacity rule N cards ↔ N full-window streams
  verified, over-subscription queues without crashing.
- **NUMA**: `NUMA placement FAILED ... remote reads` root-caused (file pages follow the reading
  CPU; the engine's pinned main thread parked all 48.8 GiB of PLE on node0); fixed with the
  mmap-hook arena binder (`numabind.so`) + PLE split-parking (`ple_park.sh 500`).
- Final Wave B baselines (B6/B4) taken as two boots with geometry as the only variable
  (`PERFORMANCE.md` §1); adopted state frozen into launcher + config with dated backups and a
  documented rollback.

## Phase D — accuracy campaign and reference-lane closures (11-16)

- Built a torch-free grading stack (13 tasks, official checkers where they exist) with four
  standing gates: selfcheck / bench-selftest / grader-parity / dryrun. Caught and fixed two
  caliber bugs (MC suites graded with reasoning enabled; a hand-written ifeval path that graded
  2 of 25 constraint classes — all historical ifeval scores voided and re-based on the official
  registry) and one data defect (cmmlu answer keys lost to a CSV column-name case change;
  rebuilt by row alignment).
- Full suites completed on N1: GSM8K 97.12 / MATH500 89.40 / HumanEval 96.95 (`ACCURACY.md`).
- Reference lanes closed with evidence rather than vibes: M1 after three falsified repair
  hypotheses (stop-sets, structure scan, prompt-shape strip); S1 after nine arms across two
  campaigns — and a post-mortem statistic showing the 8092 accuracy lane had *never* produced
  more than one token per request, i.e. "it was never up", not "it regressed". This produced the
  standing rule: **no suite runs before the three-probe lane gate passes** (`scripts/alive.py`).
- Cross-path panorama chart v2 published (`results/charts/`); accuracy panel currently carries
  the single citable stack — backfill scheduled behind the official-denominator anchor.

## Upstream backlog (for the next whole-version bump; policy: bump, never cherry-pick)

- Engine main has **167 commits** after our pin (`b08a70078`). Must-verify on bump: MMQ row-id
  table OOB fixes ×3 (our `STRATA_DENSE_MMQ` path), CC parked-conversation RAM return, Qwen3.6
  per-token session memory formula, NVFP4 down-kernel overdispatch, FP16 prefill expert-offset,
  plus re-adjudication of H2 (`--aux-cpus`) and the batch tickets under new defaults.
- Sister tree (`Strata-V100`, 2× V100-PCIE): `HC_Q8` opt-in (needs explicit env, not default),
  `TOPK_COMPACT` +3.5-4% end-to-end claim vs our 0.8% micro estimate — reprice via
  responsibility-field sieve before porting; `qpn2_nvfp4.cu` now exists in-family (ticket ⑧ cost
  dropped from cross-stack port to same-family move).
- 1Cat-vLLM line: host-tier eviction design (age+reuse, three watermarks, grace window) as the
  complete version of our decay-count idea; `draft_vocab.py` production shape (extra_ids merge +
  sha256 gate) for our T3; retention-default fix as the cautionary tale matching our own
  cold/warm asymmetry finding.

## Standing lessons (violated = wasted GPU-hours, each at least once)

1. Restart-isolation beats inference: effects < 4 ms/window never judged across boots.
2. Before any "dead" verdict, audit the mechanism combination; three of our deaths were
   config-layer self-harm, not engine limits.
3. A lane answering HTTP 200 is not a working lane; probe with real graded requests.
4. Hashes, not sizes; backups, not edits-in-place; bracketed pgrep patterns, not `pkill -f x.sh`
   (it kills your own session).
5. Card count buys concurrency; stage count and expert streaming buy speed — do not spend on the
   wrong axis.
