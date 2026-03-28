"""生成更新后的论文排名表（含 admission-gated IAT-Adaptive TTL）。

读取所有 baseline + EI-DTLM 结果，输出：
1. 每个 M_ratio 的完整排名（所有 9 个策略，统一口径）
2. EI-DTLM 的排名汇总
3. 与框架文档 旧表的对比
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RESULTS_DIR = ROOT / "results"
BASELINE_DIR = RESULTS_DIR / "baseline"
EI_BASELINE_DIR = RESULTS_DIR / "ei_baseline"

BASELINES = [
    "lru", "lfu", "fixed_ttl_lru", "gdsf",
    "iat_adaptive_ttl", "adaptive_ttl_lru", "ttlmin_extnd", "c2rd_sr",
]
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

M_RATIOS = [0.1, 0.2, 0.3, 0.5, 0.7, 1.0]

# 旧数据（修复前）：EI-DTLM constrained rank
OLD_CONSTRAINED_RANKS = {
    0.1: 3, 0.2: 1, 0.3: 1, 0.5: 1, 0.7: 1, 1.0: 2
}


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
    if policy == "ei_dtlm":
        return EI_BASELINE_DIR / f"ei_dtlm_{fmt(m_ratio)}.json"
    return BASELINE_DIR / f"{policy}_{fmt(m_ratio)}.json"


def cost(result):
    return result["metrics"]["total_cold_start_cost"]


def csr(result):
    return result["metrics"]["cold_start_rate"]


def main():
    lines = ["# 更新后的论文排名表\n"]
    lines.append("IAT-Adaptive TTL 已改为 admission-gated（硬内存约束、无 eviction）。")
    lines.append("所有 9 个策略统一在硬预算下比较，不再区分 constrained/unconstrained rank。\n")

    ei_dtlm_ranks = {}
    ei_dtlm_costs = {}

    for m in M_RATIOS:
        lines.append(f"### M_ratio = {fmt(m)}\n")
        costs_map = {}
        for policy in ALL_POLICIES:
            result = safe_load(baseline_path(policy, m))
            if result is None:
                lines.append(f"  WARNING: missing {policy} M={fmt(m)}")
                continue
            costs_map[policy] = cost(result)

        ranked = sorted(costs_map.items(), key=lambda x: x[1])
        lines.append("| Rank | Policy | Total Cost | CSR |")
        lines.append("|------|--------|------------|-----|")
        for rank, (policy, c) in enumerate(ranked, 1):
            result = safe_load(baseline_path(policy, m))
            csr_val = csr(result) * 100 if result else 0
            marker = " **" if policy == "ei_dtlm" else ""
            lines.append(f"| {rank} | {DISPLAY_NAMES[policy]}{marker} | {c:,.0f} | {csr_val:.2f}% |")
            if policy == "ei_dtlm":
                ei_dtlm_ranks[m] = rank
                ei_dtlm_costs[m] = c
        lines.append("")

    # EI-DTLM 排名汇总
    lines.append("## EI-DTLM 排名汇总\n")
    lines.append("| M_ratio | New Rank | Old Constrained Rank | EI-DTLM Cost |")
    lines.append("|---------|----------|---------------------|--------------|")
    for m in M_RATIOS:
        new_rank = ei_dtlm_ranks.get(m, "?")
        old_rank = OLD_CONSTRAINED_RANKS.get(m, "?")
        ei_cost = ei_dtlm_costs.get(m, 0)
        lines.append(f"| {fmt(m)} | {new_rank} | {old_rank} | {ei_cost:,.0f} |")

    avg_rank = sum(ei_dtlm_ranks.values()) / len(ei_dtlm_ranks) if ei_dtlm_ranks else 0
    lines.append(f"\n平均 rank: {avg_rank:.2f}")

    # IAT-Adaptive TTL (Adm.) 排名
    lines.append("\n## IAT-Adaptive TTL (Adm.) 排名\n")
    lines.append("| M_ratio | Rank | Cost | admission_failure |")
    lines.append("|---------|------|------|-------------------|")
    for m in M_RATIOS:
        result = safe_load(baseline_path("iat_adaptive_ttl", m))
        if result is None:
            continue
        c = cost(result)
        bd = result["metrics"].get("cold_start_breakdown", {})
        af = bd.get("admission_failure_cold_starts", "N/A")
        # find rank
        all_costs = {}
        for policy in ALL_POLICIES:
            r = safe_load(baseline_path(policy, m))
            if r:
                all_costs[policy] = cost(r)
        ranked = sorted(all_costs.items(), key=lambda x: x[1])
        iat_rank = next((i+1 for i, (k, _) in enumerate(ranked) if k == "iat_adaptive_ttl"), "?")
        lines.append(f"| {fmt(m)} | {iat_rank} | {c:,.0f} | {af} |")

    report = "\n".join(lines)
    out = RESULTS_DIR / "updated_ranking_report.md"
    out.write_text(report, encoding="utf-8")
    print(report)
    print(f"\n报告已保存: {out}")


if __name__ == "__main__":
    main()
