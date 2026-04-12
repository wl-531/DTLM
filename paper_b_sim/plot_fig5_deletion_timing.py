"""Generate Fig. 5: expiry-deletion pressure distributions across M ratios."""

import json
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt

FIGURES_DIR = Path(__file__).resolve().parent / "figures"
RESULTS_DIR = Path(__file__).resolve().parent / "results" / "deletion_timing"
M_RATIOS = [0.3, 0.5, 0.7, 1.0]
P_DEACTIVATE = 0.95


def load_payload(m_ratio):
    path = RESULTS_DIR / f"dtlm_{m_ratio:.1f}.json"
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def build_stats():
    stats = []
    for m_ratio in M_RATIOS:
        payload = load_payload(m_ratio)
        expiry = payload["expiry_physical_deletion"]
        samples = np.array(expiry["pressure_samples"], dtype=float)
        stats.append(
            {
                "M_ratio": m_ratio,
                "count": int(expiry["count"]),
                "mean_pressure": float(expiry["mean_pressure"]),
                "median_pressure": float(np.median(samples)) if len(samples) else 0.0,
                "p95_pressure": float(np.quantile(samples, 0.95)) if len(samples) else 0.0,
                "pct_ge_gate": float(np.mean(samples >= P_DEACTIVATE) * 100) if len(samples) else 0.0,
                "pressure_samples": [float(v) for v in samples],
            }
        )
    return stats


def plot(stats):
    plt.rcParams.update(
        {
            "font.family": "serif",
            "font.size": 8,
            "axes.labelsize": 9,
            "axes.titlesize": 9,
            "xtick.labelsize": 7,
            "ytick.labelsize": 7,
        }
    )

    bins = np.linspace(0.75, 1.0, 16)
    fig, axes = plt.subplots(2, 2, figsize=(6.9, 4.9), sharex=True, sharey=True)
    axes = axes.flatten()

    for ax, entry in zip(axes, stats):
        values = np.array(entry["pressure_samples"], dtype=float)
        ax.hist(values, bins=bins, color="#d95f02", edgecolor="white", linewidth=0.4)
        ax.axvline(P_DEACTIVATE, color="gray", linewidth=0.8, linestyle=":", alpha=0.85)
        ax.axvline(entry["mean_pressure"], color="#c44e52", linewidth=1.2, linestyle="--")
        ax.set_title(rf"$M={entry['M_ratio']:.1f}$")
        ax.set_xlim(0.75, 1.0)

        note = (
            f"mean = {entry['mean_pressure']:.2f}\n"
            f"count = {entry['count']}\n"
            f"{entry['pct_ge_gate']:.1f}% " + r"$\geq$ 0.95"
        )
        ax.annotate(
            note,
            xy=(0.97, 0.95),
            xycoords="axes fraction",
            ha="right",
            va="top",
            fontsize=7,
            bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="gray", alpha=0.85),
        )

    axes[0].set_ylabel("Number of expiry deletions")
    axes[2].set_ylabel("Number of expiry deletions")
    axes[2].set_xlabel(r"Normalized memory pressure $p(t)$ at deletion")
    axes[3].set_xlabel(r"Normalized memory pressure $p(t)$ at deletion")

    fig.tight_layout()
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    pdf_path = FIGURES_DIR / "fig5_deletion_timing.pdf"
    png_path = FIGURES_DIR / "fig5_deletion_timing.png"
    fig.savefig(pdf_path, dpi=300, bbox_inches="tight")
    fig.savefig(png_path, dpi=300, bbox_inches="tight")
    plt.close(fig)
    return pdf_path, png_path


def main():
    stats = build_stats()
    pdf_path, png_path = plot(stats)

    stats_path = FIGURES_DIR / "fig5_stats.json"
    with stats_path.open("w", encoding="utf-8") as handle:
        json.dump({"p_deactivate": P_DEACTIVATE, "panels": stats}, handle, indent=2)

    print(f"SAVED: {pdf_path}")
    print(f"SAVED: {png_path}")
    print(f"SAVED: {stats_path}")
    for entry in stats:
        print(
            f"M={entry['M_ratio']:.1f}: count={entry['count']}, "
            f"mean={entry['mean_pressure']:.6f}, pct_ge_095={entry['pct_ge_gate']:.2f}%"
        )


if __name__ == "__main__":
    main()
