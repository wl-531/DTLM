"""EI-GDSF verification: DTLMExpInf / DTLM v3.1 / GDSF × M_ratio {0.1, 0.3, 0.5, 1.0}

Side-branch sanity check. Does not modify any existing files.
Registers DTLMExpInf into runner.POLICY_MAP at runtime, then uses run_single().
"""
import json
import time
from pathlib import Path

import runner
from runner import prepare_data, run_single
from policies.dtlm_ei import DTLMExpInf

# --- 注册 EI-GDSF 到 runner（运行时注入，不改文件） ---
runner.POLICY_MAP["ei_dtlm"] = DTLMExpInf

# --- 实验参数 ---
POLICIES = ["ei_dtlm", "dtlm", "gdsf"]
M_RATIOS = [0.1, 0.3, 0.5, 1.0]
COLD_START_SCALE = 1.0
SEED = 42
DAYS = (3, 12)
WORKING_SET_DAYS = (5, 12)
WARMUP_DAYS = 2

_PROJECT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = _PROJECT_DIR / "results" / "ei_gdsf_verification"

# --- DTLM v3.1 参数（EI-GDSF 和 DTLM 共用） ---
_DTLM_V31_TAUS = {
    "tau_hot_ms": 1200000,
    "tau_warm_ms": 360000,
    "tau_cold_ms": 120000,
}

def _load_dtlm_v31_kwargs():
    taus = dict(_DTLM_V31_TAUS)
    params_path = _PROJECT_DIR / "results" / "dtlm_v3" / "selected_params.json"
    if params_path.exists():
        with open(params_path, "r", encoding="utf-8") as f:
            saved = json.load(f)
        for key in ("tau_hot_ms", "tau_warm_ms", "tau_cold_ms"):
            if key in saved:
                taus[key] = saved[key]
        print(f"Loaded tau values from {params_path}: {taus}")
    else:
        print(f"selected_params.json not found, using fallback: {taus}")
    return {
        "physical_delete_requires_pressure": True,
        "p_deactivate": 0.95,
        "hot_threshold": 10,
        "warm_threshold": 1,
        "t_protect_ms": 60000,
        "ttl_scan_interval_ms": 60000,
        **taus,
    }

_V31_KWARGS = _load_dtlm_v31_kwargs()

POLICY_KWARGS = {
    "ei_dtlm": _V31_KWARGS,
    "dtlm": _V31_KWARGS,
}


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    data = prepare_data(seed=SEED, days=DAYS, working_set_days=WORKING_SET_DAYS)

    total = len(POLICIES) * len(M_RATIOS)
    count = 0
    results = {}

    for m_ratio in M_RATIOS:
        for policy_name in POLICIES:
            count += 1
            output_path = OUTPUT_DIR / f"{policy_name}_{m_ratio}.json"
            label = f"{policy_name} M={m_ratio}"

            print(f"\n{'='*60}")
            print(f"[{count}/{total}] {label}")
            print(f"{'='*60}")

            t0 = time.time()
            result = run_single(
                data,
                policy_name,
                m_ratio,
                cold_start_scale=COLD_START_SCALE,
                output_path=str(output_path),
                warmup_days=WARMUP_DAYS,
                policy_kwargs=POLICY_KWARGS.get(policy_name),
            )
            elapsed = time.time() - t0

            results[(policy_name, m_ratio)] = result
            cost = result["metrics"]["total_cold_start_cost"]
            csr = result["metrics"]["cold_start_rate"]
            print(f"  CSR={csr*100:.2f}% Cost={cost:.0f} Time={elapsed:.1f}s")

    build_summary(results)
    print(f"\nSummary written to {OUTPUT_DIR / 'summary.md'}")


def build_summary(results):
    lines = []
    lines.append("# EI-GDSF Verification Results\n")
    lines.append(f"Policies: EI-DTLM, DTLM v3.1, GDSF | M_ratio: {M_RATIOS} | seed: {SEED}\n")

    # --- 表1: Cost 对比 ---
    lines.append("## 1. Cost Comparison\n")
    lines.append("| M_ratio | EI-DTLM | DTLM v3.1 | GDSF | EI/v3.1 | EI/GDSF | v3.1/GDSF |")
    lines.append("|---------|---------|-----------|------|---------|---------|-----------|")
    for m in M_RATIOS:
        ei_cost = results[("ei_dtlm", m)]["metrics"]["total_cold_start_cost"]
        dtlm_cost = results[("dtlm", m)]["metrics"]["total_cold_start_cost"]
        gdsf_cost = results[("gdsf", m)]["metrics"]["total_cold_start_cost"]
        r_ei_dtlm = ei_cost / dtlm_cost if dtlm_cost else float("inf")
        r_ei_gdsf = ei_cost / gdsf_cost if gdsf_cost else float("inf")
        r_dtlm_gdsf = dtlm_cost / gdsf_cost if gdsf_cost else float("inf")
        lines.append(
            f"| {m} | {ei_cost:.0f} | {dtlm_cost:.0f} | {gdsf_cost:.0f} "
            f"| {r_ei_dtlm:.4f} | {r_ei_gdsf:.4f} | {r_dtlm_gdsf:.4f} |"
        )

    # --- 表2: 冷启动分解 ---
    lines.append("\n## 2. Cold Start Breakdown\n")
    lines.append("| Policy | M_ratio | initial(n/cost) | expiry_induced(n/cost) | eviction_induced(n/cost) |")
    lines.append("|--------|---------|-----------------|------------------------|--------------------------|")
    for policy in POLICIES:
        display = {"ei_dtlm": "EI-DTLM", "dtlm": "DTLM v3.1", "gdsf": "GDSF"}[policy]
        for m in M_RATIOS:
            bd = results[(policy, m)]["metrics"].get("cold_start_breakdown")
            if bd:
                lines.append(
                    f"| {display} | {m} "
                    f"| {bd['initial_cold_starts']}/{bd['initial_cold_cost']:.0f} "
                    f"| {bd['expiry_induced_cold_starts']}/{bd['expiry_induced_cold_cost']:.0f} "
                    f"| {bd['eviction_induced_cold_starts']}/{bd['eviction_induced_cold_cost']:.0f} |"
                )
            else:
                lines.append(f"| {display} | {m} | N/A | N/A | N/A |")

    # --- 表3: 删除时刻利用率 (EI-DTLM vs DTLM) ---
    lines.append("\n## 3. Deletion-Time Utilization (EI-DTLM vs DTLM v3.1)\n")
    lines.append("| Policy | M_ratio | Type | count | mean | median |")
    lines.append("|--------|---------|------|-------|------|--------|")
    for policy in ["ei_dtlm", "dtlm"]:
        display = "EI-DTLM" if policy == "ei_dtlm" else "DTLM v3.1"
        for m in M_RATIOS:
            util = results[(policy, m)]["metrics"].get("utilization_stats")
            if util and "deletion_time" in util:
                dt = util["deletion_time"]
                for reason in ["expiry", "eviction"]:
                    d = dt[reason]
                    lines.append(
                        f"| {display} | {m} | {reason} | {d['count']} "
                        f"| {d['mean']:.4f} | {d['median']:.4f} |"
                    )

    # --- 表4: DTLM get_state 对比 ---
    lines.append("\n## 4. Final State (logically_expired_count)\n")
    lines.append("| Policy | M_ratio | logically_expired_count | ttl_reclaim | eviction_count |")
    lines.append("|--------|---------|-------------------------|-------------|----------------|")
    for policy in ["ei_dtlm", "dtlm"]:
        display = "EI-DTLM" if policy == "ei_dtlm" else "DTLM v3.1"
        for m in M_RATIOS:
            fpath = OUTPUT_DIR / f"{policy}_{m}.json"
            if fpath.exists():
                with open(fpath, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                # get_state is not persisted by run_single; use deletion_log counts instead
                metrics = raw["metrics"]
                bd = metrics.get("cold_start_breakdown", {})
                util = metrics.get("utilization_stats", {})
                dt_eviction = util.get("deletion_time", {}).get("eviction", {})
                dt_expiry = util.get("deletion_time", {}).get("expiry", {})
                lines.append(
                    f"| {display} | {m} | (end-state not persisted) "
                    f"| {dt_expiry.get('count', 'N/A')} | {dt_eviction.get('count', 'N/A')} |"
                )

    # --- 止损条件检查 ---
    lines.append("\n## 5. Stop-Loss Checks\n")

    checks = []

    # 条件1: M=0.1 cost ratio ≤ 1.02
    ei_01 = results[("ei_dtlm", 0.1)]["metrics"]["total_cold_start_cost"]
    dtlm_01 = results[("dtlm", 0.1)]["metrics"]["total_cold_start_cost"]
    ratio_01 = ei_01 / dtlm_01 if dtlm_01 else float("inf")
    pass_1 = ratio_01 <= 1.02
    checks.append(("Cond 1", f"M=0.1 EI/v3.1 = {ratio_01:.4f} <= 1.02", pass_1))

    # 条件2: M=1.0 cost(EI) ≤ cost(v3.1)
    ei_10 = results[("ei_dtlm", 1.0)]["metrics"]["total_cold_start_cost"]
    dtlm_10 = results[("dtlm", 1.0)]["metrics"]["total_cold_start_cost"]
    pass_2 = ei_10 <= dtlm_10
    checks.append(("Cond 2", f"M=1.0 EI={ei_10:.0f} <= v3.1={dtlm_10:.0f}", pass_2))

    # 条件3: M=0.3 or M=0.5 ratio < 0.98
    ei_03 = results[("ei_dtlm", 0.3)]["metrics"]["total_cold_start_cost"]
    dtlm_03 = results[("dtlm", 0.3)]["metrics"]["total_cold_start_cost"]
    ratio_03 = ei_03 / dtlm_03 if dtlm_03 else float("inf")
    ei_05 = results[("ei_dtlm", 0.5)]["metrics"]["total_cold_start_cost"]
    dtlm_05 = results[("dtlm", 0.5)]["metrics"]["total_cold_start_cost"]
    ratio_05 = ei_05 / dtlm_05 if dtlm_05 else float("inf")
    pass_3 = ratio_03 < 0.98 or ratio_05 < 0.98
    checks.append(("Cond 3", f"M=0.3 EI/v3.1={ratio_03:.4f}, M=0.5 EI/v3.1={ratio_05:.4f}, need either < 0.98", pass_3))

    for name, desc, passed in checks:
        status = "PASS" if passed else "FAIL"
        lines.append(f"- **{name}**: {desc} → **{status}**")

    all_pass = all(p for _, _, p in checks)
    lines.append(f"\n**Overall (Cond 1-3): {'ALL PASS' if all_pass else 'NOT ALL PASS'}**")
    if not all_pass:
        lines.append("\nEI-GDSF does not meet stop-loss criteria. No further action needed.")
    else:
        lines.append("\nCond 1-3 passed. Proceed to Step 4 (code review) before full-matrix promotion.")

    with open(OUTPUT_DIR / "summary.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    main()
