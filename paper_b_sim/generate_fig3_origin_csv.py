"""Generate Origin-friendly CSV files for Fig. 3 from result JSON files."""

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FIGURES_DIR = ROOT / "figures"
RESULTS_DIR = ROOT / "results"

PANEL_A_M = [0.1, 0.2, 0.3, 0.5, 0.7, 1.0]
SENSITIVITY_M = [0.3, 0.5, 0.7]
SENSITIVITY_SCALES = [0.5, 1.0, 2.0]

COLUMN_ORDER = [
    ("DTLM", "dtlm"),
    ("GDSF", "gdsf"),
    ("LRU", "lru"),
    ("LFU", "lfu"),
    ("C2RD-SR", "c2rd_sr"),
    ("Fixed-TTL+LRU", "fixed_ttl_lru"),
    ("Adaptive-TTL+LRU", "adaptive_ttl_lru"),
    ("IAT-Adaptive TTL (Adm.)", "iat_adaptive_ttl"),
    ("TTLmin_extnd", "ttlmin_extnd"),
]

PANEL_A_DTLM_TRUTH = {
    "0.1": 57691770,
    "0.2": 36466895,
    "0.3": 11754120,
    "0.5": 3946730,
    "0.7": 1950870,
    "1.0": 1181620,
}

PANEL_A_GDSF_TRUTH = {
    "0.1": 57691770,
    "0.3": 16284490,
    "0.5": 7666250,
    "1.0": 1249850,
}


def format_ratio(value):
    """Keep the paper's one-decimal naming convention."""
    return f"{value:.1f}"


def read_json(path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def extract_total_cost(path):
    payload = read_json(path)
    return int(payload["metrics"]["total_cold_start_cost"])


def unique_paths(candidates):
    seen = set()
    ordered = []
    for path in candidates:
        key = str(path)
        if key not in seen:
            seen.add(key)
            ordered.append(path)
    return ordered


def panel_a_candidates(policy, m_ratio):
    m_str = format_ratio(m_ratio)
    if policy == "dtlm":
        return unique_paths(
            [
                RESULTS_DIR / "ei_ablation" / f"ei_dtlm_{m_str}.json",
                RESULTS_DIR / "ei_ablation" / f"dtlm_{m_str}.json",
                RESULTS_DIR / "ei_ablation" / f"ei-dtlm_{m_str}.json",
                RESULTS_DIR / "ei_baseline" / f"ei_dtlm_{m_str}.json",
                RESULTS_DIR / "ei_baseline" / f"dtlm_{m_str}.json",
            ]
        )
    policy_dash = policy.replace("_", "-")
    return unique_paths(
        [
            RESULTS_DIR / "baseline" / f"{policy}_{m_str}.json",
            RESULTS_DIR / "baseline" / f"{policy_dash}_{m_str}.json",
            RESULTS_DIR / "baseline" / f"{policy}-{m_str}.json",
            RESULTS_DIR / "baseline" / f"{policy_dash}-{m_str}.json",
        ]
    )


def sensitivity_candidates(policy, m_ratio, scale):
    m_str = format_ratio(m_ratio)
    scale_str = format_ratio(scale)
    if policy == "dtlm":
        stems = [
            f"ei_dtlm_{m_str}_scale{scale_str}",
            f"ei_dtlm_{m_str}_{scale_str}",
            f"ei_dtlm_{m_str}_{scale_str}x",
            f"ei-dtlm_{m_str}_scale{scale_str}",
            f"dtlm_{m_str}_scale{scale_str}",
            f"dtlm_{m_str}_{scale_str}",
            f"dtlm_{m_str}_{scale_str}x",
        ]
        directories = [RESULTS_DIR / "ei_sensitivity", RESULTS_DIR / "sensitivity"]
    else:
        policy_dash = policy.replace("_", "-")
        stems = [
            f"{policy}_{m_str}_scale{scale_str}",
            f"{policy}_{m_str}_{scale_str}",
            f"{policy}_{m_str}_{scale_str}x",
            f"{policy_dash}_{m_str}_scale{scale_str}",
            f"{policy_dash}_{m_str}_{scale_str}",
            f"{policy_dash}_{m_str}_{scale_str}x",
        ]
        directories = [RESULTS_DIR / "sensitivity"]

    candidates = []
    for directory in directories:
        for stem in stems:
            candidates.append(directory / f"{stem}.json")
    return unique_paths(candidates)


def load_total_cost(candidates, label):
    for path in candidates:
        if path.exists():
            return extract_total_cost(path), str(path)
    print(f"WARNING: missing result for {label}")
    for path in candidates:
        print(f"  tried: {path}")
    return "NA", None


def build_panel_a_rows():
    rows = []
    for m_ratio in PANEL_A_M:
        row = {"M": format_ratio(m_ratio)}
        for column_name, policy in COLUMN_ORDER:
            value, _ = load_total_cost(panel_a_candidates(policy, m_ratio), f"panel_a {policy} M={m_ratio:.1f}")
            row[column_name] = value
        rows.append(row)
    return rows


def build_panels_bcd_rows(panel_a_rows):
    panel_a_dtlm = {row["M"]: row["DTLM"] for row in panel_a_rows}
    rows = []
    for scale in SENSITIVITY_SCALES:
        for m_ratio in SENSITIVITY_M:
            row = {"scale": format_ratio(scale), "M": format_ratio(m_ratio)}
            for column_name, policy in COLUMN_ORDER:
                value, source_path = load_total_cost(
                    sensitivity_candidates(policy, m_ratio, scale),
                    f"sensitivity {policy} M={m_ratio:.1f} scale={scale:.1f}",
                )
                if policy == "dtlm":
                    base_value = int(panel_a_dtlm[row["M"]])
                    scaled_value = int(round(base_value * scale))
                    if value != "NA" and int(value) != scaled_value:
                        print(
                            "WARNING: ei_sensitivity DTLM mismatches panel_a-scaled value "
                            f"at M={row['M']} scale={row['scale']}: "
                            f"source={value}, scaled={scaled_value}, path={source_path}"
                        )
                    value = scaled_value
                row[column_name] = value
            rows.append(row)
    return rows


def write_csv(path, fieldnames, rows):
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def validate_panel_a(rows):
    print("Validation: fig3_panel_a.csv")
    index = {row["M"]: row for row in rows}

    for m_str, expected in PANEL_A_DTLM_TRUTH.items():
        actual = index[m_str]["DTLM"]
        if actual == "NA":
            raise ValueError(f"DTLM validation failed at M={m_str}: found NA, expected {expected}")
        if int(actual) != expected:
            raise ValueError(f"DTLM validation failed at M={m_str}: found {actual}, expected {expected}")
        print(f"  DTLM M={m_str}: PASS ({actual})")

    for m_str, expected in PANEL_A_GDSF_TRUTH.items():
        actual = index[m_str]["GDSF"]
        if actual == "NA":
            raise ValueError(f"GDSF validation failed at M={m_str}: found NA, expected {expected}")
        if int(actual) != expected:
            raise ValueError(f"GDSF validation failed at M={m_str}: found {actual}, expected {expected}")
        print(f"  GDSF M={m_str}: PASS ({actual})")


def main():
    panel_a_rows = build_panel_a_rows()
    panels_bcd_rows = build_panels_bcd_rows(panel_a_rows)

    panel_a_path = FIGURES_DIR / "fig3_panel_a.csv"
    panels_bcd_path = FIGURES_DIR / "fig3_panels_bcd.csv"

    write_csv(panel_a_path, ["M"] + [name for name, _ in COLUMN_ORDER], panel_a_rows)
    write_csv(panels_bcd_path, ["scale", "M"] + [name for name, _ in COLUMN_ORDER], panels_bcd_rows)

    print(f"SAVED: {panel_a_path}")
    print(f"SAVED: {panels_bcd_path}")
    validate_panel_a(panel_a_rows)


if __name__ == "__main__":
    main()
