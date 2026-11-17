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

## Phase E — the "decode 20 / prefill 500" reconciliation (11-16 evening)

- Closed the gap between published numbers (tg 53.75 on 4 cards, tg 66.0 on 6, pp 7k) and the
  operator's felt speed (~20 / ~500) without touching the running lane.  The felt number is a
  **mixture**, not a floor: classifying 8,300 self-report turns by how deep their prefix reuse went,
  57.7% relay (mean prefill 0.34 s), 13.8% land partially (15.8 s), **28.5% start from nothing
  (20.7 s)** — and that last class carries 80.6% of all re-read tokens (222 Mtok).  The split is
  bimodal per length band: in the 240-250K band, 48% of turns relayed *at 249,829 tokens* while 50%
  re-prefilled 244K tokens at 43 s each.
- Root cause reproduced with a single flag: parked-conversation **slots**, not RAM.  Eight
  concurrent conversations on the dev lane: `--conversation-cache-slots 8` relayed 16/16
  continuations (gap = the new turn); the shipped default of 4 re-prefilled 16/16 from zero, for
  ~0.1 GiB of RSS.  In production 3,529 of 3,583 conversation-cache eviction events happen at
  exactly `parked=16` (its slot ceiling) while its RAM budget is nowhere near binding
  (footprint p90 21.8 GiB, max 53.6 GiB of 128 GiB; 0.0% of events reach 97% of budget).
- Priced the checkpoint grid over 10 arms with 10/10 pre-registered predictions:
  `spacing = ceil(prompt-cache-every / chunk) x chunk`, and a continuation re-reads its own prefix
  length modulo that spacing.  This reverses both intuitive fixes: setting `--prompt-cache-every`
  to <= chunk halves the spacing for ~0% cold-prefill cost, while shrinking `--prefill` alone is
  strictly dominated (it pays up to -56% cold prefill and cannot push spacing below 16384).
- Request shape dominates geometry: the same ~57K content delivered as one user message resumed
  12,670 tokens deep (9.3 s); delivered as a 12-turn messages array it relayed the whole prefix
  (3.5 s) -- identical engine, identical config.
- Dead end recorded so nobody re-opens it: `--batch-mtp` would remove the 333 "no per-slot draft
  K/V, so the slot cannot park" events, but its own help bars it together with `--batch-groups`.
  It is the price of the adopted pipelined ring, not a missed switch.
- Tools: `eval/resume_probe.py`, `eval/tg_short.py`, `eval/cap_probe.py` (+ METHODOLOGY 9).

## Phase F — production verification of the capacity fix (11-16 late evening)

- Phase E's root cause was reproduced on the **live production lane**, not a dev box: 20 concurrent
  conversations, cold build then continuation, `--conversation-cache-slots 16` relayed **0 of 40**
  continuations (every one re-prefilled ~10.8K tokens, median turn prefill 6574 ms); `slots 32` +
  `mib 98304` relayed **40 of 40** (gap = the new turn, median 1306 ms, **5.0× faster**).
- The obvious confound was killed with a control rather than argued: a *fresh* engine at the old
  `slots 16 / mib 131072` also relayed **0/40** with median 6662 ms, i.e. bit-comparable to the
  4-hours-into-production arm.  Freshness was not the variable; the slot ceiling was.
- Overshoot recorded: we had predicted a proportional failure (~4 of 20 missing above a 16-slot
  ceiling).  It is all-or-nothing -- past the ceiling nothing relays -- so a capacity ceiling shows
  up as a cliff in the outcome distribution, never in the averages.
- Adopted into the shipped config (`configs/strata-nvfp4-final.json`), with the RAM budget *lowered*
  128 → 96 GiB so doubling parked conversations still shrinks the worst case.  tg8k 55.9 tok/s
  (inside the B4 band), det ×3 identical, no VRAM change.
- Two defects surfaced by trying to gate this, both larger than the ticket itself:
  the numeric gates had been **grading their own auth failures** for ~24 h (see METHODOLOGY 3), and
  the production `stop()` used a box-wide `pkill -f "build/strata --serve"`, which killed the dev
  lane's engine while its supervisor kept answering `/health` 200 -- a lane that looks alive with
  nothing behind it.  Both fixed: lane-scoped stop/status/check, a card-occupancy guard on `start`
  (the 6-card default had already silently straddled cards 4/5 once), fixtures authenticate and
  abort on 401, and `eval/repro_probe.py` prices the reproducibility floor instead of pretending
  cross-boot hash equality is an invariant.
- Pre-existing, not caused by this change, still open: the 64K `selfcheck` cell flips
  cold != cached in roughly half of runs (Nov-14 history shows the same 5-of-9 pattern at
  `slots 4/16`), and it is the first-relay pass only -- passes 2 and 3 match cold.  Ticket owner:
  whoever chases the nondeterminism found by `repro_probe.py`.
- Chasing that flake turned up the real shape of it, and it is bigger than the cell that kept failing:
  at *matched* re-read depth, two gate runs in one boot disagree on 6 of 8 long-context cells
  (agreement ~2/8), while the 8.5K cold fixture reproduced the 2026-11-15 REF hash bit-for-bit after a
  day of config adoptions.  Output on this lane is therefore deterministic only for short prompts on
  the cold path; long-context relay output has a genuine run-to-run floor.  The gate is rebuilt around
  that fact (`divprobe` primes each depth and records exact `reused`; `qualgate` FAILs on cold
  divergence and on total relay collapse, and treats a single drifted relay cell as WARN), because an
  unsatisfiable gate is the same liability as a broken one -- it was exactly that for 24 h.
  Open ticket, nobody has claimed it yet: find the source of the long-context relay nondeterminism
  (candidates in order: nondeterministic reductions in the host-tier KV restore, expert-streaming
  residency differing pass-to-pass, near-tie argmax with no stable tie-break).  Nothing in the adopted
  capacity change depends on the answer -- the binary is unchanged since 11-15 13:07 and the Nov-14
  `selfcheck` history already shows the intermittency.

## Phase G — residency tickets adopted, ship geometry moved back to 4 cards (11-17)

- The day's tool is `eval/cphase.py`: it boots an arm, waits for health, runs det + a deep ladder +
  an 8K×N concurrency burst, stops, and moves to the next arm, with arms chained so a whole family
  lands unattended. Two defects found while building it: a launcher whose stdout is captured through
  a pipe never reaches EOF because the daemon's grandchild keeps the pipe open (boot hangs), and
  deep-window single runs carry ±15-25% boot noise, so no single cell decides anything — verdicts are
  family medians plus a "winner's min > control's max" bar.
- Adopted pair: `expert-profile-learned.bin` (trained 11-14 for this pack and shipped *unused*) +
  `--trim-stage-weights`.  Single stream 8K 57.1 → 77.9 (6-card boot), full-window 6×262,136
  `sum_tg` 148.5 → 155.0. `--adapt-every 0` measured neutral next to the profile, so adaptation
  stays on.
- Gate discipline hardened the same day, twice: the det fixture had been grading the *cold first run*
  of a boot (run 0 pp 2550 → different hash; runs 1-2 → REF hash), and a cross-boot `relay-collapse
  0/8` FAIL was boot noise — the same default config scores 0-2/8 against its own REF across boots.
  Cross-boot relay cells carry no verdict; same-config reproducibility does (the adopted boot scored
  8/8).
- Ship geometry then moved to 4 cards so 4/5 could go back to the dev lane. The reference config had
  carried the 6-card batch pair, so a 4-card boot was only correct if the operator remembered four
  environment variables; the launcher now derives the batch pair and `NB_CUT` from the card count.
  Re-anchoring the quality reference per geometry, `det` came back bit-identical across the switch
  (`bfd40ecd4692`) — the cold path does not see residency, which is exactly why it is the anchor.
- 4-card adopted state (first boot): single stream 77.3@8K / 70.0@210K, `aggbench` 8K×4 135.6 and
  245K×4 100.2, 4×261,632+512 all exact at the true ceiling with `sum_tg` 108.1, VRAM 58.6 GiB, RAM
  pinned 57.65. The honest gap that came with it — adopted 6-card aggregates never re-taken — is now
  closed: second boots on both geometries gave 6-card 8K×6 **240.1**, 245K×6 **135.2**, full window
  **136.2** (first boot 155.0), plus a 6-card 65K point (80.3), all `det`-identical at `bfd40ecd4692`.
- **Publishing rule changed as a result of those four boots: headline numbers are medians over two
  boots, single-boot cells are marked `n=1`.** The reason is mechanical, not stylistic — the same
  adopted config measured 77.3 and 66.1 at 4-card 8K tg, and 155.0 and 136.2 at 6-card full window,
  i.e. ±15-25% boot noise on deep runs. Promoting one boot into a README row was how we shipped a
  number we could not reproduce.
- Geometry is no longer something the operator remembers: `scripts/lane.sh` + `lane4.sh` / `lane6.sh`
  pin the machine layout, set `LD_PRELOAD=numabind.so`, wait for health and print `GEOMETRY-PASS`
  only after checking *this* boot's startup marker, the split stage count and the live engine's
  `/proc/<pid>/environ`. Two of our own gates died on the machine on the way there (the cumulative
  engine log made a "last batch line" check read the previous lane's statistics; the pack's
  first-touch `NUMA placement FAILED` line appears on every boot including the anchored one, so it is
  a note, not a gate) — recorded in `DECISIONS.md` so they are not re-added.
- Short-window aggregate did not move with the residency tickets (4-card +4%, inside the boot band) —
  it is admission-bound, so those tickets show up in single-stream and deep-window cells instead.
- Ship state re-proved after all of the above landed, on the same 4-card boot (docs merges need no
  restart): `lane4.sh status` GEOMETRY-PASS with cards 4/5 free, `/health` 200 `images: true`, and
  `eval/qualgate.sh check` giving `det` x3 = the anchor hash with `acc-drift-ok 1.003` →
  `QUALGATE-PASS`.  One caution recorded here rather than buried: the gate's `relay-cell-agreement`
  printed 4/8 with five drift WARNs, and a bare `sbench det` invocation returned HTTP 400 once before
  the same command reproduced the anchor hash four times in a row.  Neither changes a verdict -
  cross-boot relay cells carry no verdict by design, and a fixture that aborts is not a measurement -
  but a lane is only "ship" when the *graded* gate says so, not when one probe looks odd.

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
6. Read a flag's own help before modeling it: "--prompt-cache N = N conversation checkpoints"
   (global, not per conversation) and "--batch-mtp ... not with --batch-groups" each killed a
   designed experiment before it cost GPU-hours.
7. Capacity ceilings show up as *outcome distributions*: 98.5% of evictions landing on
   `parked=16` named the knob; every average we had published hid it.
8. Cross-check the served prompt length against the payload you built.  Twice this session a probe
   was silently measuring something else (a messages array flattened to a string: ptok=52 for a
   20K transcript; four filler strings indexed by conversation: NC=8 died after four rows).
9. A green-looking gate is not a gate.  Assert the fixture reached the lane (rows > 0, HTTP 200 with
   auth) before interpreting its verdict, and check liveness with a real request -- a supervisor can
   answer `/health` 200 with its engine dead, and a 401 can be graded as `FAIL det-nondeterministic`.
