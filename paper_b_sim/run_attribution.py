"""生成函数级归因分析：DTLM v3.1 vs GDSF / C2RD-SR"""
import json
import numpy as np
from pathlib import Path

from data_loader import (
    load_memory, forced_tail_sample, load_invocations_for_apps,
    load_triggers,
)
from cold_start import calibrate_cold_start
from analysis import function_attribution
from config import SEED

RESULTS_DIR = Path(__file__).resolve().parent / "results" / "small_matrix_v3_1"
M_RATIOS = [0.3, 0.5, 0.7, 1.0]
BASELINES = ["gdsf", "c2rd_sr"]


def rebuild_functions_info():
    app_mem = load_memory()
    sampled = forced_tail_sample(app_mem, seed=SEED)
    sampled_apps = set(sampled["HashApp"])
    inv = load_invocations_for_apps(sampled_apps)
    triggers = load_triggers()
    func_info_df = inv[["HashApp", "HashFunction"]].drop_duplicates()
    func_info_df = func_info_df.merge(sampled[["HashApp", "m_mb"]], on="HashApp", how="left")
    func_info_df = func_info_df.merge(triggers, on="HashFunction", how="left")
    c_map = calibrate_cold_start(func_info_df)
    functions_info = {}
    for _, row in func_info_df.iterrows():
        fid = row["HashFunction"]
        functions_info[fid] = {"m_i": row["m_mb"], "c_i": c_map.get(fid, 350)}
    return functions_info


def build_hotness_labels_from_results():
    """从 GDSF M=0.3 的 per_function_stats 构建全局热度标签"""
    path = RESULTS_DIR / "gdsf_0.3.json"
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    pfs = data["metrics"]["per_function_stats"]
    counts = {fid: entry["request_count"] for fid, entry in pfs.items()}
    values = np.array(list(counts.values()), dtype=float)
    p50 = float(np.quantile(values, 0.50))
    p90 = float(np.quantile(values, 0.90))
    labels = {}
    for fid, c in counts.items():
        if c >= p90:
            labels[fid] = "hot"
        elif c >= p50:
            labels[fid] = "warm"
        else:
            labels[fid] = "cold"
    print(f"Hotness: P50={p50:.0f} P90={p90:.0f} hot={sum(1 for v in labels.values() if v=='hot')} "
          f"warm={sum(1 for v in labels.values() if v=='warm')} cold={sum(1 for v in labels.values() if v=='cold')}")
    return labels


def load_per_function_stats(policy, m_ratio):
    path = RESULTS_DIR / f"{policy}_{m_ratio}.json"
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["metrics"]["per_function_stats"]


def format_float(v, width=10):
    return f"{v:.1f}".rjust(width)


def truncate_id(fid, length=16):
    return fid[:length] + "..."


def run():
    print("=== 函数级归因分析 ===")
    functions_info = rebuild_functions_info()
    hotness_labels = build_hotness_labels_from_results()

    # 对每个 M_ratio 生成归因 JSON
    all_results = {}  # (m_ratio, baseline) -> attribution dict
    for m_ratio in M_RATIOS:
        dtlm_stats = load_per_function_stats("dtlm", m_ratio)
        payload = {"M_ratio": m_ratio, "comparisons": {}}
        for bl in BASELINES:
            bl_stats = load_per_function_stats(bl, m_ratio)
            attr = function_attribution(dtlm_stats, bl_stats, functions_info, hotness_labels)
            payload["comparisons"][bl] = attr
            all_results[(m_ratio, bl)] = attr
            net = attr["summary"]["net_delta_cost"]
            print(f"M={m_ratio} DTLM vs {bl}: net_delta={net:.1f} "
                  f"beneficial={attr['summary']['beneficial_count']} "
                  f"harmful={attr['summary']['harmful_count']}")

        out_path = RESULTS_DIR / f"attribution_M{m_ratio}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        print(f"SAVED: {out_path}")

    # 生成 attribution_summary.md
    lines = []
    lines.append("# 函数级归因分析摘要")
    lines.append("")
    lines.append("DTLM v3.1 vs baselines，基于 small_matrix_v3_1 结果。")
    lines.append(f"函数总数: {len(functions_info)}, 热度分档: hot≥P90, warm≥P50, cold<P50")
    lines.append("")

    # 表1: DTLM vs GDSF 热度分档净效应
    lines.append("## 表1: DTLM vs GDSF 热度分档净效应 (net_delta_cost)")
    lines.append("")
    lines.append("| M_ratio | hot | warm | cold | net_total |")
    lines.append("|---------|-----|------|------|-----------|")
    for m_ratio in M_RATIOS:
        attr = all_results[(m_ratio, "gdsf")]
        h = attr["net_effect_by_hotness"]
        net = attr["summary"]["net_delta_cost"]
        lines.append(f"| {m_ratio} | {h['hot']['total_delta_cost']:.1f} | "
                     f"{h['warm']['total_delta_cost']:.1f} | "
                     f"{h['cold']['total_delta_cost']:.1f} | {net:.1f} |")
    lines.append("")

    # 表2: DTLM vs C2RD-SR 热度分档净效应
    lines.append("## 表2: DTLM vs C2RD-SR 热度分档净效应 (net_delta_cost)")
    lines.append("")
    lines.append("| M_ratio | hot | warm | cold | net_total |")
    lines.append("|---------|-----|------|------|-----------|")
    for m_ratio in M_RATIOS:
        attr = all_results[(m_ratio, "c2rd_sr")]
        h = attr["net_effect_by_hotness"]
        net = attr["summary"]["net_delta_cost"]
        lines.append(f"| {m_ratio} | {h['hot']['total_delta_cost']:.1f} | "
                     f"{h['warm']['total_delta_cost']:.1f} | "
                     f"{h['cold']['total_delta_cost']:.1f} | {net:.1f} |")
    lines.append("")

    # 表3/表4: M=0.7 和 M=1.0 的 top beneficial/harmful
    for m_ratio, table_num in [(0.7, 3), (1.0, 4)]:
        attr = all_results[(m_ratio, "gdsf")]
        lines.append(f"## 表{table_num}: M={m_ratio} DTLM vs GDSF top beneficial / harmful 函数")
        lines.append("")

        # beneficial
        lines.append("### Top 5 Beneficial (DTLM 更好)")
        lines.append("")
        lines.append("| func_id | delta_cost | hotness | memory_mb | c_i (ms) | requests | dtlm_cold | gdsf_cold |")
        lines.append("|---------|-----------|---------|-----------|----------|----------|-----------|-----------|")
        for row in attr["top_10_beneficial"][:5]:
            lines.append(f"| {truncate_id(row['func_id'])} | {row['delta_cost']:.1f} | "
                         f"{row['hotness']} | {row['memory_mb']:.1f} | "
                         f"{row['cold_start_cost_ms']:.0f} | {row['request_count']} | "
                         f"{row['dtlm_cold_count']} | {row['baseline_cold_count']} |")
        lines.append("")

        # harmful
        lines.append("### Top 5 Harmful (DTLM 更差)")
        lines.append("")
        lines.append("| func_id | delta_cost | hotness | memory_mb | c_i (ms) | requests | dtlm_cold | gdsf_cold |")
        lines.append("|---------|-----------|---------|-----------|----------|----------|-----------|-----------|")
        for row in attr["top_10_harmful"][:5]:
            lines.append(f"| {truncate_id(row['func_id'])} | {row['delta_cost']:.1f} | "
                         f"{row['hotness']} | {row['memory_mb']:.1f} | "
                         f"{row['cold_start_cost_ms']:.0f} | {row['request_count']} | "
                         f"{row['dtlm_cold_count']} | {row['baseline_cold_count']} |")
        lines.append("")

    # 结论
    lines.append("## 结论")
    lines.append("")

    # 自动化结论生成：基于数据
    conclusions = _generate_conclusions(all_results, functions_info)
    for i, c in enumerate(conclusions, 1):
        lines.append(f"{i}. {c}")
    lines.append("")

    summary_path = RESULTS_DIR / "attribution_summary.md"
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"SAVED: {summary_path}")
    print("=== 归因分析完成 ===")


def _generate_conclusions(all_results, functions_info):
    """基于归因数据自动生成结论"""
    conclusions = []

    # Q1: 收益主要集中在哪类函数？
    gdsf_07 = all_results[(0.7, "gdsf")]["net_effect_by_hotness"]
    hotness_order = sorted(gdsf_07.items(), key=lambda x: x[1]["total_delta_cost"])
    most_beneficial_class = hotness_order[0][0]
    conclusions.append(
        f"在 M=0.7 时，DTLM vs GDSF 的收益主要集中在 {most_beneficial_class} 函数"
        f"（net_delta_cost={hotness_order[0][1]['total_delta_cost']:.1f}），"
        f"涉及 {hotness_order[0][1]['function_count']} 个函数。"
    )

    # Q2: 收益是否来自小内存、高 c_i 函数？
    beneficial_07 = all_results[(0.7, "gdsf")]["top_10_beneficial"]
    if beneficial_07:
        avg_mem = np.mean([r["memory_mb"] for r in beneficial_07])
        avg_ci = np.mean([r["cold_start_cost_ms"] for r in beneficial_07])
        all_mem = np.mean([info["m_i"] for info in functions_info.values()])
        all_ci = np.mean([info["c_i"] for info in functions_info.values()])
        mem_ratio = avg_mem / all_mem if all_mem > 0 else 0
        ci_ratio = avg_ci / all_ci if all_ci > 0 else 0
        conclusions.append(
            f"M=0.7 top beneficial 函数的平均内存 {avg_mem:.1f}MB"
            f"（全局均值 {all_mem:.1f}MB，比值 {mem_ratio:.2f}x），"
            f"平均 c_i={avg_ci:.0f}ms（全局均值 {all_ci:.0f}ms，比值 {ci_ratio:.2f}x）。"
        )

    # Q3: 负面影响落在哪类函数？
    harmful_07 = all_results[(0.7, "gdsf")]["top_10_harmful"]
    if harmful_07:
        harmful_hotness = [r["hotness"] for r in harmful_07]
        from collections import Counter
        hc = Counter(harmful_hotness)
        dominant = hc.most_common(1)[0]
        conclusions.append(
            f"M=0.7 的 harmful 函数中，{dominant[0]} 类占 {dominant[1]}/{len(harmful_07)}，"
            f"DTLM 的负面影响主要落在 {dominant[0]} 函数。"
        )

    # Q4: vs GDSF 和 vs C2RD-SR 归因图谱是否一致？
    for m_ratio in [0.7]:
        g_net = all_results[(m_ratio, "gdsf")]["net_effect_by_hotness"]
        c_net = all_results[(m_ratio, "c2rd_sr")]["net_effect_by_hotness"]
        g_order = sorted(g_net.keys(), key=lambda k: g_net[k]["total_delta_cost"])
        c_order = sorted(c_net.keys(), key=lambda k: c_net[k]["total_delta_cost"])
        consistent = g_order == c_order
        conclusions.append(
            f"M={m_ratio} 时，DTLM vs GDSF 与 vs C2RD-SR 的热度分档净效应排序"
            f"{'一致' if consistent else '不一致'}（GDSF: {g_order}, C2RD-SR: {c_order}），"
            f"归因图谱{'具有' if consistent else '不具有'}跨 baseline 一致性。"
        )

    # Q5: M=0.7 vs M=1.0 收益来源变化
    g_07 = all_results[(0.7, "gdsf")]["net_effect_by_hotness"]
    g_10 = all_results[(1.0, "gdsf")]["net_effect_by_hotness"]
    shift_lines = []
    for h in ["hot", "warm", "cold"]:
        d07 = g_07[h]["total_delta_cost"]
        d10 = g_10[h]["total_delta_cost"]
        direction = "改善" if d10 < d07 else "恶化" if d10 > d07 else "不变"
        shift_lines.append(f"{h}: {d07:.1f}→{d10:.1f}({direction})")
    conclusions.append(
        f"从 M=0.7 到 M=1.0，各热度分档净效应变化：{'; '.join(shift_lines)}。"
    )

    # Q6: 总体净效应趋势
    net_by_m = [(m, all_results[(m, "gdsf")]["summary"]["net_delta_cost"]) for m in M_RATIOS]
    best_m = min(net_by_m, key=lambda x: x[1])
    worst_m = max(net_by_m, key=lambda x: x[1])
    conclusions.append(
        f"DTLM vs GDSF 的总净效应随 M 变化：最优在 M={best_m[0]}（Δ={best_m[1]:.1f}），"
        f"最差在 M={worst_m[0]}（Δ={worst_m[1]:.1f}）。"
    )

    # Q7: changed_functions 比例
    for m_ratio in [0.3, 1.0]:
        s = all_results[(m_ratio, "gdsf")]["summary"]
        pct = s["changed_functions"] / s["total_functions"] * 100 if s["total_functions"] > 0 else 0
        conclusions.append(
            f"M={m_ratio} 时，{s['changed_functions']}/{s['total_functions']} "
            f"({pct:.0f}%) 函数的 cold-start cost 受 DTLM 影响。"
        )

    return conclusions


if __name__ == "__main__":
    run()
