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
# §48.3 的三个深档 tg **不是没测**，是口径不可同比：重复语料 + ngram 起草把深窗 decode 抬到 110-112（>54K 档的 84.5），
# 所以不并入阶梯实线，只作为存档虚线画在 ②，且不进 ⑤ 的同比切片。
S1_DEEP = [(110526, 59.1), (165350, 112.4), (210038, 110.0)]      # §48.3 原值，虚线存档
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
# 65K 档两主角都补到（同一 depthtg 协议：warm 前缀 + 5 轮 × 64 tok 取中位）。
# 4 卡 = 2026-11-17 23:2x 交付仓二进制 boot，ptok 65,376，两次独立跑 tg_med 75.4 / 70.8 ⇒ 合并 10 轮中位 **72.1**。
# 6 卡 = note_c17/6CARD2.p65k 的单发复用读数 80.3（ptok 65,410），未做同轮数复测 ⇒ 两格不同置信层，图上分清。
DECODE65 = {"N1 NVFP4 6卡 定档": (65410, None, 80.3),
            "N1 NVFP4 4卡 定档": (65376, None, 72.1)}

# ---------- ③ 多流聚合 decode：(每路上下文 ptok, 聚合 tg, 路数, 每路 tg, 来源) ----------
AGG = {
    "N1 NVFP4 6卡 定档": [(8192, 237.4, 6, 39.6, "11-22 两 floor 同带 227.2/237.4（11-17 为 240.1）"),
                          (245000, 125.0, 6, 20.8, "11-22 两发 125.0/103.5（11-17 为 135.2）"),
                          (261632, 145.6, 6, 24.3, "11-17 满窗 6×262,136 exact ×2 中位；11-22 复读数 153.4/153.7/124.9（容量 exact 6/6，冷边缘 EOS 抖动见 §10d）")],
    "N1 NVFP4 4卡 定档": [(8192, 159.2, 4, 39.8, "11-22 停车地板 4096 三发中位（151.7/159.2/171.4；旧 32768 同形状 138.6、被税时 78.3/94.9）"),
                          (245000, 103.5, 4, 25.9, "11-17 定档形状 aggbench 245K · 11-22 复测 114.1/97.1 同带"),
                          (261632, 107.4, 4, 26.9, "11-22 出厂 boot 三次重复中位（126.4/106.3/107.4），held_edge 4/4")],
    "S1 int8 Strata 4卡": [(50654, 235.0, 4, 59.0, "§48.4")],
    "M1 Monster 4卡":     [(1536, 119.7, 4, 38.6, "§76"), (8192, 122.0, 4, 33.3, "§74.5 -c 68608 --parallel 4"),
                           (32768, 60.5, 4, 15.1, "§48")],
    "M1 Monster 6卡":     [(1536, 115.3, 4, 28.8, "§76"), (8192, 83.1, 4, 22.0, "§74.5 同档 6 卡 −31.9%")],
}
# 每格标注的落点：8K 档现在挤了 Monster 4卡/6卡两格，1.5K 也挤两格，一律错开
AGG_OFF = {("N1 NVFP4 6卡 定档", 8192): (10, 11), ("N1 NVFP4 6卡 定档", 245000): (-12, 12),
           ("N1 NVFP4 6卡 定档", 261632): (12, 6),      # 满窗两格甩到右侧空白，别和 245K 挤
           ("N1 NVFP4 4卡 定档", 8192): (10, 11), ("N1 NVFP4 4卡 定档", 245000): (-12, -22),
           ("N1 NVFP4 4卡 定档", 261632): (12, 6),
           ("S1 int8 Strata 4卡", 50654): (10, 11),
           ("M1 Monster 4卡", 1536): (12, 12), ("M1 Monster 4卡", 8192): (-12, -22),
           ("M1 Monster 4卡", 32768): (10, -19),
           ("M1 Monster 6卡", 1536): (12, -24), ("M1 Monster 6卡", 8192): (12, -24),
           ("*", 1536): (10, -19), ("*", 8192): (10, 11)}

AGG_ANCHOR = (282.6, "上游私有 fourbank harness 记录 282.6（16K/512·四bank·多路MTP生效）\nllama-server 路径不可复刻，见 runbook §76/§101 讨论")

# ---------- ⑥ 资源账单 ----------
# per_card = 逐卡 VRAM GiB；ram = 主机侧锁页/驻留 GiB
RES = [
    ("NVFP4 6卡", [14.5, 14.0, 14.0, 14.0, 14.0, 14.8], H6, 54.42, "锁页 54.42",
     "11-17 定档 6 卡 boot · resident RAM 54.42"),
    ("NVFP4 4卡", [14.48, 14.38, 14.38, 14.77], H4, 57.10, "锁页 57.10",
     "11-17 22:53 交付仓二进制 boot · free 528/633/626/237 MiB（卡3 紧）"),
    ("int8 Strata 4卡", [14.15, 14.15, 14.15, 14.15], C_S1, 39.97, "experts 驻留 39.97",
     "§48.3 逐卡 13.4-14.9 取中值"),
    ("Monster 4卡", [11.3, 11.0, 14.0, 14.2], C_M4, 61.0, "host used 61",
     "laneverify 11-16 01:57（-c 65536）"),
    ("Monster 6卡", "Σ66.8", C_M6, None, "逐卡未采同刻",
     "§74.5 · node8 岛 11.6-13.6/卡，比 node0 多 ~1.5 · RAM 同刻未采"),
]

fig = plt.figure(figsize=(31.2, 12.8), dpi=140, facecolor=BG)
gs = fig.add_gridspec(2, 4, hspace=0.46, wspace=0.17, left=0.038, right=0.988, top=0.852, bottom=0.122)
fig.suptitle("AC922 · 多栈横向对比全景 v6：停车地板 4096 采纳（短窗 ×2）·出厂二进制 0.1.61·满窗 held_edge（2026-11-22）",
             fontsize=21.5, fontweight="bold", y=0.975, color=INK)
fig.text(0.036, 0.938,
         "N1 = Strata 0.1.59 NVFP4-GPTQ 定档（批式流水环，本机最终线）    "
         "S1 = 定制版 Strata int8/IQ3_XXS 0.1.41    M1 = llama.cpp Monster v232（IQ3_XXS + MTP）    "
         "全部本机实测；满窗 = 262,136 可用上界", fontsize=10.6, color=SUB)
fig.text(0.036, 0.906,
         "4 卡线 = 11-17 重平衡定档形状（layer_split 9,24,37 + vram-reserve 832；旧形状 auto+700 在自己的门禁 fixture 下会自重启，故换几何），阶梯取两 boot 中位数；",
         fontsize=10.6, color=SUB)
fig.text(0.036, 0.876,
         "出厂二进制 = 本仓自编译（strata 0.1.61 / sha 8f6a5167…），QUALGATE 全绿；停车闸 32768→4096 于 11-22 采纳："
         "4卡 8K×4 138.6→159.2、满窗 107.4 三发中位不变；6卡 六槽≥六路、闸不被咨询（两 floor 同带实测，§10）", fontsize=10.6, color=SUB)


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
# 深档三格只画散点、不连线：连线会被读成一条可比曲线，而它是合成语料口径
ax.plot([r[0] for r in S1_DEEP], [r[1] for r in S1_DEEP], "o", color=C_S1, ms=7.5, fillstyle="none",
        markeredgecolor=C_S1, markeredgewidth=1.6, ls="", zorder=4,
        label="S1 int8 深档 tg（§48.3 存档：重复语料+ngram 抬升，不可同比）")
ax.annotate("存档口径，不并入阶梯", (132000, 115.5), fontsize=7.8, color="#8f86a8", ha="left", va="top")
for xk, tv, lab, ck, dxy in ((65410, 80.3, "6卡 单发 80.3", H6, (14, 18)),
                             (65376, 72.1, "4卡 10轮中位 72.1", H4, (14, -22))):
    ax.plot([xk], [tv], marker="P", ms=13.0, color=ck, ls="", zorder=9,
            markeredgecolor="white", markeredgewidth=1.2)
    ax.annotate(lab, (xk, tv), textcoords="offset points", xytext=dxy,
                fontsize=8.3, color=ck, fontweight="bold", zorder=9)
for gx in (8192, 54000, 65536, 131072, 196608, 262144):
    ax.axvline(gx, ls=":", lw=0.9, color=GRID, zorder=1)
ax.set_xscale("log")
ax.set_xticks([8192, 32768, 65536, 131072, 262144])
ax.set_xticklabels(["8K", "32K", "64K", "128K", "262K"], fontsize=10.2)
ax.set_xlim(5600, 300000)
ax.set_ylim(25, 118)
ax.set_xlabel("当前上下文长度 ptok（对数轴）", fontsize=11)
ax.set_ylabel("单流 decode tok/s", fontsize=11)
ax.set_title("② Decode：Monster 短窗最快，满窗只有 NVFP4 站得住（加号=65K 补点，空心=存档口径）",
             fontsize=12.6, fontweight="bold", pad=9, loc="left")
axes_len(ax)
style_legend(ax, loc="lower left", fontsize=7.9, framealpha=0.95)

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
        dx, dy = AGG_OFF.get((label, x0)) or AGG_OFF[("*", x0)]
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
ax.set_title("③ 并发聚合：注意每路窗长不同，不可跨档直读（Monster 8K×4 已从 §74.5 补齐）",
             fontsize=12.8, fontweight="bold", pad=9, loc="left")
axes_len(ax)
style_legend(ax, loc="upper left", fontsize=8.0, framealpha=0.95)
ax.text(0.012, -0.20,
        "同档可比（同一 aggbench/fullwindow 协议，11-22 刷新）：8K 6卡 237.4 vs 4卡 159.2（停车地板 4096；\n"
        "旧 32768 地板下 4卡 8K 曾跌到 78.3/94.9，6卡 因六槽≥六路从不被税——两 floor 实测同带 §10d）；\n"
        "245K 125.0 vs 103.5；真满窗 262,136：145.6（11-17 中位）vs 107.4（11-22 出厂 boot 三发中位）。",
        transform=ax.transAxes, fontsize=8.0, color=SUB, va="top")

# ================= ④ 各栈最长可达上下文 =================
ax = fig.add_subplot(gs[0, 3], facecolor=BG)
reach = [
    ("NVFP4 6卡", 261632, FULL, "6 路同刻全 exact"),
    ("NVFP4 4卡", 261632, FULL, "4 路同刻全 exact·4/4 held edge（11-22 复测）"),
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
ax.set_title("⑤ 同长度竖切：短中窗的 decode 排序（65K 已补满 N1 两行）", fontsize=13.0, fontweight="bold", pad=9, loc="left")
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
    if isinstance(cards, str):                      # 只有 Σ 总量、逐卡未采同刻
        tot = float(cards.split("Σ")[1])
        ax.barh(yy, tot, 0.42, color=col, alpha=0.5, edgecolor=col, lw=1.1, hatch="//", zorder=4)
        ax.text(tot + 1.8, yy, "Σ %.1f GiB · 逐卡未采" % tot, va="center", fontsize=8.6, color=INK)
        ax.text(0.8, yy - 0.56, note, va="center", fontsize=7.0, color=CLOUD)
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
    ax.text(0.8, yy - 0.56, note, va="center", fontsize=7.0, color=CLOUD)
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

FOOTER = [
    "口径：pp = 冷 prefill 吞吐、tg = 单流 decode。NVFP4 两行为 sbench long 协议（64 tok 生成、两 boot 中位）；竞品行为各自 harness，来源标在数据注释里。",
    "聚合 tg 见 ③，各路窗长标在点旁，跨档不可直读。65K 档两主角均已补：4卡 10 轮中位 72.1 / 6卡 单发复用 80.3（仅 decode 可比，pp 不入 ①；两格置信不同层，别按 11% 差值下结论）。",
    "Monster 4卡 ② = 本次单 boot 连续阶梯（-c 98304），各档可比；§73.2 的 102.2@8K 是 bench3 口径、系统性高 ~14%。S1 int8 的 110K/165K/210K 与 NVFP4 阶梯非同一 ptok，且其 tg 受重复语料+ngram 合成抬升，故只作 ② 的空心存档点、不进 ⑤。",
    "资源账单：VRAM 为运行态 nvidia-smi 采样，RAM 为引擎自报锁页/驻留（S1=experts 驻留，Monster=运行期 host used）。⑦ 精度：exec/boxed 双判分、errors=0、预算越界已 tail 修复（humaneval 96.34→96.95，flipped=1）；smoke 星号仅为轨迹参照、不作结论。",
    "v6 更新（11-22）：③ 短窗/深窗/满窗全部换成出厂 0.1.61 + 停车地板 4096 的实测（含 6 卡双 floor 旁证与 parkpressure 反测）；①②⑤ 阶梯档仍为 0.1.59 旧读，出厂引擎重取是已记账欠账（PERFORMANCE §7/§10d）。",
    "确定性口径：det 三跑 bfd40ecd4692（10K fixture，与 REF.json 同值）为锚；65K 深窗轮间 hash 不稳（R2-R5 稳、R1 随 slot 几何变），只作速率参照、不作确定性证据。",
    "还差的格子与所需窗口（都要独占卡，按纪律等你点头）：Monster 6卡 逐卡 VRAM + 同刻 RAM ≈ 一次 6 卡 boot 20 min（只采样不跑分）；S1 int8 深档自然语料 tg ≈ 一次 4 卡 + 自然长文阶梯 30 min（替掉 ② 的空心存档点）；Monster 4卡/6卡 深并发档（32K×4 以上）与上面同一窗口顺手可取。",
]
fig.text(0.036, 0.012, "\n".join(FOOTER), fontsize=8.3, color=SUB, va="bottom")

OUT = sys.argv[1] if len(sys.argv) > 1 else "ac922-samelen-v6-2026-11-22.png"
fig.savefig(OUT, facecolor=BG)
print("WROTE", OUT)
