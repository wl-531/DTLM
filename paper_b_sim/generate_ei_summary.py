"""EI-GDSF 全量实验汇总：CSV + 图表。

数据来源：
- 8 baselines: results/baseline/ 和 results/sensitivity/（v3.6 既有）
- EI-DTLM:    results/ei_baseline/ 和 results/ei_sensitivity/（新跑）

产出：
- results/ei_cost_vs_M.csv
- results/ei_summary_table.csv
- results/ei_cost_vs_M_plot.png
- results/ei_sensitivity_plot.png
- results/ei_vs_v31_comparison.csv
"""

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

RESULTS_DIR = Path(__file__).resolve().parent / "results"
BASELINE_DIR = RESULTS_DIR / "baseline"
SENSITIVITY_DIR = RESULTS_DIR / "sensitivity"
EI_BASELINE_DIR = RESULTS_DIR / "ei_baseline"
EI_SENSITIVITY_DIR = RESULTS_DIR / "ei_sensitivity"

# 8 baselines（与 phase10.py 一致，不含 dtlm）
BASELINES = [
    "lru", "lfu", "fixed_ttl_lru", "gdsf",
    "iat_adaptive_ttl", "adaptive_ttl_lru", "ttlmin_extnd", "c2rd_sr",
]

# 全部策略（EI-DTLM 替代 DTLM）
ALL_POLICIES = BASELINES + ["ei_dtlm"]

DISPLAY_NAMES = {
    "lru": "LRU",
    "lfu": "LFU",
    "fixed_ttl_lru": "Fixed-TTL+LRU",
    "gdsf": "GDSF",
    "iat_adaptive_ttl": "IAT-Adaptive TTL (Adm.)",
    "adaptive_ttl_lru": "Adaptive-TTL+LRU",
    "ttlmin_extnd": "TTLmin_extnd",
    "c2rd_sr": "C2RD-SR",
    "ei_dtlm": "EI-DTLM",
}

BASELINE_M_RATIOS = [0.1, 0.2, 0.3, 0.5, 0.7, 1.0]
# sensitivity 交集：v3.6 baselines 有 {0.2,0.3,0.5}，EI-DTLM 有 {0.3,0.5,0.7}
SENSITIVITY_M_RATIOS = [0.3, 0.5]
COLD_START_SCALES = [0.5, 1.0, 2.0]


def fmt(v):
    return f"{v:.1f}"


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def safe_load(path):
    if not path.exists():
        return None
    try:
        return load_json(path)
    except Exception:
        return None


def baseline_path(policy, m_ratio):
    """8 baselines 在 results/baseline/，EI-DTLM 在 results/ei_baseline/"""
    if policy == "ei_dtlm":
        return EI_BASELINE_DIR / f"ei_dtlm_{fmt(m_ratio)}.json"
    return BASELINE_DIR / f"{policy}_{fmt(m_ratio)}.json"


def sensitivity_path(policy, m_ratio, scale):
    if policy == "ei_dtlm":
        return EI_SENSITIVITY_DIR / f"ei_dtlm_{fmt(m_ratio)}_{fmt(scale)}.json"
    return SENSITIVITY_DIR / f"{policy}_{fmt(m_ratio)}_{fmt(scale)}.json"


def cost(result):
    return result["metrics"]["total_cold_start_cost"]


def csr(result):
    return result["metrics"]["cold_start_rate"]


def mem_util(result):
    return result["metrics"]["avg_memory_utilization"]


# ─── 4.1 汇总 CSV ───

def generate_cost_vs_M_csv():
    """生成 ei_cost_vs_M.csv，格式同 cost_vs_M.csv"""
    rows = []
    for m_ratio in BASELINE_M_RATIOS:
        row = {"M_ratio": m_ratio}
        for policy in ALL_POLICIES:
            result = safe_load(baseline_path(policy, m_ratio))
            row[policy] = cost(result) if result else None
        rows.append(row)

    df = pd.DataFrame(rows).set_index("M_ratio")
    out = RESULTS_DIR / "ei_cost_vs_M.csv"
    df.to_csv(out)
    print(f"SAVED: {out} ({len(df)} rows)")
    return df


def generate_summary_table_csv():
    """生成 ei_summary_table.csv，格式同 summary_table.csv"""
    rows = []

    # baseline 部分
    for m_ratio in BASELINE_M_RATIOS:
        for policy in ALL_POLICIES:
            result = safe_load(baseline_path(policy, m_ratio))
            if result is None:
                continue
            rows.append({
                "policy": policy,
                "M_ratio": m_ratio,
                "cold_start_scale": 1.0,
                "CSR": csr(result),
                "total_cost": cost(result),
                "avg_mem_util": mem_util(result),
                "runtime_s": result["metrics"].get("runtime_seconds", 0),
            })

    # sensitivity 部分（排除 scale=1.0 避免重复）
    for m_ratio in SENSITIVITY_M_RATIOS:
        for scale in COLD_START_SCALES:
            if scale == 1.0:
                continue
            for policy in ALL_POLICIES:
                result = safe_load(sensitivity_path(policy, m_ratio, scale))
                if result is None:
                    continue
                rows.append({
                    "policy": policy,
                    "M_ratio": m_ratio,
                    "cold_start_scale": scale,
                    "CSR": csr(result),
                    "total_cost": cost(result),
                    "avg_mem_util": mem_util(result),
                    "runtime_s": result["metrics"].get("runtime_seconds", 0),
                })

    df = pd.DataFrame(rows)
    out = RESULTS_DIR / "ei_summary_table.csv"
    df.to_csv(out, index=False)
    print(f"SAVED: {out} ({len(df)} rows)")
    return df


# ─── 4.2 Cost vs M 图 ───

def generate_cost_vs_M_plot(cost_df):
    plt.figure(figsize=(9, 5))
    for policy in ALL_POLICIES:
        if policy not in cost_df.columns:
            continue
        subset = cost_df[[policy]].dropna()
        if subset.empty:
            continue
        linestyle = "--" if policy == "iat_adaptive_ttl" else "-"
        plt.plot(subset.index, subset[policy], marker="o",
                 linestyle=linestyle, label=DISPLAY_NAMES[policy])

    plt.yscale("log")
    plt.xlabel("M / working_set_size")
    plt.ylabel("Total cold-start cost")
    plt.legend(loc="upper right", fontsize=8)
    plt.tight_layout()
    out = RESULTS_DIR / "ei_cost_vs_M_plot.png"
    plt.savefig(out, dpi=200)
    plt.close()
    print(f"SAVED: {out}")


# ─── 4.3 Sensitivity 图 ───

def generate_sensitivity_plot():
    n_panels = len(SENSITIVITY_M_RATIOS)
    fig, axes = plt.subplots(1, n_panels, figsize=(6.5 * n_panels, 4), sharey=True)
    if n_panels == 1:
        axes = [axes]

    for ax, m_ratio in zip(axes, SENSITIVITY_M_RATIOS):
        for policy in ALL_POLICIES:
            costs_by_scale = []
            scales_valid = []
            for scale in COLD_START_SCALES:
                result = safe_load(sensitivity_path(policy, m_ratio, scale))
                if result is None:
                    continue
                scales_valid.append(scale)
                costs_by_scale.append(cost(result))
            if not costs_by_scale:
                continue
            ax.plot(scales_valid, costs_by_scale, marker="o",
                    label=DISPLAY_NAMES[policy])
        ax.set_title(f"M_ratio={fmt(m_ratio)}")
        ax.set_xlabel("cold_start_scale")
        ax.set_ylabel("Total Cost")

    axes[-1].legend(loc="upper left", bbox_to_anchor=(1.02, 1.0), fontsize=7)
    fig.tight_layout()
    out = RESULTS_DIR / "ei_sensitivity_plot.png"
    fig.savefig(out, dpi=200, bbox_inches="tight")
    plt.close(fig)
    print(f"SAVED: {out}")


# ─── 4.4 EI vs v3.1 逐 M 对比 ───

def generate_comparison_csv():
    rows = []
    for m_ratio in BASELINE_M_RATIOS:
        ei_result = safe_load(EI_BASELINE_DIR / f"ei_dtlm_{fmt(m_ratio)}.json")
        v31_result = safe_load(BASELINE_DIR / f"dtlm_{fmt(m_ratio)}.json")
        if ei_result is None or v31_result is None:
            continue
        ei_c = cost(ei_result)
        v31_c = cost(v31_result)
        rows.append({
            "M_ratio": m_ratio,
            "ei_dtlm_cost": ei_c,
            "v31_dtlm_cost": v31_c,
            "ei_vs_v31_ratio": ei_c / v31_c if v31_c else None,
            "improvement_pct": (v31_c - ei_c) / v31_c * 100 if v31_c else None,
        })

    df = pd.DataFrame(rows)
    out = RESULTS_DIR / "ei_vs_v31_comparison.csv"
    df.to_csv(out, index=False)
    print(f"SAVED: {out}")

    # 打印对比表
    print("\nEI-DTLM vs DTLM v3.1 对比：")
    print(f"{'M_ratio':>8} {'EI-DTLM':>14} {'DTLM v3.1':>14} {'ratio':>8} {'improve%':>10}")
    for _, r in df.iterrows():
        print(f"{r['M_ratio']:>8.1f} {r['ei_dtlm_cost']:>14,.0f} {r['v31_dtlm_cost']:>14,.0f} "
              f"{r['ei_vs_v31_ratio']:>8.4f} {r['improvement_pct']:>+10.2f}%")
    return df


def main():
    print("=" * 60)
    print("EI-GDSF 汇总生成")
    print("=" * 60)

    if SENSITIVITY_M_RATIOS != [0.2, 0.3, 0.5]:
        print(f"注意：sensitivity 使用交集 M_ratios={SENSITIVITY_M_RATIOS}"
              f"（v3.6={[0.2,0.3,0.5]}，EI={[0.3,0.5,0.7]}）")

    print("\n--- 4.1 汇总 CSV ---")
    cost_df = generate_cost_vs_M_csv()
    generate_summary_table_csv()

    print("\n--- 4.2 Cost vs M 图 ---")
    generate_cost_vs_M_plot(cost_df)

    print("\n--- 4.3 Sensitivity 图 ---")
    generate_sensitivity_plot()

    print("\n--- 4.4 EI vs v3.1 对比 ---")
    generate_comparison_csv()

    print("\n汇总生成完成。")


if __name__ == "__main__":
    main()
