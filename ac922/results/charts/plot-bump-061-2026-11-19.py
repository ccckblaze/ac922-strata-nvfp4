#!/usr/bin/env python3
# 0.1.59 -> 0.1.61 same-protocol bump comparison, AC922, measured 2026-11-19.
# Caliber: aggbench sum_tg (published aggregate), warm prefills, decode-only.
# 6c pairs are same-boot-family both sides (0.1.59 A1_6x3 boot vs 0.1.61 boots).
# 4c n=6 points have no 0.1.59 same-caliber history -> shown as 0.1.61 state only.
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
V059, V061 = "#9aa0ab", "#0f5ee0"
H4 = "#00806a"

fig, axes = plt.subplots(1, 3, figsize=(17.5, 6.4), dpi=160)
fig.patch.set_facecolor(BG)

def style(ax, title, ylab):
    ax.set_facecolor(BG)
    ax.set_title(title, fontsize=12.5, color=INK, pad=10, fontweight="bold")
    ax.set_ylabel(ylab, fontsize=10, color=SUB)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color("#c2c6cd")
    ax.tick_params(colors=SUB, labelsize=9.5)
    ax.grid(axis="y", color="#dfe2e7", lw=0.7, zorder=0)

# ---------------- panel 1: 6 cards, paired ----------------
m6 = ["单流\n8K", "单流\n50K", "6路并发\n8K", "6路并发\n50K", "6路并发\n131K", "满窗并发\n6×262K"]
a6 = [84.5, 72.8, 227.9, 200.9, 135.0, 145.6]          # 0.1.59 (A1_6x3 / 满窗×2中位)
b6 = [91.8, 80.8, 207.4, 206.9, 138.4, 158.5]          # 0.1.61 (8K×6 = 3-run median)
d6 = [(y - x) / x * 100 for x, y in zip(a6, b6)]
x = np.arange(len(m6)); w = 0.38
ax = axes[0]
ax.bar(x - w/2, a6, w, color=V059, label="0.1.59", zorder=3)
ax.bar(x + w/2, b6, w, color=V061, label="0.1.61", zorder=3)
for xi, (va, vb, dd) in enumerate(zip(a6, b6, d6)):
    ax.text(xi + w/2, vb + 4, f"{vb:.0f}", ha="center", fontsize=8.6, color=V061, fontweight="bold")
    ax.text(xi - w/2, va + 4, f"{va:.0f}", ha="center", fontsize=8.2, color="#6c727d")
    ax.text(xi, max(va, vb) + 17, f"{dd:+.1f}%", ha="center", fontsize=9,
            color=("#0a7a3d" if dd >= 0 else "#b3261e"), fontweight="bold")
ax.set_xticks(x); ax.set_xticklabels(m6, fontsize=9)
ax.set_ylim(0, 268)
style(ax, "① 6 卡 · 同协议前后对比（aggbench sum_tg）", "decode tok/s")
ax.legend(frameon=False, fontsize=9.5, loc="upper right", ncols=2)

# ---------------- panel 2: 4 cards, 0.1.61 state ----------------
m4 = ["单流\n8K", "单流\n50K", "6路并发\n8K", "6路并发\n50K", "6路并发\n131K"]
b4 = [81.1, 72.7, 247.2, 238.2, 179.8]   # mean of two boots (0.1.61)
boots4 = ["85.0/77.2", "70.7/74.6", "240.8/253.7", "245.0/231.5", "178.0/181.6"]
ax = axes[1]
bars = ax.bar(np.arange(len(m4)), b4, 0.55, color=H4, zorder=3)
for xi, (v, bs) in enumerate(zip(b4, boots4)):
    ax.text(xi, v + 4, f"{v:.0f}", ha="center", fontsize=9.5, color=H4, fontweight="bold")
    ax.text(xi, v / 2, bs.split("/")[0] + "\n" + bs.split("/")[1], ha="center",
            fontsize=7.6, color="white")
ax.set_xticks(np.arange(len(m4))); ax.set_xticklabels(m4, fontsize=9)
ax.set_ylim(0, 285)
style(ax, "② 4 卡 · 0.1.61 现状（两 boot 均值，白字=boot1/boot2）", "decode tok/s")
ax.text(0.02, 0.965, "n=6 档无 0.1.59 同口径历史（当年出厂形状为 4 路）\n满窗对比见 ③",
        transform=ax.transAxes, fontsize=8.4, color=SUB, va="top")

# ---------------- panel 3: full-window warm, both card counts ----------------
labels = ["4 卡\n4×262,136", "6 卡\n6×262,136"]
v059 = [106.9, 145.6]
v061 = [102.4, 158.5]
x = np.arange(2); w = 0.34
ax = axes[2]
ax.bar(x - w/2, v059, w, color=V059, label="0.1.59", zorder=3)
ax.bar(x + w/2, v061, w, color=V061, label="0.1.61", zorder=3)
for xi, (a, b) in enumerate(zip(v059, v061)):
    dd = (b - a) / a * 100
    ax.text(xi - w/2, a + 2.5, f"{a:.1f}", ha="center", fontsize=9, color="#6c727d")
    ax.text(xi + w/2, b + 2.5, f"{b:.1f}", ha="center", fontsize=9.5, color=V061, fontweight="bold")
    ax.text(xi + w/2, b / 2, f"{dd:+.1f}%", ha="center", fontsize=10, color="white", fontweight="bold")
ax.set_xticks(x); ax.set_xticklabels(labels, fontsize=9.5)
ax.set_ylim(0, 190)
style(ax, "③ 满窗并发（字节级 262,136/路）", "agg decode tok/s")
ax.legend(frameon=False, fontsize=9.5, loc="upper left", ncols=2)
ax.text(0.02, 0.72, "4 卡 −4.2%（带内，0.1.59 两 boot 105.8/108.0，另有出厂二进制单 boot 109.3）\n"
                    "6 卡 +8.9%；0.1.61 侧 5/6 自然 EOS、1 路 held_edge\n"
                    "6 卡冷满窗参照：131.4（0.1.59 冷口径 100.9 total，非同尺）",
        transform=ax.transAxes, fontsize=8.0, color=SUB, va="top")

fig.suptitle("AC922 · Qwen3.8-Flash-Next NVFP4 · 引擎 0.1.59 → 0.1.61 采纳复测（2026-11-19，全部本机实测）",
             fontsize=14.5, color=INK, fontweight="bold", y=0.985)
fig.text(0.5, 0.015,
         "口径：aggbench warm-burst sum_tg（发布口径），prefill 不在测量内 · 6 卡 8K×6 三跑中位（182.1/207.4/231.9，队列计时噪声 ±12%，中位规则见 DEVLOG H.9） · "
         "精度带（0.1.61，4 卡 boot）：GSM8K-500 97.00% · HumanEval-164 96.34%（3,000 tok 预算；旧线尾样本 6,000 预算翻正记 96.95） · qualgate det_same=true",
         ha="center", fontsize=8.3, color=SUB)
fig.tight_layout(rect=(0, 0.045, 1, 0.955))
out = "/Users/steven/Projects/LLM/ac922-strata-nvfp4/ac922/results/charts/ac922-bump-061-2026-11-19.png"
fig.savefig(out, facecolor=BG)
print(out)
