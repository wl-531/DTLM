"""Generate Fig. 3 cost-vs-M using DTLM as the complete EI-enabled method."""

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent
BASELINE_DIR = ROOT / "results" / "baseline"
EI_ABLATION_DIR = ROOT / "results" / "ei_ablation"
FIGURES_DIR = ROOT / "figures"

M_RATIOS = [0.1, 0.2, 0.3, 0.5, 0.7, 1.0]
POLICIES = [
    "adaptive_ttl_lru",
    "c2rd_sr",
    "dtlm",
    "fixed_ttl_lru",
    "gdsf",
    "iat_adaptive_ttl",
    "lfu",
    "lru",
    "ttlmin_extnd",
]

DISPLAY_NAMES = {
    "adaptive_ttl_lru": "Adaptive-TTL+LRU",
    "c2rd_sr": "C2RD-SR",
    "dtlm": "DTLM",
    "fixed_ttl_lru": "Fixed-TTL+LRU",
    "gdsf": "GDSF",
    "iat_adaptive_ttl": "IAT-Adaptive TTL (Adm.)",
    "lfu": "LFU",
    "lru": "LRU",
    "ttlmin_extnd": "TTLmin,extnd",
}

LINE_STYLES = {
    "dtlm": dict(color="#111111", linewidth=2.6, marker="o", markersize=4.5, zorder=4),
    "gdsf": dict(color="#d95f02", linewidth=2.0, marker="s", markersize=3.8, zorder=3),
    "lru": dict(color="#1b9e77", linewidth=1.8, marker="^", markersize=3.5, zorder=2),
    "lfu": dict(color="#7570b3", linewidth=1.8, marker="D", markersize=3.2, zorder=2),
    "fixed_ttl_lru": dict(color="#e7298a", linewidth=1.5, marker="v", markersize=3.2, zorder=1),
    "iat_adaptive_ttl": dict(color="#66a61e", linewidth=1.5, marker="P", markersize=3.0, zorder=1),
    "adaptive_ttl_lru": dict(color="#e6ab02", linewidth=1.5, marker="X", markersize=3.0, zorder=1),
    "c2rd_sr": dict(color="#a6761d", linewidth=1.5, marker="<", markersize=3.0, zorder=1),
    "ttlmin_extnd": dict(color="#666666", linewidth=1.5, marker=">", markersize=3.0, zorder=1),
}


def read_json(path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def result_path(policy_name, m_ratio):
    if policy_name == "dtlm":
        return EI_ABLATION_DIR / f"ei_dtlm_{m_ratio:.1f}.json"
    return BASELINE_DIR / f"{policy_name}_{m_ratio:.1f}.json"


def load_series():
    """Load total cold-start cost for every policy and M ratio."""
    series = {}
    for policy_name in POLICIES:
        rows = []
        for m_ratio in M_RATIOS:
            path = result_path(policy_name, m_ratio)
            payload = read_json(path)
            source = "ei_ablation/ei_dtlm" if policy_name == "dtlm" else "baseline"
            rows.append(
                {
                    "M_ratio": m_ratio,
                    "total_cost": float(payload["metrics"]["total_cold_start_cost"]),
                    "cold_start_rate": float(payload["metrics"]["cold_start_rate"]),
                    "avg_mem_util": float(payload["metrics"]["avg_memory_utilization"]),
                    "path": str(path),
                    "source": source,
                }
            )
        series[policy_name] = rows
    return series


def build_stats(series):
    """Persist the exact values used by the plot for easy paper cross-checks."""
    stats = {"m_ratios": M_RATIOS, "policies": {}, "dtlm_vs_gdsf": {}, "rankings": {}}

    for policy_name, rows in series.items():
        stats["policies"][policy_name] = {
            "display_name": DISPLAY_NAMES[policy_name],
            "points": rows,
        }

    for m_ratio in M_RATIOS:
        by_cost = []
        for policy_name in POLICIES:
            row = next(item for item in series[policy_name] if item["M_ratio"] == m_ratio)
            by_cost.append((policy_name, row["total_cost"]))
        by_cost.sort(key=lambda item: item[1])
        stats["rankings"][f"{m_ratio:.1f}"] = [
            {"policy": policy_name, "display_name": DISPLAY_NAMES[policy_name], "total_cost": total_cost}
            for policy_name, total_cost in by_cost
        ]

        dtlm_cost = next(item["total_cost"] for item in series["dtlm"] if item["M_ratio"] == m_ratio)
        gdsf_cost = next(item["total_cost"] for item in series["gdsf"] if item["M_ratio"] == m_ratio)
        stats["dtlm_vs_gdsf"][f"{m_ratio:.1f}"] = {
            "dtlm_cost": dtlm_cost,
            "gdsf_cost": gdsf_cost,
            "ratio": dtlm_cost / gdsf_cost if gdsf_cost else None,
        }

    return stats


def plot(series):
    plt.rcParams.update(
        {
            "font.family": "serif",
            "font.size": 8,
            "axes.labelsize": 9,
            "axes.titlesize": 9,
            "xtick.labelsize": 8,
            "ytick.labelsize": 8,
            "legend.fontsize": 7,
        }
    )

    fig, ax = plt.subplots(1, 1, figsize=(7.1, 3.2))
    for policy_name in POLICIES:
        rows = series[policy_name]
        x = [row["M_ratio"] for row in rows]
        y = [row["total_cost"] for row in rows]
        style = dict(LINE_STYLES[policy_name])
        ax.plot(x, y, label=DISPLAY_NAMES[policy_name], **style)

    ax.set_yscale("log")
    ax.set_xlabel(r"Memory budget ratio $M$ / working set")
    ax.set_ylabel("Total cold-start cost")
    ax.set_xticks(M_RATIOS)
    ax.set_xlim(0.08, 1.02)
    ax.grid(axis="y", which="major", linestyle=":", linewidth=0.6, alpha=0.6)
    ax.legend(loc="upper center", ncol=3, frameon=False, bbox_to_anchor=(0.5, 1.22))

    fig.tight_layout()
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    pdf_path = FIGURES_DIR / "fig3_cost_vs_m.pdf"
    png_path = FIGURES_DIR / "fig3_cost_vs_m.png"
    fig.savefig(pdf_path, bbox_inches="tight")
    fig.savefig(png_path, dpi=300, bbox_inches="tight")
    plt.close(fig)
    return pdf_path, png_path


def main():
    series = load_series()
    pdf_path, png_path = plot(series)
    stats = build_stats(series)
    stats_path = FIGURES_DIR / "fig3_stats.json"
    with stats_path.open("w", encoding="utf-8") as handle:
        json.dump(stats, handle, indent=2)

    print(f"SAVED: {pdf_path}")
    print(f"SAVED: {png_path}")
    print(f"SAVED: {stats_path}")


if __name__ == "__main__":
    main()
