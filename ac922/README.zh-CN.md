# AC922 定档线 — Qwen3.8-Flash-Next NVFP4-GPTQ 在 6× V100（POWER9）上

> 英文原文见 [README.md](README.md)，本文为并行维护的中文版本。

本目录是我们把这套引擎部署到 IBM AC922 后的**定档、实测交付物**。机器：双 POWER9 20C SMT4、
6× V100-16GB SXM2（NVLink 2.0，两个 NUMA 岛各三卡）、**实装 DDR 256 GiB**（注意：
`/proc/meminfo` 会虚报到约 341 GiB——多出来的是 V100 HBM 映射进系统物理地址空间的窗口，
不是内存，早期多次冻结事故就栽在信了这个数）、NVMe（Intel PM1725a）。这里写下的每一条都是真跑过的，没有愿景值。

## 头条数字（定档基线，全部本机实测）

| 指标 | **4 卡线（出厂定档）** | 6 卡线（参照） |
| --- | ---: | ---: |
| 最大上下文（真实可用） | **262,136 可达**：4 × 261,632 + 512，每路 prompt 全 exact | 6 × 261,632 + 512，每路全 exact |
| 冷 prefill @ 6.8K / 54K / 210K | 2,076 / 3,977 / 3,563 | 2,499 / 6,104 / 5,441 |
| 冷 prefill @ 262K（单流） | 定档形状未重测（旧形状 4,964） | 7,016 tok/s（261,494 阶梯） |
| 单流 decode @ 8K | 69.9 tok/s | 82.1 tok/s |
| 单流 decode @ 210K | 66.8 tok/s | 81.1 tok/s |
| 满窗并发 | **4 × 262,136 同时活着，聚合 107.4 tok/s，`held_edge` 4/4**（2026-11-22 同一 boot 三次重复的中位数；2026-11-17 两 boot 为 105.8 / 108.0） | 6 × 262,136 同时活着，聚合 145.6（每路 20.7–32.4） |
| 满窗并发（3 路地板） | 3 × 262,136 同时活着，聚合 85.7，3/3 路 `finish=length` | 未测 |
| 短窗并发 @ 8K | 聚合 159.2（每路 39.8；停车下限 4096，2026-11-22——旧 32768 下限下为 138.6） | 聚合 240.1（每路 39.7） |
| 二轮 TTFT（2K–64K） | 已停车 ~0.4 s，未停车 4.5–10 s（下限 4096） | 未复测 |
| 深窗并发 @ 245K | 聚合 103.5（墙钟 79.6 s） | 聚合 135.2（墙钟 93.6 s） |
| 暖 262K 单流 decode | 59.2 tok/s | 未测 |
| VRAM 常驻 | 58.0 GiB（逐卡 14.4–14.8） | 85.3 GiB（逐卡 14.0–14.8） |
| 主机 RAM 锁页 | 57.10 GiB | 54.42 GiB |

两列都是 2026-11-17 以采纳态（learned expert-profile + `--trim-stage-weights` + 批式流水环）实测，
口径为**每种几何两次独立 boot 的中位数**——单次深档跑分带 ±15–25% 的 boot 噪声，因此除表中标注
`n=1` 的格子外，本文没有任何数字来自单次 boot。4 卡一列是**重平衡后的定档形状**
（`layer_split 9,24,37` + `--vram-reserve-mib 832`）：旧形状在自己的门禁 fixture 下会自行重启，
所以换了几何；6 卡一列仍是重平衡前的参照线。完整口径见 `PERFORMANCE.md` §6d–6e。

一条口径说明，写在明处而不是藏起来：4 卡那一列的**阶梯行**（冷 pp、单流 tg）是在定档当天的引擎
0.1.59 上取的。此后引擎按整版纪律 bump 过（现网 0.1.61，见 `PERFORMANCE.md` §7）；出厂二进制上已
重新验过的是满窗行（2026-11-22，`PERFORMANCE.md` §9f）与 8K/245K 聚合及停车下限（2026-11-22，
§10）。把冷 pp / 单流 tg 在出厂引擎上重跑已经记账，属于已知欠账，不是被忽略。

停车下限（2026-11-22）同日在 6 卡参照线上做了旁证：对它是**中性**——6 路会话正好放进 6 个 batch
槽，根本触发不了准入闸；该线的冷 boot `held_edge` 抖动经"顺序对齐对照组"实测**与下限无关**，是深窗
边缘在分块 prefill 切分变化下的 EOS 采样现象，已随切分不变性欠账记账（`PERFORMANCE.md` §10d）。

**出厂不变量——满窗是前提。** 窗口长度永远不是可以让步的那一项：如果某个改动在出厂 4 卡线上撑不住
4 路逐字节满窗并发，出厂形状就降到 **3 路满窗并发**，而不是把窗口缩短。裁决工具是
`eval/fullwindow.py`（判据 `held_edge`：每一路都以 `finish=length` 停在同一个窗口末端 262,136，
引擎自己预留的 8 个草稿槽正好是它到 262,144 的差；"窗口的 98.7%"不算通过），
并发要付多少代价由 `eval/aggbench.py` 定价。

精度（全量题库，errors = 0，预算体检干净）：**GSM8K 97.12%**（1,319 题）、
**MATH500 89.40%**（500 题）、**HumanEval 96.95%**（164 题）。详见 `ACCURACY.md`。

多模态默认开启：图像输入走 OpenAI `image_url` 消息格式，由纯 CPU 的 FP32 视觉塔
（`strata-vision` + `mmproj-F16.gguf`）编码，参考图往返 2–4 s。对文本线实测代价：
**VRAM 增量 0**；单流与满窗数字全部落在箱体漂移带内（262K 探针 pp 6,949 vs 7,016；
6×245K 稳态聚合 156–169，插入图像探针前后同带）；18 行超订阅下图像请求只排队
（TTFT 28–44 s）、不崩。启动时 `VISION=0` 可关。

贯穿所有实验的容量法则：**N 卡 ↔ N 路满窗并发**（超订阅只排队、不崩——6 卡 8×245K 已验证）。

## 这里定档了什么

- **引擎**：与上游提交 `b08a70078` 逐字节一致（strata 0.1.59，标签 `v0.2.13-nvfp4.1`）——
  加入本 `ac922/` 目录的交付提交**不碰任何引擎代码**，从这个分支编译出来的就是我们实测的引擎。
  在 ppc64le 上用 CUDA 12.4 面向 `sm_70` 构建。见 `BUILD.md`。
- **模型资产**：Hugging Face [`Maximilian228/Qwen3.8-Flash-Next-NVFP4-GPTQ-Strata`](https://huggingface.co/Maximilian228/Qwen3.8-Flash-Next-NVFP4-GPTQ-Strata)
  —— 17 个文件，校验和见 `results/MODEL-SHA256SUMS.txt`（定版基线前全部验过）。
  多模态另需 `unsloth/Qwen3.8-Flash-Next-GGUF` 中的 `mmproj-F16.gguf`（约 904 MB；
  dense GGUF 本身早已带 `image-text-to-text` 任务声明和 `image_token_id`）。
- **配置 + 启动器**：`configs/strata-nvfp4-final.json`；`scripts/lane4.sh` / `scripts/lane6.sh`
  （几何快捷入口，底层是 `scripts/lane.sh`：钉住本机布局、等 health、并用引擎自述行证明几何形状），
  以及底层的 `scripts/strata-nvfp4-run.sh`
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
