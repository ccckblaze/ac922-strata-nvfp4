# AC922 定档线 — Qwen3.8-Flash-Next NVFP4-GPTQ 在 6× V100（POWER9）上

> 英文原文见 [README.md](README.md)，本文为并行维护的中文版本。

本目录是我们把这套引擎部署到 IBM AC922 后的**定档、实测交付物**。机器：双 POWER9 20C SMT4、
6× V100-16GB SXM2（NVLink 2.0，两个 NUMA 岛各三卡）、**实装 DDR 256 GiB**（注意：
`/proc/meminfo` 会虚报到约 341 GiB——多出来的是 V100 HBM 映射进系统物理地址空间的窗口，
不是内存，早期多次冻结事故就栽在信了这个数）、NVMe（Intel PM1725a）。这里写下的每一条都是真跑过的，没有愿景值。

## 头条数字（定版基线，全部本机实测）

| 指标 | 6 卡线（B6） | 4 卡线（B4） |
| --- | ---: | ---: |
| 最大上下文（真实可用） | **262,144**（261,494 探针通过） | **262,144** |
| 冷 prefill @ 262K | **7,016 tok/s** | 5,120 tok/s |
| 冷 prefill @ 6.8K / 54K | 2,246 / 5,154 | 1,488 / 3,970 |
| 单流 decode @ 8K | 66.0 tok/s | 57.2 tok/s |
| 单流 decode @ 262K | 72.5 tok/s | 57.1 tok/s |
| 满窗并发 | **6 × 245K 同时活着，聚合 166.4 tok/s**（每路 23.4） | 4 × 245K，聚合 93.5（每路 21.6） |
| 短窗并发 @ 8K | 聚合 218.2（每路 35.7） | 聚合 133.6（每路 33.3） |
| VRAM 常驻 | 87.7 GiB（逐卡约 14.5–15.0） | 58.6 GiB |
| 主机 RAM 锁页 | 47.7 GiB | 47.7 GiB |

精度（全量题库，errors = 0，预算体检干净）：**GSM8K 97.12%**（1,319 题）、
**MATH500 89.40%**（500 题）、**HumanEval 96.95%**（164 题）。详见 `ACCURACY.md`。

贯穿所有实验的容量法则：**N 卡 ↔ N 路满窗并发**（超订阅只排队、不崩——6 卡 8×245K 已验证）。

## 这里定档了什么

- **引擎**：与上游提交 `b08a70078` 逐字节一致（strata 0.1.59，标签 `v0.2.13-nvfp4.1`）——
  加入本 `ac922/` 目录的交付提交**不碰任何引擎代码**，从这个分支编译出来的就是我们实测的引擎。
  在 ppc64le 上用 CUDA 12.4 面向 `sm_70` 构建。见 `BUILD.md`。
- **模型资产**：Hugging Face [`Maximilian228/Qwen3.8-Flash-Next-NVFP4-GPTQ-Strata`](https://huggingface.co/Maximilian228/Qwen3.8-Flash-Next-NVFP4-GPTQ-Strata)
  —— 17 个文件，校验和见 `results/MODEL-SHA256SUMS.txt`（定版基线前全部验过）。
- **配置 + 启动器**：`configs/strata-nvfp4-final.json`、`scripts/strata-nvfp4-run.sh`
  （已参数化；生产布局记录在 `RUN.md`）。
- **采纳/否决台账**（每个旋钮带实测增益）：`DECISIONS.md`。
- **测量纪律**（同 boot 配对 A/B、质量门禁、预算体检）：`METHODOLOGY.md`。
- **完整性能表与跨栈图**：`PERFORMANCE.md`、`results/charts/`。
- **开发史**、事故与教训：`DEVLOG.md`。
- **评测工具链**（无 torch 判分、车道探针、自检件）：`eval/`。

## 已知缺口（刻意不粉饰）

- 跨栈**精度**对比目前只有一列：两条参照线（int8/IQ3_XXS Strata「S1」、llama.cpp Monster「M1」）
  在**本评测口径**下均无可用端点而结案——它们的*性能*曲线依然有效并已发布
  （`DEVLOG.md`、`ACCURACY.md`、`PERFORMANCE.md`）。诚实的下一步是用 `eval/ref_run.py`
  换锚到官方模型同题分母（只差一个 API key）。
- AIME/ceval 目前只是样本级读数（全量被中止）；GPQA / IFBench / LiveCodeBench-v6 判分件已建好
  并过门禁，GPU 真数排队中。
- 全景图第 ⑦ 面板（精度）当前只有一栈的柱子，数据补齐已挂账。
- 上游在我们钉版之后已有 167 个提交（等下个 `-nvfp4.x` 标签）。策略：**只整版 bump、
  不 cherry-pick**；bump 必核清单在 `DEVLOG.md`。
