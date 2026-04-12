"""Generate Fig. 4 sensitivity panels for the EI-enabled DTLM method."""

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent
SENSITIVITY_DIR = ROOT / "results" / "sensitivity"
EI_SENSITIVITY_DIR = ROOT / "results" / "ei_sensitivity"
FIGURES_DIR = ROOT / "figures"

M_RATIOS = [0.3, 0.5, 0.7]
SCALES = [0.5, 1.0, 2.0]
POLICIES = [
    "dtlm",
    "gdsf",
    "lru",
    "lfu",
    "c2rd_sr",
    "fixed_ttl_lru",
    "adaptive_ttl_lru",
    "iat_adaptive_ttl",
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
    "dtlm": dict(color="black", linestyle="-", linewidth=2.5, marker="o", markersize=6.0, zorder=10),
    "gdsf": dict(color="#D62728", linestyle="-", linewidth=1.2, marker="s", markersize=4.5, zorder=4),
    "lru": dict(color="#1F77B4", linestyle="-", linewidth=1.2, marker="^", markersize=4.5, zorder=4),
    "lfu": dict(color="#9467BD", linestyle="-", linewidth=1.2, marker="D", markersize=4.5, zorder=4),
    "c2rd_sr": dict(color="#8C564B", linestyle="-", linewidth=1.2, marker="<", markersize=4.5, zorder=4),
    "fixed_ttl_lru": dict(color="#E377C2", linestyle="-", linewidth=1.2, marker="v", markersize=4.5, zorder=4),
    "adaptive_ttl_lru": dict(color="#FF7F0E", linestyle="-", linewidth=1.2, marker="P", markersize=4.5, zorder=4),
    "iat_adaptive_ttl": dict(color="#2CA02C", linestyle="--", linewidth=1.2, marker="X", markersize=4.0, zorder=3),
    "ttlmin_extnd": dict(color="#7F7F7F", linestyle="--", linewidth=1.2, marker=">", markersize=4.0, zorder=3),
}


def read_json(path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def result_path(policy_name, m_ratio, scale):
    if policy_name == "dtlm":
        return EI_SENSITIVITY_DIR / f"ei_dtlm_{m_ratio:.1f}_{scale:.1f}.json"
    return SENSITIVITY_DIR / f"{policy_name}_{m_ratio:.1f}_{scale:.1f}.json"


def load_data():
    """Load all sensitivity points and verify required files exist."""
    data = {}
    missing = []
    for scale in SCALES:
        data[scale] = {}
        for policy_name in POLICIES:
            rows = []
            for m_ratio in M_RATIOS:
                path = result_path(policy_name, m_ratio, scale)
                if not path.exists():
                    missing.append(str(path))
                    continue
                payload = read_json(path)
                rows.append(
                    {
                        "M_ratio": m_ratio,
                        "total_cost": float(payload["metrics"]["total_cold_start_cost"]),
                        "cold_start_rate": float(payload["metrics"]["cold_start_rate"]),
                        "avg_mem_util": float(payload["metrics"]["avg_memory_utilization"]),
                        "path": str(path),
                    }
                )
            data[scale][policy_name] = rows
    if missing:
        first = "\n".join(missing[:5])
        raise FileNotFoundError(f"Missing sensitivity files:\n{first}")
    return data


def build_stats(data):
    stats = {"scales": SCALES, "m_ratios": M_RATIOS, "panels": {}, "dtlm_ranks": {}}
    for scale in SCALES:
        stats["panels"][f"{scale:.1f}"] = {}
        ranking = {}
        for policy_name, rows in data[scale].items():
            stats["panels"][f"{scale:.1f}"][policy_name] = {
                "display_name": DISPLAY_NAMES[policy_name],
                "points": rows,
            }
        for m_ratio in M_RATIOS:
            rows = []
            for policy_name in POLICIES:
                point = next(item for item in data[scale][policy_name] if item["M_ratio"] == m_ratio)
                rows.append((policy_name, point["total_cost"]))
            rows.sort(key=lambda item: item[1])
            ranking[f"{m_ratio:.1f}"] = [
                {"policy": name, "display_name": DISPLAY_NAMES[name], "total_cost": cost}
                for name, cost in rows
            ]
        stats["dtlm_ranks"][f"{scale:.1f}"] = {
            key: next(i + 1 for i, row in enumerate(value) if row["policy"] == "dtlm")
            for key, value in ranking.items()
        }
    return stats


def plot(data):
    plt.rcParams.update(
        {
            "font.family": "serif",
            "font.size": 10,
            "axes.labelsize": 11,
            "axes.titlesize": 11,
            "xtick.labelsize": 10,
            "ytick.labelsize": 10,
            "legend.fontsize": 7.5,
        }
    )

    fig, axes = plt.subplots(1, 3, figsize=(7.0, 3.8), sharey=True)
    fig.subplots_adjust(left=0.10, right=0.98, bottom=0.20, top=0.80, wspace=0.08)

    legend_handles = []
    legend_labels = []
    for ax, scale in zip(axes, SCALES):
        for policy_name in POLICIES:
            rows = data[scale][policy_name]
            x = [row["M_ratio"] for row in rows]
            y = [row["total_cost"] for row in rows]
            style = dict(LINE_STYLES[policy_name])
            (line,) = ax.plot(x, y, label=DISPLAY_NAMES[policy_name], **style)
            if scale == SCALES[0]:
                legend_handles.append(line)
                legend_labels.append(DISPLAY_NAMES[policy_name])
        ax.set_title(rf"$\times {scale:.1f}$", fontsize=11, fontweight="bold", pad=6)
        ax.set_xticks(M_RATIOS)
        ax.set_xlim(0.28, 0.72)
        ax.set_yscale("log")
        ax.grid(axis="y", which="major", linestyle=":", linewidth=0.6, alpha=0.6)

    axes[0].set_ylabel("Total cold-start cost")
    axes[1].set_xlabel(r"Memory budget ratio $M$ / working set")

    fig.legend(
        legend_handles,
        legend_labels,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.985),
        ncol=5,
        frameon=False,
        fontsize=7.5,
        handlelength=2.0,
        handletextpad=0.4,
        columnspacing=1.0,
        labelspacing=0.3,
        borderpad=0.3,
    )

    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    pdf_path = FIGURES_DIR / "fig4_sensitivity.pdf"
    png_path = FIGURES_DIR / "fig4_sensitivity.png"
    fig.savefig(pdf_path, dpi=300, bbox_inches="tight")
    fig.savefig(png_path, dpi=300, bbox_inches="tight")
    plt.close(fig)
    return pdf_path, png_path


def main():
    data = load_data()
    pdf_path, png_path = plot(data)
    stats = build_stats(data)
    stats_path = FIGURES_DIR / "fig4_sensitivity_stats.json"
    with stats_path.open("w", encoding="utf-8") as handle:
        json.dump(stats, handle, indent=2)
    print(f"SAVED: {pdf_path}")
    print(f"SAVED: {png_path}")
    print(f"SAVED: {stats_path}")


if __name__ == "__main__":
    main()
