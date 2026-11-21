#!/usr/bin/env python3
# AC922 decode-window anatomy measured on our own lane, 2026-11-22, 6 cards, shipped env.
# Source: 6 755 STRATA_DECODE_TIMING lines in the 68 MB tail of /data/champion/strata-nvfp4-engine.log
# (this night's ladder boots), batched windows (>=4 windows/line).  Bucket medians; the stack sums
# differ slightly from the ms median because medians do not add.
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

try:
    from matplotlib import font_manager
    for c in ("/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/Helvetica.ttc"):
        font_manager.fontManager.addfont(c)
except Exception:
    pass
plt.rcParams["font.family"] = ["PingFang SC", "Heiti SC", "Arial Unicode MS", "sans-serif"]
plt.rcParams["axes.unicode_minus"] = False

BG, INK, SUB = "#fbfbfd", "#15161a", "#4a4d55"
BODY, CPU, REACH, WAIT, DRAFT, COMMIT = "#1b2430", "#d94f3d", "#f2b134", "#7c5cd6", "#3aa6dd", "#b9c0c9"

fig, axes = plt.subplots(1, 3, figsize=(18.2, 6.6), dpi=160, gridspec_kw={"width_ratios": [1.15, 1, 1.05]})
fig.patch.set_facecolor(BG)

def style(ax, title, ylab, xlab=None):
    ax.set_facecolor(BG)
    ax.set_title(title, fontsize=12.2, color=INK, pad=10, fontweight="bold")
    ax.set_ylabel(ylab, fontsize=10, color=SUB)
    if xlab:
        ax.set_xlabel(xlab, fontsize=10, color=SUB)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color("#c2c6cd")
    ax.tick_params(colors=SUB, labelsize=9.5)
    ax.grid(axis="y", color="#dfe2e7", lw=0.7, zorder=0)

# ---------------- panel 1: window composition by depth class ----------------
labels = ["短窗\n(<=30 ms)", "中窗\n(30-45 ms)", "深窗\n(>45 ms)"]
body  = np.array([20.24, 18.97, 19.88])
cpu   = np.array([0.52, 6.24, 17.48])
reach = np.array([3.32, 4.29, 4.88])
wait  = np.array([0.03, 1.19, 2.87])
draft = np.array([2.10, 3.20, 4.33])
commit= np.array([0.46, 0.48, 0.53])
tot   = np.array([27.05, 36.33, 51.82])
ax = axes[0]
x = np.arange(3); w = 0.62
bottom = np.zeros(3)
for name, vals, colr in (("GPU 计算体（残差）", body, BODY), ("host-CPU 专家", cpu, CPU),
                         ("GPU-reach 等待", reach, REACH), ("自适应等待", wait, WAIT),
                         ("草稿 (MTP)", draft, DRAFT), ("提交/emit", commit, COMMIT)):
    ax.bar(x, vals, w, bottom=bottom, color=colr, label=name, zorder=3)
    bottom = bottom + vals
for i, t in enumerate(tot):
    ax.plot([x[i]-w/2-0.06, x[i]+w/2+0.06], [t, t], color=INK, lw=1.6, zorder=5)
    ax.text(x[i], t+1.2, "%.1f ms" % t, ha="center", fontsize=9.5, color=INK, fontweight="bold")
ax.text(0.0, 8.5, "GPU 体恒定\n~20 ms", ha="center", fontsize=9, color="#e9ebf0", fontweight="bold")
ax.annotate("投送成本 +17 ms", xy=(2.34, 30.5), xytext=(1.62, 46.5), fontsize=9.5, color=CPU,
            arrowprops=dict(arrowstyle="->", color=CPU, lw=1.4))
ax.set_xticks(x); ax.set_xticklabels(labels, fontsize=10)
style(ax, "① 窗口解剖：深窗慢在“投送”，不在算力", "每窗口时间 (ms)", "窗口深度分档（批式窗口中位数）")
ax.legend(fontsize=8.2, loc="upper left", frameon=False, ncol=1)
ax.set_ylim(0, 60)

# ---------------- panel 2: deep band vs arm deltas at 131K ----------------
ax = axes[1]
anchors = {"ctl22": 160.5, "ctlA": 146.9, "ctlB": 166.1}
mean = sum(anchors.values()) / len(anchors)
deltas = {"noarena\n(去页锁 complement)": -23.3, "dx\n(RTX50 长块打分)": -5.2, "topk\n(TOPK_COMPACT)": -1.8}
names = list(deltas.keys())
vals = list(deltas.values())
ax.axhspan(-mean*(max(anchors.values())/min(anchors.values())-1), mean*(max(anchors.values())/min(anchors.values())-1),
           color="#f2b134", alpha=0.18, zorder=1)
ax.axhspan(-6.1, 6.1, color="#f2b134", alpha=0.28, zorder=2)
bars = ax.bar(range(3), vals, 0.5, color=["#0f5ee0", "#9aa0ab", "#9aa0ab"], zorder=4)
for i, v in enumerate(vals):
    ax.text(i, v - 1.6 if v < 0 else v + 1.2, "%+.1f%%" % v, ha="center", fontsize=10,
            color=INK, fontweight="bold")
ax.axhline(0, color=INK, lw=1.2, zorder=5)
ax.set_xticks(range(3)); ax.set_xticklabels(names, fontsize=9.2)
ax.set_ylim(-32, 12)
style(ax, "② 131K 判读门槛：同 env 三次锚点先自证", "相对 ctlA 的 sum_tg Δ", "臂（单 boot）")
ax.text(2.42, 8.0, "±6% = 锚点标准差\n±13% = 锚点极差", fontsize=8.6, color="#a2761b", ha="right")
ax.text(0.03, 3.2, "只有 noarena 逃出带外\n（且与 98K 的 -16.2% 同向）", fontsize=9.2, color="#0f5ee0")

# ---------------- panel 3: expert delivery counters ----------------
ax = axes[2]
misses = np.array([0.04, 0.73, 1.68])       # CPU-served experts per layer-window
swaps  = np.array([0.2, 7.4, 16.8])         # VRAM cache swaps per window
hits   = np.array([17.0, 28.4, 38.8])       # VRAM hits per layer-window
x = np.arange(3)
ax.bar(x - 0.17, misses, 0.33, color=CPU, label="每层-窗 CPU 专家（漏出 VRAM）", zorder=3)
ax.bar(x + 0.17, swaps, 0.33, color="#3aa6dd", label="每窗 VRAM 换入次数", zorder=3)
for i, v in enumerate(misses):
    ax.text(x[i]-0.17, v+0.4, "%.2f" % v, ha="center", fontsize=9, color=INK)
for i, v in enumerate(swaps):
    ax.text(x[i]+0.17, v+0.4, "%.1f" % v, ha="center", fontsize=9, color=INK)
ax2 = ax.twinx()
ratio = 100 * hits / (hits + misses)
ax2.plot(x, ratio, marker="o", color="#00806a", lw=2.2, label="VRAM 命中率 %")
ax2.set_ylim(90, 101); ax2.set_ylabel("命中率 (%)", fontsize=10, color="#00806a")
ax2.tick_params(colors="#00806a", labelsize=9.5)
for s in ("top", "left"):
    ax2.spines[s].set_visible(False)
ax2.spines["right"].set_color("#c2c6cd")
for i, v in enumerate(ratio):
    ax2.text(x[i], v - (1.9 if i == 0 else 1.7), "%.1f%%" % v, ha="center", fontsize=9, color="#00806a", fontweight="bold")
ax.set_xticks(x); ax.set_xticklabels(["短窗", "中窗", "深窗"], fontsize=10)
style(ax, "③ 命中率没塌，绝对漏出量涨 40×", "次 / 层-窗（漏出）· 次 / 窗（换入）")
ax.set_ylim(0, 20)
ax.legend(fontsize=8.4, loc="upper right", frameon=False)
ax2.legend(fontsize=8.4, loc="lower left", frameon=False)

fig.suptitle("AC922 · Qwen3.8-Flash-Next NVFP4-GPTQ · 自测解码窗口解剖与深窗判读门槛 · 2026-11-22 夜间 6 卡实测",
             fontsize=13.5, color=INK, y=0.985, fontweight="bold")
fig.text(0.012, 0.012, "数据源：STRATA_DECODE_TIMING（生产 env：--batch 6 --batch-groups 3, CC 48 GiB, resident complement 64 GiB, learned expert profile）；"
                       "n=744 批式窗口行 / 5 994 单窗行。  脚本：ac922/results/charts/plot-anatomy-2026-11-22.py", fontsize=8.2, color=SUB)
fig.tight_layout(rect=(0, 0.03, 1, 0.96))
out = "/".join(__file__.split("/")[:-1]) + "/ac922-anatomy-2026-11-22.png"
fig.savefig(out, facecolor=BG)
print("wrote", out)
