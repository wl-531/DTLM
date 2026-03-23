"""EI-GDSF Step 4: Attribution analysis for M={0.3, 0.5}.

Compares EI-DTLM vs GDSF attribution against existing v3.1 vs GDSF attribution.
Appends results to results/ei_gdsf_verification/summary.md.
"""
import json
from collections import Counter
from pathlib import Path

from analysis import function_attribution
from run_attribution import rebuild_functions_info, build_hotness_labels_from_results

_PROJECT_DIR = Path(__file__).resolve().parent
EI_DIR = _PROJECT_DIR / "results" / "ei_gdsf_verification"
V31_DIR = _PROJECT_DIR / "results" / "small_matrix_v3_1"
M_RATIOS = [0.3, 0.5]


def load_pfs(directory, policy, m_ratio):
    path = directory / f"{policy}_{m_ratio}.json"
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["metrics"]["per_function_stats"]


def load_v31_attribution(m_ratio):
    path = V31_DIR / f"attribution_M{m_ratio}.json"
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["comparisons"]["gdsf"]


def main():
    print("=== EI-GDSF Attribution Analysis ===")
    functions_info = rebuild_functions_info()

    # Build hotness labels from v3.1 GDSF results (same as run_attribution.py)
    hotness_labels = build_hotness_labels_from_results()

    lines = []
    lines.append("\n---\n")
    lines.append("## 6. Attribution Analysis (Step 4)\n")
    lines.append(f"Comparing EI-DTLM vs GDSF attribution against v3.1 vs GDSF, M={{{', '.join(str(m) for m in M_RATIOS)}}}\n")
    lines.append("**Calibration note**: hotness labels from v3.1 GDSF M=0.3 per_function_stats (same as run_attribution.py).\n")

    all_checks = []  # (name, desc, pass/fail/uncertain)

    for m in M_RATIOS:
        lines.append(f"### M={m}\n")

        # --- EI-DTLM vs GDSF attribution ---
        ei_pfs = load_pfs(EI_DIR, "ei_dtlm", m)
        gdsf_pfs = load_pfs(EI_DIR, "gdsf", m)
        ei_attr = function_attribution(ei_pfs, gdsf_pfs, functions_info, hotness_labels)

        # --- v3.1 vs GDSF attribution (from existing results) ---
        v31_attr = load_v31_attribution(m)

        ei_s = ei_attr["summary"]
        v31_s = v31_attr["summary"]

        ei_harmful = [r for r in ei_attr["top_10_harmful"]]
        v31_harmful = [r for r in v31_attr["top_10_harmful"]]

        # Full harmful lists (not just top 10) — recompute from raw data
        # function_attribution returns top_10_harmful but summary has harmful_count
        # We need full harmful total cost, so recompute
        ei_dtlm_pfs_all = ei_pfs
        gdsf_pfs_all = gdsf_pfs
        ei_harmful_total = 0.0
        ei_harmful_funcs = []
        for fid in set(ei_dtlm_pfs_all) | set(gdsf_pfs_all) | set(functions_info):
            ei_cost = float(ei_dtlm_pfs_all.get(fid, {}).get("cold_start_cost", 0.0))
            g_cost = float(gdsf_pfs_all.get(fid, {}).get("cold_start_cost", 0.0))
            delta = ei_cost - g_cost
            if delta > 0:
                hotness = hotness_labels.get(fid, "cold")
                baseline_cold = int(gdsf_pfs_all.get(fid, {}).get("cold_start_count", 0))
                ei_harmful_total += delta
                ei_harmful_funcs.append({
                    "func_id": fid, "delta_cost": delta, "hotness": hotness,
                    "baseline_cold_count": baseline_cold,
                })

        # Same for v3.1
        v31_dtlm_pfs = load_pfs(V31_DIR, "dtlm", m)
        v31_gdsf_pfs = load_pfs(V31_DIR, "gdsf", m)
        v31_harmful_total = 0.0
        v31_harmful_funcs = []
        for fid in set(v31_dtlm_pfs) | set(v31_gdsf_pfs) | set(functions_info):
            v31_cost = float(v31_dtlm_pfs.get(fid, {}).get("cold_start_cost", 0.0))
            g_cost = float(v31_gdsf_pfs.get(fid, {}).get("cold_start_cost", 0.0))
            delta = v31_cost - g_cost
            if delta > 0:
                hotness = hotness_labels.get(fid, "cold")
                baseline_cold = int(v31_gdsf_pfs.get(fid, {}).get("cold_start_count", 0))
                v31_harmful_total += delta
                v31_harmful_funcs.append({
                    "func_id": fid, "delta_cost": delta, "hotness": hotness,
                    "baseline_cold_count": baseline_cold,
                })

        # gdsf_cold=0 counts
        ei_gdsf_cold_zero = sum(1 for r in ei_harmful_funcs if r["baseline_cold_count"] == 0)
        v31_gdsf_cold_zero = sum(1 for r in v31_harmful_funcs if r["baseline_cold_count"] == 0)

        # Hotness distribution of harmful funcs
        ei_hotness_dist = Counter(r["hotness"] for r in ei_harmful_funcs)
        v31_hotness_dist = Counter(r["hotness"] for r in v31_harmful_funcs)

        # New harmful funcs from hot tier
        ei_harmful_ids = set(r["func_id"] for r in ei_harmful_funcs)
        v31_harmful_ids = set(r["func_id"] for r in v31_harmful_funcs)
        new_harmful = ei_harmful_ids - v31_harmful_ids
        new_harmful_hot = [r for r in ei_harmful_funcs if r["func_id"] in new_harmful and r["hotness"] == "hot"]

        # --- Output table ---
        lines.append("| Metric | EI-DTLM vs GDSF | v3.1 vs GDSF |")
        lines.append("|--------|-----------------|--------------|")
        lines.append(f"| net_delta_cost | {ei_s['net_delta_cost']:.0f} | {v31_s['net_delta_cost']:.0f} |")
        lines.append(f"| harmful_count (all) | {len(ei_harmful_funcs)} | {len(v31_harmful_funcs)} |")
        lines.append(f"| harmful_total_cost (all) | {ei_harmful_total:.0f} | {v31_harmful_total:.0f} |")
        lines.append(f"| beneficial_count | {ei_s['beneficial_count']} | {v31_s['beneficial_count']} |")
        lines.append(f"| gdsf_cold=0 in harmful | {ei_gdsf_cold_zero}/{len(ei_harmful_funcs)} | {v31_gdsf_cold_zero}/{len(v31_harmful_funcs)} |")
        lines.append(f"| harmful hotness (hot/warm/cold) | {ei_hotness_dist.get('hot',0)}/{ei_hotness_dist.get('warm',0)}/{ei_hotness_dist.get('cold',0)} | {v31_hotness_dist.get('hot',0)}/{v31_hotness_dist.get('warm',0)}/{v31_hotness_dist.get('cold',0)} |")
        lines.append(f"| new harmful (not in v3.1) | {len(new_harmful)} | — |")
        lines.append(f"| new harmful from hot | {len(new_harmful_hot)} | — |")
        lines.append("")

        # Top 5 harmful for EI-DTLM
        ei_harmful_funcs.sort(key=lambda r: -r["delta_cost"])
        lines.append(f"**EI-DTLM top 5 harmful (M={m})**:\n")
        lines.append("| func_id | delta_cost | hotness | gdsf_cold |")
        lines.append("|---------|-----------|---------|-----------|")
        for r in ei_harmful_funcs[:5]:
            fid_short = r["func_id"][:16] + "..."
            lines.append(f"| {fid_short} | {r['delta_cost']:.0f} | {r['hotness']} | {r['baseline_cold_count']} |")
        lines.append("")

        # --- Condition checks ---
        # (a) harmful_total_cost(EI) <= harmful_total_cost(v3.1)
        pass_a = ei_harmful_total <= v31_harmful_total
        all_checks.append((f"Cond 4a M={m}", f"harmful_total: EI={ei_harmful_total:.0f} <= v3.1={v31_harmful_total:.0f}", pass_a))

        # (b) harmful_count(EI) <= harmful_count(v3.1)
        pass_b = len(ei_harmful_funcs) <= len(v31_harmful_funcs)
        all_checks.append((f"Cond 4b M={m}", f"harmful_count: EI={len(ei_harmful_funcs)} <= v3.1={len(v31_harmful_funcs)}", pass_b))

        # (c) no new harmful from hot
        pass_c = len(new_harmful_hot) == 0
        all_checks.append((f"Cond 4c M={m}", f"new harmful hot funcs: {len(new_harmful_hot)}", pass_c))

    # --- Stop-loss summary ---
    lines.append("### Stop-Loss Checks (Condition 4)\n")
    for name, desc, passed in all_checks:
        status = "PASS" if passed else "FAIL"
        lines.append(f"- **{name}**: {desc} → **{status}**")

    cond4_pass = all(p for _, _, p in all_checks)
    lines.append(f"\n**Condition 4 overall: {'ALL PASS' if cond4_pass else 'NOT ALL PASS'}**\n")

    # --- Calibration note ---
    lines.append("### Calibration Note\n")
    lines.append("- EI-DTLM and v3.1 attribution use the same `function_attribution()` from `analysis.py`")
    lines.append("- Hotness labels from `build_hotness_labels_from_results()` in `run_attribution.py` (P50/P90 quantiles on GDSF M=0.3)")
    lines.append("- GDSF results for EI-DTLM come from `ei_gdsf_verification/gdsf_*.json` (re-run), v3.1 from `small_matrix_v3_1/gdsf_*.json`")
    lines.append("- Both GDSF runs use identical parameters; any minor cost difference would indicate non-determinism (check seed)")
    lines.append("")

    # Verify GDSF consistency
    for m in M_RATIOS:
        ei_gdsf_cost = float(load_pfs(EI_DIR, "gdsf", m).get(list(load_pfs(EI_DIR, "gdsf", m).keys())[0], {}).get("cold_start_cost", -1))
        # Just compare total costs
        with open(EI_DIR / f"gdsf_{m}.json") as f:
            ei_total = json.load(f)["metrics"]["total_cold_start_cost"]
        with open(V31_DIR / f"gdsf_{m}.json") as f:
            v31_total = json.load(f)["metrics"]["total_cold_start_cost"]
        match = "MATCH" if ei_total == v31_total else f"MISMATCH (ei={ei_total}, v31={v31_total})"
        lines.append(f"- GDSF M={m} total_cost: {match}")

    # Append to summary.md
    summary_path = EI_DIR / "summary.md"
    with open(summary_path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"\nAttribution results appended to {summary_path}")
    print(f"Condition 4: {'ALL PASS' if cond4_pass else 'NOT ALL PASS'}")
    for name, desc, passed in all_checks:
        print(f"  {name}: {desc} -> {'PASS' if passed else 'FAIL'}")


if __name__ == "__main__":
    main()
