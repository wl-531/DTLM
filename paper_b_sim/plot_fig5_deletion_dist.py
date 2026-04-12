"""Generate single-panel Fig. 5 using the same style as Fig. 2."""

import json
from pathlib import Path

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FormatStrFormatter

FIGURES_DIR = Path(__file__).resolve().parent / "figures"
RESULTS_PATH = Path(__file__).resolve().parent / "results" / "deletion_timing" / "dtlm_1.0.json"
THRESHOLD = 0.85


def load_stats():
    with RESULTS_PATH.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    samples = np.array(payload["expiry_physical_deletion"]["pressure_samples"], dtype=float)
    return {
        "mean_pressure": float(np.mean(samples)) if len(samples) else 0.0,
        "pct_le_085": float(np.mean(samples <= THRESHOLD) * 100) if len(samples) else 0.0,
        "pressure_samples": [float(v) for v in samples],
        "expiry_deletion_count": int(payload["expiry_physical_deletion"]["count"]),
        "source": str(RESULTS_PATH),
        "M_ratio": 1.0,
    }


def plot_histogram(stats):
    """Match Fig. 2 styling as closely as possible."""
    plt.rcParams.update(
        {
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
        }
    )

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
    pdf_path = FIGURES_DIR / "fig5_deletion_dist.pdf"
    png_path = FIGURES_DIR / "fig5_deletion_dist.png"
    fig.savefig(pdf_path, dpi=300, bbox_inches="tight")
    fig.savefig(png_path, dpi=300, bbox_inches="tight")
    plt.close(fig)
    return pdf_path, png_path


def main():
    stats = load_stats()
    pdf_path, png_path = plot_histogram(stats)

    stats_path = FIGURES_DIR / "fig5_deletion_dist_stats.json"
    with stats_path.open("w", encoding="utf-8") as handle:
        json.dump(stats, handle, indent=2)

    print(f"SAVED: {pdf_path}")
    print(f"SAVED: {png_path}")
    print(f"SAVED: {stats_path}")
    print(
        f"M=1.0: count={stats['expiry_deletion_count']}, "
        f"mean={stats['mean_pressure']:.6f}, pct_le_085={stats['pct_le_085']:.2f}%"
    )


if __name__ == "__main__":
    main()
