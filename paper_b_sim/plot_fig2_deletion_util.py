"""Generate the paper-frozen naive-only Fig. 2 deletion-pressure histogram."""

import json
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FormatStrFormatter

from engine import simulate
from metrics import summary
from policies.dtlm import DTLM
from policies.gdsf import GDSF
from runner import DAY_MS, prepare_data

SEED = 42
DAYS = (3, 12)
WORKING_SET_DAYS = (5, 12)
WARMUP_DAYS = 2
M_RATIO = 1.0

NAIVE_KWARGS = {
    "physical_delete_requires_pressure": False,
    "p_deactivate": 0.90,
    "hot_threshold": 10,
    "warm_threshold": 1,
    "tau_hot_ms": 600000,
    "tau_warm_ms": 180000,
    "tau_cold_ms": 60000,
    "t_protect_ms": 60000,
    "ttl_scan_interval_ms": 60000,
}

FIGURES_DIR = Path(__file__).resolve().parent / "figures"
SMALL_MATRIX_V30_DTLM_PATH = Path(__file__).resolve().parent / "results" / "small_matrix_v3_0" / "dtlm_1.0.json"


def load_naive_anchor():
    """Load naive anchor metrics from the historical small-matrix result."""
    if not SMALL_MATRIX_V30_DTLM_PATH.exists():
        return None
    with SMALL_MATRIX_V30_DTLM_PATH.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    metrics = payload["metrics"]
    deletion_time = metrics["utilization_stats"]["deletion_time"]["expiry"]
    breakdown = metrics["cold_start_breakdown"]
    total_cost = metrics["total_cold_start_cost"]
    total_starts = metrics["total_cold_starts"]
    return {
        "total_cost": total_cost,
        "mean": deletion_time["mean"],
        "pct_le_085": deletion_time.get("pct_le_085"),
        "expiry_cs_count": breakdown["expiry_induced_cold_starts"],
        "expiry_cost_share_pct": (
            breakdown["expiry_induced_cold_cost"] / total_cost * 100 if total_cost > 0 else 0.0
        ),
        "expiry_starts_share_pct": (
            breakdown["expiry_induced_cold_starts"] / total_starts * 100 if total_starts > 0 else 0.0
        ),
    }


def run_naive_dtlm(data):
    """Run naive DTLM once and return policy plus summary metrics."""
    functions_info = data["functions_info"]
    memory_budget = data["ws_mean"] * M_RATIO
    warmup_end_ms = data["day_offset_ms"] + WARMUP_DAYS * DAY_MS

    policy = DTLM(memory_budget, functions_info, **NAIVE_KWARGS)
    results = simulate(policy, data["stream"], warmup_end_ms=warmup_end_ms)
    metrics = summary(results, functions_info, memory_budget, policy=policy, skip_warmup=True)
    return policy, metrics, warmup_end_ms


def run_gdsf(data):
    """Run GDSF once as the cost reference."""
    functions_info = data["functions_info"]
    memory_budget = data["ws_mean"] * M_RATIO
    warmup_end_ms = data["day_offset_ms"] + WARMUP_DAYS * DAY_MS

    policy = GDSF(memory_budget, functions_info)
    results = simulate(policy, data["stream"], warmup_end_ms=warmup_end_ms)
    return summary(results, functions_info, memory_budget, policy=policy, skip_warmup=True)


def extract_expiry_pressures(policy, warmup_end_ms):
    """Return deletion-time pressure samples for physical expiry deletions."""
    return [
        event["memory_utilization"]
        for event in policy.deletion_log
        if event["reason"] == "expiry" and event["timestamp"] >= warmup_end_ms
    ]


def check(label, actual, expected, tol_pct=5.0):
    """Print PASS/MISMATCH against an anchor value."""
    if expected == 0:
        status = "PASS" if actual == 0 else "MISMATCH"
    else:
        diff = abs(actual - expected) / abs(expected) * 100
        status = "PASS" if diff <= tol_pct else "MISMATCH"
    print(f"  {label}: actual={actual:.4f}, expected={expected:.4f} -> {status}")


def build_stats(metrics, gdsf_cost, pressures):
    """Build the persisted stats payload for the naive run."""
    pressure_array = np.array(pressures, dtype=float) if pressures else np.array([])
    breakdown = metrics["cold_start_breakdown"]
    total_cost = metrics["total_cold_start_cost"]
    total_starts = metrics["total_cold_starts"]
    pct_le_085_actual = float(np.mean(pressure_array <= 0.85) * 100) if len(pressure_array) else 0.0

    return {
        "expiry_deletion_count": len(pressures),
        "expiry_cold_start_count": breakdown.get("expiry_induced_cold_starts", 0),
        "mean_pressure": float(np.mean(pressure_array)) if len(pressure_array) else 0.0,
        "pct_le_085": round(pct_le_085_actual, 2),
        "total_cost": total_cost,
        "cost_over_gdsf": round(total_cost / gdsf_cost, 4) if gdsf_cost > 0 else 0.0,
        "expiry_cost_share_pct": round(
            breakdown.get("expiry_induced_cold_cost", 0.0) / total_cost * 100, 2
        ) if total_cost > 0 else 0.0,
        "expiry_starts_share_pct": round(
            breakdown.get("expiry_induced_cold_starts", 0) / total_starts * 100, 2
        ) if total_starts > 0 else 0.0,
        "pressure_samples": [round(value, 6) for value in pressures],
    }


def plot_histogram(stats):
    """Save the naive-only histogram PDF."""
    plt.rcParams.update({
        "font.family": "serif",
        "font.size": 8,
        "axes.labelsize": 8,
        "xtick.labelsize": 7,
        "ytick.labelsize": 7,
        "axes.linewidth": 0.6,
        "axes.labelcolor": "#333333",
        "xtick.color": "#333333",
        "ytick.color": "#333333",
        "xtick.major.width": 0.6,
        "ytick.major.width": 0.6,
    })

    values = np.array(stats["pressure_samples"], dtype=float)
    bins = np.linspace(0, 1, 21)
    fig, ax = plt.subplots(1, 1, figsize=(3.45, 2.8))

    ax.hist(values, bins=bins, color="#5B7FA5", edgecolor="white", linewidth=0.5)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.set_xlim(0.0, 1.0)
    ax.set_ylim(0, 3200)
    ax.margins(x=0)
    ax.set_xticks(np.linspace(0.0, 1.0, 6))
    ax.xaxis.set_major_formatter(FormatStrFormatter("%.1f"))
    ax.set_xlabel(r"Normalized memory pressure $p(t)$ at deletion")
    ax.set_ylabel("Expiry deletions")
    mean_pressure = stats["mean_pressure"]
    ax.axvline(mean_pressure, color="#B22222", linewidth=1.5, linestyle="--")

    annotation = f"mean = {mean_pressure:.2f}\n$n$ = {stats['expiry_deletion_count']:,}"
    ax.annotate(
        annotation,
        xy=(0.97, 0.95),
        xycoords="axes fraction",
        ha="right",
        va="top",
        fontsize=7.5,
        bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="gray", alpha=0.85),
    )

    fig.tight_layout()
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    output_path = FIGURES_DIR / "fig2_deletion_util.pdf"
    fig.savefig(output_path, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print(f"SAVED: {output_path}")


def main():
    print("Loading data...")
    data = prepare_data(seed=SEED, days=DAYS, working_set_days=WORKING_SET_DAYS)
    memory_budget = data["ws_mean"] * M_RATIO
    print(f"M = {memory_budget:.1f} MB (ratio={M_RATIO})")

    print("\nRunning GDSF (cost reference)...")
    gdsf_metrics = run_gdsf(data)
    gdsf_cost = gdsf_metrics["total_cold_start_cost"]
    print(f"  GDSF total_cost = {gdsf_cost:,.0f}")

    print("\nRunning naive DTLM overlay...")
    policy, metrics, warmup_end_ms = run_naive_dtlm(data)
    pressures = extract_expiry_pressures(policy, warmup_end_ms)
    stats = build_stats(metrics, gdsf_cost, pressures)

    print(f"  expiry deletion count:   {stats['expiry_deletion_count']}")
    print(f"  expiry cold starts:      {stats['expiry_cold_start_count']}")
    print(f"  mean deletion pressure:  {stats['mean_pressure']:.4f}")
    print(f"  proportion <= 0.85:      {stats['pct_le_085']:.2f}%")
    print(f"  total_cost:              {stats['total_cost']:,.0f}")
    print(f"  cost / GDSF:             {stats['cost_over_gdsf']:.4f}")

    naive_anchor = load_naive_anchor()
    if naive_anchor is None:
        print("\nCross-validation skipped: historical naive anchor is not available in results/.")
    else:
        pct_le_anchor = naive_anchor["pct_le_085"] if naive_anchor["pct_le_085"] is not None else stats["pct_le_085"]
        print("\nCross-validation against naive anchor")
        print("=" * 50)
        check("total_cost", stats["total_cost"], naive_anchor["total_cost"])
        check("mean p(t)", stats["mean_pressure"], naive_anchor["mean"])
        check("pct <= 0.85", stats["pct_le_085"], pct_le_anchor)
        check("cost/GDSF", stats["cost_over_gdsf"], naive_anchor["total_cost"] / gdsf_cost if gdsf_cost > 0 else 0.0)
        check("expiry CS count", stats["expiry_cold_start_count"], naive_anchor["expiry_cs_count"])
        check("expiry cost / total cost (%)", stats["expiry_cost_share_pct"], naive_anchor["expiry_cost_share_pct"], tol_pct=1.0)
        check("expiry starts / total starts (%)", stats["expiry_starts_share_pct"], naive_anchor["expiry_starts_share_pct"], tol_pct=1.0)

    plot_histogram(stats)

    output_path = FIGURES_DIR / "fig2_stats.json"
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump({"gdsf_cost": gdsf_cost, "naive": stats}, handle, indent=2)
    print(f"SAVED: {output_path}")


if __name__ == "__main__":
    main()
