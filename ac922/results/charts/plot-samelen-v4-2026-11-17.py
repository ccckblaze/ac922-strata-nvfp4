import json, sys, os
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

FULL = 262144          # 模型满窗（架构上限）
USABLE = 232000        # 引擎每槽自留 ~24-30K => 净可用深度（与栈无关）
BG, INK, SUB = "#fbfbfd", "#15161a", "#4a4d55"

# ---- 配色：主角（我们的 4 卡 / 6 卡定版）高对比；竞品降饱和 + 空心 + 细线 ----
H6, H4 = "#0f5ee0", "#00806a"                      # 主角：电蓝 / 深青绿
C_S1, C_M6, C_M4 = "#a89bc0", "#c6b096", "#9d8468"  # 竞品：灰紫 / 浅灰褐 / 灰褐
GRID, CLOUD = "#c2c6cd", "#8a8d94"

# ---------- 数据：全部为实测（来源见注释），None = 该长度未测/不可达 ----------
# (ptok, pp tok/s, tg tok/s)
# 两条 NVFP4 阶梯为 2026-11-17 定档配置（learned expert-profile + trim-stage-weights）同一 sbench
# long 协议实测，且每档取 **两个 boot 的中位数**（深档单发 boot 噪声 ±15-25%，单 boot 不作结论）：
# 4 卡 /data/work/rb-long.txt + c2-long.txt（重平衡定档）；6 卡 note_c17/ADOPT.long + 6CARD2.long。x 值逐点相同
# ⇒ ①②⑤ 三格严格同比。
N6 = [(6786, 2498.8, 82.1), (54022, 6104.3, 76.4), (110526, 6047.4, 76.1), (165350, 5830.7, 77.9),
      (210038, 5441.2, 81.1)]
N4 = [(6786, 2075.6, 69.9), (54022, 3977.3, 71.5), (110526, 3964.3, 72.0), (165350, 3817.2, 66.0),
      (210038, 3562.8, 66.8)]   # 11-17 重平衡定档 rb-long + c2-long 两 boot 中位
S1 = [(6786, 2955, 63.3), (54022, 5785, 84.5), (110526, 5752, None), (165350, 5501, None), (210038, 5041, None)]  # §48.3 int8 Strata 4卡
M6 = [(7925, 1789.7, 85.9), (63729, 2158.5, 79.0), (260541, 2120.7, 39.2)]                          # §74.5 Monster 6卡 MTP
M4 = [(5570, 1896.8, 89.3), (52961, 1904.6, 75.0), (64772, 1945.9, 75.5),
      (76712, 1946.1, 67.1), (89043, 1942.6, 72.0)]                                                 # §100.2 本次单 boot 阶梯
M4_OLD = (7955, 2426.5, 102.2)   # §73.2 bench3 口径，仅作脚注

# 主角在前（zorder 高），竞品在后（弱化）
STACKS = [
    ("N1 NVFP4 6卡 定档", N6, H6, "o", 3.3, "full",  7),
    ("N1 NVFP4 4卡 定档", N4, H4, "s", 3.0, "full",  7),
    ("S1 int8 Strata 4卡", S1, C_S1, "^", 1.7, "none", 4),
    ("M1 Monster 6卡",     M6, C_M6, "D", 1.7, "none", 4),
    ("M1 Monster 4卡",     M4, C_M4, "v", 1.7, "none", 4),
]

# ⑤ 的 65K 档补点：单发同前缀复用请求，只有 decode 可比（pp 不入 ①），故单独登记
DECODE65 = {"N1 NVFP4 6卡 定档": (65410, None, 80.3)}      # note_c17/6CARD2.p65k；4 卡 65K 未在定档形状重测，不混形状

# ---------- ③ 多流聚合 decode：(每路上下文 ptok, 聚合 tg, 路数, 每路 tg, 来源) ----------
AGG = {
    "N1 NVFP4 6卡 定档": [(8192, 240.1, 6, 39.7, "11-17 aggbench d8"),
                          (245000, 135.2, 6, 21.4, "11-17 aggbench 245K"),
                          (261632, 145.6, 6, 24.3, "11-17 满窗 6×262,136 exact ×2 中位")],
    "N1 NVFP4 4卡 定档": [(8192, 138.6, 4, 34.7, "11-17 定档形状 aggbench d8"),
                          (245000, 103.5, 4, 25.9, "11-17 定档形状 aggbench 245K"),
                          (261632, 106.9, 4, 26.7, "11-17 定档形状 满窗 4×262,136 exact·8/8 length")],
    "S1 int8 Strata 4卡": [(50654, 235.0, 4, 59.0, "§48.4")],
    "M1 Monster 4卡":     [(1536, 119.7, 4, 38.6, "§76"), (32768, 60.5, 4, 15.1, "§48")],
    "M1 Monster 6卡":     [(1536, 115.3, 4, 28.8, "§76")],
}
AGG_ANCHOR = (282.6, "上游私有 fourbank harness 记录 282.6（16K/512·四bank·多路MTP生效）\nllama-server 路径不可复刻，见 runbook §76/§101 讨论")

# ---------- ⑥ 资源账单 ----------
# per_card = 逐卡 VRAM GiB；ram = 主机侧锁页/驻留 GiB
RES = [
    ("NVFP4 6卡", [14.5, 14.0, 14.0, 14.0, 14.0, 14.8], H6, 54.42, "锁页 54.42",
     "11-17 定档 6 卡 boot 运行态：resident RAM mode 54.42 GiB"),
    ("NVFP4 4卡", [14.51, 14.43, 14.42, 14.79], H4, 57.20, "锁页 57.20",
     "11-17 重平衡定档运行态：resident RAM mode 57.20 GiB；空闲 504/587/597/211 MiB（卡3 最紧）"),
    ("int8 Strata 4卡", [14.15, 14.15, 14.15, 14.15], C_S1, 39.97, "experts 驻留 39.97",
     "§48.3（逐卡区间 13.4-14.9，取中值）"),
    ("Monster 4卡", [11.3, 11.0, 14.0, 14.2], C_M4, 61.0, "host used 61",
     "laneverify 11-16 01:57（-c 65536 运行态）"),
    ("Monster 6卡", None, C_M6, None, "未采同刻凭证", "缺采：需一次 6 卡运行态采样"),
]

fig = plt.figure(figsize=(31.2, 12.8), dpi=140, facecolor=BG)
gs = fig.add_gridspec(2, 4, hspace=0.46, wspace=0.17, left=0.038, right=0.988, top=0.852, bottom=0.088)
fig.suptitle("AC922 · 多栈横向对比全景 v4：4 卡出厂线重平衡定档·满窗 8/8 跑到最后格（2026-11-17 17:4x）",
             fontsize=23, fontweight="bold", y=0.972, color=INK)
fig.text(0.036, 0.922,
         "N1 = Strata 0.1.59 NVFP4-GPTQ 定档（批式流水环，**本机最终线**）    "
         "S1 = 定制版 Strata int8/IQ3_XXS 0.1.41    M1 = llama.cpp Monster v232（IQ3_XXS + MTP）    "
         "全部本机实测；满窗 = 262,136 可用上界    本版 4 卡线为 **重平衡定档形状**（layer_split 9,24,37 + vram-reserve 832）："
         "旧形状（auto + 700）在自己的门禁 fixture 下会自重启，故换几何；两 boot 中位数；"
         "满窗 4 路两 boot 全部 prompt+gen=262,136 且 finish=length；"
         "⑦ 精度不变（N1 三科 full，S1 九臂结案、M1 不可判分 §101.7b/§101.7c）",
         fontsize=11.2, color=SUB)


def style_legend(ax, hero=("N1 NVFP4 6卡 定档", "N1 NVFP4 4卡 定档"), **kw):
    leg = ax.legend(**kw)
    for t in leg.get_texts():
        s = t.get_text()
        if s in hero:
            t.set_fontweight("bold")
            t.set_color(INK)
        else:
            t.set_color("#83878f")
    return leg


def axes_len(ax, hero=True):
    ax.grid(True, which="major", ls=":", alpha=0.45)
    ax.set_axisbelow(True)


# ================= ① 冷 prefill =================
ax = fig.add_subplot(gs[0, 0], facecolor=BG)
for name, rows, col, mk, lw, fs, z in STACKS:
    ax.plot([r[0] for r in rows], [r[1] for r in rows], "-", color=col, lw=lw, marker=mk,
            ms=8 if z > 5 else 6.5, fillstyle=fs, markeredgecolor=col, markeredgewidth=lw,
            label=name, zorder=z)
for gx in (8192, 54000, 65536, 131072, 196608, 262144):
    ax.axvline(gx, ls=":", lw=0.9, color=GRID, zorder=1)
ax.set_xscale("log")
ax.set_xticks([8192, 32768, 65536, 131072, 262144])
ax.set_xticklabels(["8K", "32K", "64K", "128K", "262K"], fontsize=10.2)
ax.set_xlim(5600, 300000)
ax.set_ylim(1150, 7800)
ax.set_xlabel("冷 prompt 长度 ptok（对数轴；虚线 = 同比刻度）", fontsize=11)
ax.set_ylabel("冷 prefill 吞吐 tok/s", fontsize=11)
ax.set_title("① Prefill：越长的上下文，NVFP4 车道越占优", fontsize=13.4, fontweight="bold", pad=9, loc="left")
axes_len(ax)
style_legend(ax, loc="lower right", fontsize=8.4, framealpha=0.95, ncol=2)

# ================= ② 单流 decode =================
ax = fig.add_subplot(gs[0, 1], facecolor=BG)
for name, rows, col, mk, lw, fs, z in STACKS:
    pts = [(r[0], r[2]) for r in rows if r[2] is not None]
    if not pts:
        continue
    ax.plot([p[0] for p in pts], [p[1] for p in pts], "-", color=col, lw=lw, marker=mk,
            ms=8 if z > 5 else 6.5, fillstyle=fs, markeredgecolor=col, markeredgewidth=lw,
            label=name, zorder=z)
for gx in (8192, 54000, 65536, 131072, 196608, 262144):
    ax.axvline(gx, ls=":", lw=0.9, color=GRID, zorder=1)
ax.set_xscale("log")
ax.set_xticks([8192, 32768, 65536, 131072, 262144])
ax.set_xticklabels(["8K", "32K", "64K", "128K", "262K"], fontsize=10.2)
ax.set_xlim(5600, 300000)
ax.set_ylim(25, 115)
ax.set_xlabel("当前上下文长度 ptok（对数轴）", fontsize=11)
ax.set_ylabel("单流 decode tok/s", fontsize=11)
ax.set_title("② Decode：Monster 短窗最快，满窗只有 NVFP4 站得住", fontsize=13.4, fontweight="bold", pad=9, loc="left")
axes_len(ax)
style_legend(ax, loc="upper right", fontsize=8.6, framealpha=0.95)
ax.annotate("Monster 4卡 = 本次单 boot 连续阶梯（-c 98304）；\n§73.2 的 102.2@8K 是 bench3 口径，系统性高 ~14%",
            (6100, 30.5), fontsize=8.3, color="#8a6a3a")

# ================= ③ 多流聚合 decode（并发天花板） =================
ax = fig.add_subplot(gs[0, 2], facecolor=BG)
colmap = {s[0]: (s[2], s[3], s[4], s[5], s[6]) for s in STACKS}
for label, pts in AGG.items():
    col, mk, lw, fs, z = colmap[label]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    ax.plot(xs, ys, "-", color=col, lw=lw, marker=mk, ms=8 if z > 5 else 6.5, fillstyle=fs,
            markeredgecolor=col, markeredgewidth=lw, label=label, zorder=z)
    for x0, y0, n, per, src in pts:
        if z > 5:
            if x0 < 200000:
                dx, dy = 10, 11
            else:
                # 245K 与满窗两点在 155-166 / 100-108 上几乎同高，一律朝上会互压
                dx, dy = (-12, 11) if (x0, y0) != pts[-1][:2] else (-12, -22)
        elif label.startswith("M1 Monster 6"):
            dx, dy = 12, 17          # 与 Monster 4卡 同 x 同 y，必须抬到上方
        else:
            dx, dy = 10, -19
        ax.annotate("%d路 ×%.0f/路" % (n, per), (x0, y0), textcoords="offset points",
                    ha="left" if dx > 0 else "right",
                    xytext=(dx, dy), fontsize=8.2,
                    color=INK if z > 5 else "#83878f",
                    fontweight="bold" if z > 5 else "normal")
ax.axhline(AGG_ANCHOR[0], ls="--", lw=1.6, color="#b03a2e", zorder=3, alpha=0.85)
ax.text(0.985, 0.985, AGG_ANCHOR[1], transform=ax.transAxes, fontsize=8.0,
        color="#b03a2e", ha="right", va="top")
ax.set_xscale("log")
ax.set_xticks([2048, 8192, 32768, 131072, 262144])
ax.set_xticklabels(["1.5K", "8K", "32K", "128K", "245K"], fontsize=10.2)
ax.set_xlim(1100, 420000)
ax.set_ylim(35, 335)
ax.set_xlabel("每路上下文档 ptok（对数轴）", fontsize=11)
ax.set_ylabel("并发聚合 decode tok/s", fontsize=11)
ax.set_title("③ 并发聚合：注意每路窗长不同，不可跨档直读", fontsize=13.4, fontweight="bold", pad=9, loc="left")
axes_len(ax)
style_legend(ax, loc="lower left", fontsize=8.0, framealpha=0.95)
ax.text(0.012, -0.20,
        "同档可比（11-17 定档形状，同一 harness）：8K 6卡 240.1 vs 4卡 138.6；245K 135.2 vs 103.5；\n"
        "真满窗 262,136：145.6 vs 106.9。4 卡满窗两 boot 均 4/4 路 exact 且 finish=length（8/8）。",
        transform=ax.transAxes, fontsize=8.0, color=SUB, va="top")

# ================= ④ 各栈最长可达上下文 =================
ax = fig.add_subplot(gs[0, 3], facecolor=BG)
reach = [
    ("NVFP4 6卡", 261632, FULL, "6 路同刻全 exact"),
    ("NVFP4 4卡", 261632, FULL, "4 路同刻全 exact·8/8 finish=length"),
    ("int8 Strata 4卡", 210038, FULL, "-c 262144 可建；稳定实测到 210,038"),
    ("Monster 6卡", 260541, FULL, "实测 260,541 活（勉强）"),
    ("Monster 4卡", 89043, FULL, "-c 98304 实测 89,043 活（本次上限）"),
]
names = [r[0] for r in reach]
vals = [r[1] for r in reach]
cols = [H6, H4, C_S1, C_M6, C_M4]
y = np.arange(len(names))[::-1]
ax.barh(y, vals, 0.62, color=cols, edgecolor="white", lw=0.9, hatch=["", "", "//", "//", "//"],
        alpha=1.0)
ax.axvline(FULL, ls="--", lw=1.8, color="#c0392b", zorder=5)
ax.axvline(USABLE, ls=":", lw=1.3, color="#aeb2b8", zorder=2)
ax.set_ylim(-0.75, 5.05)
ax.text(FULL + 4000, 4.95, "满窗 262,144", color="#c0392b", fontsize=9.4, ha="left", va="top")
ax.text(USABLE - 6000, 4.95, "旧净可用口径 ≈232K\n（定档满窗实测已推翻：262,136 可全取）", color="#5d6066",
        fontsize=7.8, ha="right", va="top")
for yy, (nm, v, _, note) in zip(y, reach):
    ax.text(v + 4200, yy, "%s\n%s" % ("{:,}".format(v), note), va="center", fontsize=8.4, color=INK)
ax.set_yticks(y)
ax.set_yticklabels(names, fontsize=10)
ax.set_xlim(0, 392000)
ax.set_xticks([0, 65536, 131072, 196608, 262144])
ax.set_xticklabels(["0", "64K", "128K", "192K", "256K"], fontsize=10)
ax.set_xlabel("单请求可服务的最大 prompt 长度 ptok", fontsize=11)
ax.set_title("④ 最长上下文：4 卡 Monster 是唯一站不进满窗的一格", fontsize=13.4, fontweight="bold", pad=9, loc="left")
ax.grid(True, axis="x", ls=":", alpha=0.45)
ax.set_axisbelow(True)

# ================= ⑤ 同长度竖切读数（8K / 54K / 66K） =================
ax = fig.add_subplot(gs[1, 0], facecolor=BG)
slots = ["≈8K", "≈54K", "≈65K"]
targets = [(5000, 8200), (50000, 56000), (62000, 68000)]


def nearest(rows, lo, hi):
    best = None
    for r in rows:
        if lo <= r[0] <= hi:
            if best is None or abs(r[0] - (lo + hi) / 2) < abs(best[0] - (lo + hi) / 2):
                best = r
    return best


groups = {}
for name, rows, *_ in STACKS:
    groups[name] = [nearest(rows, lo, hi) for lo, hi in targets]
for name, extra in DECODE65.items():
    if name in groups and groups[name][2] is None:
        groups[name][2] = extra
x = np.arange(len(slots))
w, gap = 0.15, 0.012
for i, (name, _) in enumerate([(s[0], 0) for s in STACKS]):
    col = colmap[name][0]
    hero = i < 2
    off = (i - (len(STACKS) - 1) / 2) * (w + gap)
    vals = []
    for r in groups[name]:
        tg = r[2] if r is not None else None
        vals.append(np.nan if tg is None else float(tg))
    plot_v = [0 if v != v else v for v in vals]
    bars = ax.bar(x + off, plot_v, w, color=col, label=name, edgecolor="white",
                  lw=0.8, alpha=1.0 if hero else 0.9, hatch="" if hero else "//",
                  zorder=6 if hero else 3)
    for xx, vv, bb in zip(x + off, vals, bars):
        if vv != vv:
            ax.text(bb.get_x() + w / 2, 2.5, "—", ha="center", va="bottom", fontsize=10.5, color=CLOUD)
        else:
            ax.text(bb.get_x() + w / 2, vv + 2.2, "%.1f" % vv, ha="center", fontsize=8.9,
                    fontweight="bold" if hero else "normal", color=INK if hero else "#7c7f86")
ax.set_xticks(x)
ax.set_xticklabels(slots, fontsize=11)
ax.set_ylim(0, 118)
ax.set_xlabel("同一 prompt 长度档", fontsize=11)
ax.set_ylabel("单流 decode tok/s", fontsize=11)
ax.set_title("⑤ 同长度竖切：短中窗的 decode 排序", fontsize=13.4, fontweight="bold", pad=9, loc="left")
ax.grid(True, axis="y", ls=":", alpha=0.45)
ax.set_axisbelow(True)
style_legend(ax, loc="upper right", fontsize=8.0, ncol=2, framealpha=0.95)

# ================= ⑥ 资源账单 =================
ax = fig.add_subplot(gs[1, 1], facecolor=BG)
ypos = np.arange(len(RES))[::-1]
for yy, (label, cards, col, ram, rlab, note) in zip(ypos, RES):
    if cards is None:
        ax.barh(yy, 96, 0.42, color="#eceef1", edgecolor="#d6d9de", lw=0.9, zorder=2)
        ax.text(2, yy, "凭证缺采：" + rlab, va="center", fontsize=8.6, color=CLOUD, style="italic")
        ax.text(0.8, yy - 0.36, note, va="center", fontsize=7.1, color=CLOUD)
        continue
    hero = label.startswith("NVFP4")
    left = 0.0
    for g in cards:
        ax.barh(yy, g, 0.42, left=left, color=col, alpha=0.62 if hero else 0.85,
                edgecolor="white", lw=1.1, zorder=4, hatch="" if hero else "//")
        ax.text(left + g / 2, yy, "%.1f" % g, ha="center", va="center", fontsize=7.6,
                color="white" if hero else "#3f4248", fontweight="bold", zorder=5)
        left += g
    ax.barh(yy - 0.30, ram, 0.15, color=col, alpha=0.30, edgecolor=col, lw=0.8, zorder=4)
    ax.text(ram + 1.4, yy - 0.30, "RAM " + rlab, va="center", fontsize=7.9, color=INK)
    ax.text(left + 1.8, yy, "Σ %.1f GiB · %d 卡" % (left, len(cards)), va="center",
            fontsize=8.6, color=INK, fontweight="bold")
    ax.text(0.8, yy - 0.52, note, va="center", fontsize=7.1, color=CLOUD)
ax.set_yticks(ypos)
ax.set_yticklabels([r[0] for r in RES], fontsize=9.8)
ax.set_xlim(0, 150)
ax.set_xticks([0, 16, 32, 48, 64, 80, 96])
ax.set_xticklabels(["0", "16", "32", "48", "64", "80", "96"], fontsize=9.6)
ax.set_xlabel("GiB（每段 = 一张卡的 VRAM；细条 = 主机侧 RAM 占用）", fontsize=10.6)
ax.set_ylim(-0.95, len(RES) - 0.30)
ax.set_title("⑥ 资源账单：HBM 花在哪、RAM 换来了什么", fontsize=13.4, fontweight="bold", pad=9, loc="left")
ax.grid(True, axis="x", ls=":", alpha=0.45)
ax.set_axisbelow(True)
ax.axvline(64, ls="--", lw=1.2, color="#5d6066", zorder=1)
ax.text(64.6, len(RES) - 0.6, "4×16 GiB 上限", fontsize=7.8, color="#5d6066")
ax.axvline(96, ls="--", lw=1.2, color="#5d6066", zorder=1)
ax.text(96.6, len(RES) - 0.6, "6×16 GiB", fontsize=7.8, color="#5d6066")


# ================= ⑦ 精度：唯一能出 full 数的一条线 =================
ax = fig.add_subplot(gs[1, 2:4], facecolor=BG)
ACC = [("gsm8k\n1,319 题", 97.12, 93.75), ("MATH500\n500 题", 89.40, 93.75), ("HumanEval\n164 题", 96.95, 100.0)]
x = np.arange(len(ACC))
bv = [a[1] for a in ACC]
ax.bar(x, bv, 0.38, color=H6, zorder=6, label="N1 NVFP4-GPTQ（4-bit）full 实測")
for xx, (lab, v, sm) in zip(x, ACC):
    ax.text(xx, v + 0.75, "%.2f%%" % v, ha="center", fontsize=11, fontweight="bold", color=INK)
    ax.plot(xx + 0.30, sm, marker="*", ms=17, color="#b0812e", zorder=7, ls="",
            label="N1 smoke 24 题档（轨迹参照）" if xx == x[0] else None)
    ax.text(xx + 0.30, sm - 3.0, "smoke %.1f" % sm, ha="center", fontsize=8.2, color="#8a6a3a")
    ax.plot(xx - 0.32, 57.8, marker="x", ms=12, mew=2.6, color="#b9bdc4", ls="")
    ax.text(xx - 0.32, 60.2, "S1", ha="center", fontsize=8.6, color=CLOUD)
    ax.plot(xx - 0.245, 57.8, marker="x", ms=12, mew=2.6, color="#c9b39a", ls="")
    ax.text(xx - 0.245, 62.4, "M1", ha="center", fontsize=8.6, color=CLOUD)
ax.set_ylim(55, 104)
ax.set_ylim(55, 112)
ax.set_xlim(-0.55, len(ACC) - 0.20)
ax.set_xticks(x)
ax.set_xticklabels([a[0] for a in ACC], fontsize=10.4)
ax.set_ylabel("accuracy %%（轴起点 55%%）")
ax.set_title("⑦ 精度：三栈里只有 N1 交得出 full 数（errors=0 · 预算体检全清）", fontsize=13.4,
             fontweight="bold", pad=9, loc="left")
ax.grid(True, axis="y", ls=":", alpha=0.45)
ax.set_axisbelow(True)
style_legend(ax, loc="upper left", bbox_to_anchor=(0.0, 0.888), fontsize=8.4, framealpha=0.95)
ax.text(0.005, 0.995,
        "S1 × 九臂全 RED · 结案（§101.7c）｜M1 × pack 无可用端点 · 结案（§101.7b）｜标称量化位次：NVFP4-GPTQ 4-bit+校准 > IQ3_XXS 3-bit\n"
        "跨栈真同比待换锚：REF_API_KEY ⇒「N1 vs 官方同题分母」的一致率 / flip / 回归账（只差一个 key）",
        transform=ax.transAxes, fontsize=8.4, color=SUB, ha="left", va="top",
        bbox=dict(boxstyle="round,pad=0.45", fc="#f2f0ea", ec="#d8d4c8", lw=0.8))

fig.text(0.036, 0.016,
         "口径：pp = 冷 prefill 吞吐、tg = 单流 decode（NVFP4 两行为 sbench long 协议，64 tok 生成；竞品行为各自 harness，见来源标注）；聚合 tg 见 ③（各路窗长标在点旁，跨档不可直读）。"
         "65K 档只有 6 卡定档形状单发复用点（4 卡未在定档形状重测，不混形状；仅 decode 可比）。"
         "Monster 4卡本次为单 boot 连续阶梯，故各档可比；S1 int8 的 110K/165K/210K 与 NVFP4 阶梯非同一 ptok，读数请回看 ①②；"
         "int8 长档 tg 受重复语料/ngram 合成抬升，不进 ⑤。资源账单口径：VRAM 为运行态 nvidia-smi 采样，RAM 为引擎自报锁页/驻留（S1=experts 驻留，Monster=运行期 host used）。"
         "⑦ 精度口径：exec/boxed 双判分、errors=0、预算越界已 tail 修复（humaneval 96.34→96.95，flipped=1）；smoke 星号仅为轨迹参照、不作结论。",
         fontsize=9.0, color=SUB)

OUT = sys.argv[1] if len(sys.argv) > 1 else "charts/ac922-samelen-v4-2026-11-17.png"
fig.savefig(OUT, facecolor=BG)
print("WROTE", OUT)
