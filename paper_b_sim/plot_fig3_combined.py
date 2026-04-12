"""Generate the combined Fig. 3 figure* with overall and sensitivity panels."""

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

ROOT = Path(__file__).resolve().parent
FIGURES_DIR = ROOT / "figures"
OVERALL_BASELINE_DIR = ROOT / "results" / "baseline"
OVERALL_DTLM_DIRS = [
    ROOT / "results" / "ei_ablation",
    ROOT / "results" / "ei_baseline",
]
SENSITIVITY_BASELINE_DIR = ROOT / "results" / "sensitivity"
SENSITIVITY_DTLM_DIR = ROOT / "results" / "ei_sensitivity"

OVERALL_M_RATIOS = [0.1, 0.2, 0.3, 0.5, 0.7, 1.0]
SENSITIVITY_M_RATIOS = [0.3, 0.5, 0.7]
SENSITIVITY_SCALES = [0.5, 1.0, 2.0]

ALL_POLICIES = [
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
MAIN_POLICIES = [
    "dtlm",
    "gdsf",
    "lru",
    "lfu",
    "c2rd_sr",
    "fixed_ttl_lru",
    "adaptive_ttl_lru",
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
    "dtlm": dict(color="#000000", linestyle="-", linewidth=2.5, marker="o", markersize=7.0, zorder=10),
    "gdsf": dict(color="#D62728", linestyle="-", linewidth=1.2, marker="s", markersize=5.0, zorder=6),
    "lru": dict(color="#1F77B4", linestyle="-", linewidth=1.2, marker="^", markersize=5.0, zorder=6),
    "lfu": dict(color="#9467BD", linestyle="-", linewidth=1.2, marker="D", markersize=5.0, zorder=6),
    "c2rd_sr": dict(color="#8C564B", linestyle="-", linewidth=1.2, marker="<", markersize=5.0, zorder=6),
    "fixed_ttl_lru": dict(color="#E377C2", linestyle="-", linewidth=1.2, marker="v", markersize=5.0, zorder=6),
    "adaptive_ttl_lru": dict(color="#FF7F0E", linestyle="-", linewidth=1.2, marker="p", markersize=5.0, zorder=6),
    "iat_adaptive_ttl": dict(color="#2CA02C", linestyle="--", linewidth=1.2, marker="*", markersize=6.0, zorder=5),
    "ttlmin_extnd": dict(color="#7F7F7F", linestyle="--", linewidth=1.2, marker=">", markersize=5.0, zorder=5),
}

PANEL_LABEL_POS = (0.125, 0.20)


def read_json(path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def find_existing_path(candidates):
    for path in candidates:
        if path.exists():
            return path
    return None


def overall_dtlm_path(m_ratio):
    candidates = [directory / f"ei_dtlm_{m_ratio:.1f}.json" for directory in OVERALL_DTLM_DIRS]
    path = find_existing_path(candidates)
    if path is None:
        joined = "\n".join(str(item) for item in candidates)
        raise FileNotFoundError(f"Missing overall DTLM file for M={m_ratio:.1f}:\n{joined}")
    return path


def baseline_overall_path(policy_name, m_ratio):
    path = OVERALL_BASELINE_DIR / f"{policy_name}_{m_ratio:.1f}.json"
    if not path.exists():
        raise FileNotFoundError(f"Missing overall baseline file: {path}")
    return path


def sensitivity_dtlm_path(m_ratio, scale):
    candidates = [
        SENSITIVITY_DTLM_DIR / f"ei_dtlm_{m_ratio:.1f}_{scale:.1f}.json",
        SENSITIVITY_DTLM_DIR / f"ei_dtlm_{m_ratio:.1f}_{scale:.1f}x.json",
    ]
    path = find_existing_path(candidates)
    if path is None:
        joined = "\n".join(str(item) for item in candidates)
        raise FileNotFoundError(f"Missing DTLM sensitivity file for M={m_ratio:.1f}, scale={scale:.1f}:\n{joined}")
    return path


def baseline_sensitivity_path(policy_name, m_ratio, scale):
    candidates = [
        SENSITIVITY_BASELINE_DIR / f"{policy_name}_{m_ratio:.1f}_{scale:.1f}.json",
        SENSITIVITY_BASELINE_DIR / f"{policy_name}_{m_ratio:.1f}_{scale:.1f}x.json",
    ]
    path = find_existing_path(candidates)
    if path is None:
        joined = "\n".join(str(item) for item in candidates)
        raise FileNotFoundError(
            f"Missing baseline sensitivity file for {policy_name}, M={m_ratio:.1f}, scale={scale:.1f}:\n{joined}"
        )
    return path


def load_point(path):
    payload = read_json(path)
    metrics = payload["metrics"]
    return {
        "total_cost": float(metrics["total_cold_start_cost"]),
        "cold_start_rate": float(metrics["cold_start_rate"]),
        "avg_memory_utilization": float(metrics["avg_memory_utilization"]),
        "path": str(path),
    }


def load_overall_series():
    data = {}
    loaded = {"dtlm": [], "baselines": []}
    for policy_name in ALL_POLICIES:
        rows = []
        for m_ratio in OVERALL_M_RATIOS:
            if policy_name == "dtlm":
                path = overall_dtlm_path(m_ratio)
                loaded["dtlm"].append(str(path))
            else:
                path = baseline_overall_path(policy_name, m_ratio)
                loaded["baselines"].append(str(path))
            point = load_point(path)
            point["M_ratio"] = m_ratio
            rows.append(point)
        data[policy_name] = rows
    return data, loaded


def load_sensitivity_series():
    data = {}
    loaded = {"dtlm": [], "baselines": []}
    for scale in SENSITIVITY_SCALES:
        data[scale] = {}
        for policy_name in ALL_POLICIES:
            rows = []
            for m_ratio in SENSITIVITY_M_RATIOS:
                if policy_name == "dtlm":
                    path = sensitivity_dtlm_path(m_ratio, scale)
                    loaded["dtlm"].append(str(path))
                else:
                    path = baseline_sensitivity_path(policy_name, m_ratio, scale)
                    loaded["baselines"].append(str(path))
                point = load_point(path)
                point["M_ratio"] = m_ratio
                rows.append(point)
            data[scale][policy_name] = rows
    return data, loaded


def build_legend_handles():
    handles = []
    for policy_name in ALL_POLICIES:
        style = LINE_STYLES[policy_name]
        handles.append(
            Line2D(
                [0],
                [0],
                color=style["color"],
                linestyle=style["linestyle"],
                linewidth=style["linewidth"],
                marker=style["marker"],
                markersize=style["markersize"],
                label=DISPLAY_NAMES[policy_name],
            )
        )
    return handles


def scaled_style(policy_name, marker_scale=1.0):
    style = dict(LINE_STYLES[policy_name])
    style["markersize"] = style["markersize"] * marker_scale
    return style


def apply_axis_style(ax):
    ax.set_facecolor("white")
    ax.grid(axis="y", which="major", linestyle="--", linewidth=0.6, color="#d0d0d0", alpha=0.9)
    ax.grid(axis="y", which="minor", linestyle="--", linewidth=0.4, color="#e6e6e6", alpha=0.7)


def annotate_panel(ax, label, position=PANEL_LABEL_POS):
    ax.text(
        position[0],
        position[1],
        label,
        transform=ax.transAxes,
        ha="left",
        va="top",
        fontsize=12,
        fontweight="bold",
        bbox=dict(facecolor="white", edgecolor="none", alpha=0.82, pad=0.18),
    )


def plot_panel(ax, series_map, policies, x_values):
    for policy_name in policies:
        rows = series_map[policy_name]
        x = [row["M_ratio"] for row in rows]
        y = [row["total_cost"] for row in rows]
        ax.plot(x, y, **scaled_style(policy_name), label=DISPLAY_NAMES[policy_name])
    ax.set_yscale("log")
    ax.set_xticks(x_values)
    apply_axis_style(ax)


def plot_inset(ax, overall_data, y_limits):
    inset = ax.inset_axes([0.695, 0.685, 0.275, 0.245])
    for policy_name in ALL_POLICIES:
        rows = overall_data[policy_name]
        x = [row["M_ratio"] for row in rows]
        y = [row["total_cost"] for row in rows]
        inset.plot(x, y, **scaled_style(policy_name, marker_scale=0.72))
    inset.set_yscale("log")
    inset.set_xlim(0.08, 1.02)
    inset.set_ylim(*y_limits)
    inset.set_xticks([0.1, 0.5, 1.0])
    inset.tick_params(axis="both", labelsize=6, width=0.5, length=2.5)
    inset.grid(axis="y", which="major", linestyle="--", linewidth=0.45, color="#d8d8d8", alpha=0.9)
    inset.set_title("All 9 policies", fontsize=7.0, pad=2)
    for spine in inset.spines.values():
        spine.set_linewidth(0.5)
    return inset


def collect_costs(overall_data, sensitivity_data):
    values = []
    for rows in overall_data.values():
        values.extend(row["total_cost"] for row in rows)
    for scale in SENSITIVITY_SCALES:
        for rows in sensitivity_data[scale].values():
            values.extend(row["total_cost"] for row in rows)
    return values


def compute_y_limits(overall_data, sensitivity_data):
    values = collect_costs(overall_data, sensitivity_data)
    min_value = min(values)
    max_value = max(values)
    return min_value * 0.88, max_value * 1.12


def build_stats(overall_data, sensitivity_data, overall_loaded, sensitivity_loaded):
    return {
        "manual_reference_used": False,
        "panel_a_main_policies": MAIN_POLICIES,
        "panel_a_inset_policies": ALL_POLICIES,
        "panel_a_inset_contains_all_9_policies": len(ALL_POLICIES) == 9,
        "overall": overall_data,
        "sensitivity": {f"{scale:.1f}": sensitivity_data[scale] for scale in SENSITIVITY_SCALES},
        "loaded_files": {
            "panel_a_overall_dtlm": overall_loaded["dtlm"],
            "panel_a_overall_baselines": overall_loaded["baselines"],
            "panel_bcd_sensitivity_dtlm": sensitivity_loaded["dtlm"],
            "panel_bcd_sensitivity_baselines": sensitivity_loaded["baselines"],
        },
        "hand_reference_notes": "Not used for plotting. Only result JSON files were used.",
    }


def plot_figure(overall_data, sensitivity_data):
    plt.rcParams.update(
        {
            "font.family": "serif",
            "font.size": 9,
            "axes.labelsize": 10,
            "xtick.labelsize": 9,
            "ytick.labelsize": 9,
            "legend.fontsize": 8,
        }
    )

    fig = plt.figure(figsize=(7.2, 5.5), constrained_layout=False)
    grid = fig.add_gridspec(3, 3, height_ratios=[0.18, 1.30, 1.0], hspace=0.54)
    lower_grid = grid[2, :].subgridspec(1, 3, wspace=0.05)

    legend_ax = fig.add_subplot(grid[0, :])
    legend_ax.axis("off")
    ax_a = fig.add_subplot(grid[1, :])
    ax_b = fig.add_subplot(lower_grid[0, 0])
    ax_c = fig.add_subplot(lower_grid[0, 1], sharey=ax_b)
    ax_d = fig.add_subplot(lower_grid[0, 2], sharey=ax_b)

    y_limits = compute_y_limits(overall_data, sensitivity_data)

    plot_panel(ax_a, overall_data, MAIN_POLICIES, OVERALL_M_RATIOS)
    ax_a.set_xlim(0.08, 1.02)
    ax_a.set_ylim(*y_limits)
    ax_a.set_xlabel(r"Memory budget ratio $M$ / working set")
    ax_a.xaxis.set_label_coords(0.5, -0.145)
    ax_a.set_ylabel("Total cold-start cost")
    annotate_panel(ax_a, "(a)")
    inset = plot_inset(ax_a, overall_data, y_limits)

    for ax, scale, label in zip([ax_b, ax_c, ax_d], SENSITIVITY_SCALES, ["(b)", "(c)", "(d)"]):
        plot_panel(ax, sensitivity_data[scale], ALL_POLICIES, SENSITIVITY_M_RATIOS)
        ax.set_xlim(0.28, 0.72)
        ax.set_ylim(*y_limits)
        ax.set_title(rf"$\times {scale:.1f}$", fontsize=11, pad=2)
        annotate_panel(ax, label)
    ax_b.set_ylabel("Total cold-start cost")
    plt.setp(ax_c.get_yticklabels(), visible=False)
    plt.setp(ax_d.get_yticklabels(), visible=False)
    fig.supxlabel(r"Memory budget ratio $M$ / working set", y=0.035, fontsize=10)

    handles = build_legend_handles()
    top_legend = legend_ax.legend(
        handles=handles[:5],
        loc="center",
        bbox_to_anchor=(0.5, 0.18),
        ncol=5,
        frameon=False,
        handlelength=1.65,
        columnspacing=0.78,
        handletextpad=0.33,
        borderaxespad=0.0,
        fontsize=7.2,
    )
    legend_ax.add_artist(top_legend)
    legend_ax.legend(
        handles=handles[5:],
        loc="center",
        bbox_to_anchor=(0.5, -0.36),
        ncol=4,
        frameon=False,
        handlelength=1.65,
        columnspacing=0.88,
        handletextpad=0.33,
        borderaxespad=0.0,
        fontsize=7.2,
    )
    fig.subplots_adjust(left=0.09, right=0.995, top=0.985, bottom=0.12)

    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    pdf_path = FIGURES_DIR / "fig3_combined.pdf"
    png_path = FIGURES_DIR / "fig3_combined.png"
    fig.savefig(pdf_path, dpi=300, bbox_inches="tight")
    fig.savefig(png_path, dpi=300, bbox_inches="tight")
    plt.close(fig)
    return pdf_path, png_path, inset


def main():
    overall_data, overall_loaded = load_overall_series()
    sensitivity_data, sensitivity_loaded = load_sensitivity_series()
    pdf_path, png_path, _ = plot_figure(overall_data, sensitivity_data)

    stats = build_stats(overall_data, sensitivity_data, overall_loaded, sensitivity_loaded)
    stats_path = FIGURES_DIR / "fig3_combined_stats.json"
    with stats_path.open("w", encoding="utf-8") as handle:
        json.dump(stats, handle, indent=2)

    print(f"SAVED: {pdf_path}")
    print(f"SAVED: {png_path}")
    print(f"SAVED: {stats_path}")
    print(f"INSET_ALL_9_POLICIES: {stats['panel_a_inset_contains_all_9_policies']}")


if __name__ == "__main__":
    main()
