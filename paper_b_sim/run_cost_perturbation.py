"""Per-function cold-start cost perturbation sensitivity test."""

import argparse
import copy
import csv
import json
import time
from pathlib import Path

import numpy as np

from runner import POLICY_MAP, prepare_data, run_single
from run_ei_full_experiments import DTLM_V31_KWARGS

ROOT = Path(__file__).resolve().parent
RESULTS_DIR = ROOT / "results"
OUTPUT_DIR = RESULTS_DIR / "cost_perturbation"
DRY_RUN_OUTPUT_DIR = RESULTS_DIR / "cost_perturbation_dry_run"

DATA_SEED = 42
DAYS = (3, 12)
WORKING_SET_DAYS = (5, 12)
WARMUP_DAYS = 2

PERTURBATION_SEEDS = [1001, 1002, 1003, 1004, 1005]
M_RATIOS = [0.3, 0.5, 0.7]
POLICIES = [
    "dtlm",
    "gdsf",
    "lru",
    "lfu",
    "c2rd_sr",
    "iat_adaptive_ttl",
    "adaptive_ttl_lru",
    "fixed_ttl_lru",
    "ttlmin_extnd",
]

SELECTED_PARAMS_PATH = RESULTS_DIR / "dtlm_v3" / "selected_params.json"


def fmt_ratio(value):
    return f"{value:.1f}"


def validate_policies():
    missing = [name for name in POLICIES if name not in POLICY_MAP]
    if missing:
        raise ValueError(f"Missing policies in runner.POLICY_MAP: {missing}")


def policy_kwargs_for(policy_name):
    if policy_name == "dtlm":
        return dict(DTLM_V31_KWARGS)
    return None


def generate_factors(functions_info, perturbation_seed):
    rng = np.random.default_rng(perturbation_seed)
    func_ids = sorted(functions_info)
    values = rng.uniform(0.5, 2.0, size=len(func_ids))
    return {func_id: float(value) for func_id, value in zip(func_ids, values)}


def build_perturbed_data(data, factors):
    perturbed_functions = copy.deepcopy(data["functions_info"])
    for func_id, factor in factors.items():
        perturbed_functions[func_id]["c_i"] = float(perturbed_functions[func_id]["c_i"]) * factor

    perturbed_data = dict(data)
    perturbed_data["functions_info"] = perturbed_functions
    return perturbed_data


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def run_one(data, output_dir, perturbation_seed, m_ratio, policy_name):
    seed_dir = output_dir / f"seed_{perturbation_seed}"
    output_path = seed_dir / f"{policy_name}_{fmt_ratio(m_ratio)}.json"
    if output_path.exists():
        print(f"SKIP existing: {output_path}")
        return load_json(output_path)

    result = run_single(
        data,
        policy_name,
        m_ratio,
        cold_start_scale=1.0,
        policy_kwargs=policy_kwargs_for(policy_name),
        output_path=str(output_path),
        warmup_days=WARMUP_DAYS,
    )
    result["perturbation"] = {
        "type": "per-function independent uniform multiplier",
        "seed": perturbation_seed,
        "lambda_min": 0.5,
        "lambda_max": 2.0,
    }
    save_json(output_path, result)
    return result


def total_cost(result):
    return float(result["metrics"]["total_cold_start_cost"])


def build_ranking_rows(results_by_key, seeds, m_ratios, policies):
    rows = []
    for seed in seeds:
        for m_ratio in m_ratios:
            items = []
            for policy in policies:
                result = results_by_key[(seed, m_ratio, policy)]
                items.append((policy, total_cost(result)))

            items.sort(key=lambda item: (item[1], item[0]))
            for rank, (policy, cost) in enumerate(items, 1):
                rows.append(
                    {
                        "seed": seed,
                        "M_ratio": fmt_ratio(m_ratio),
                        "policy": policy,
                        "total_cold_start_cost": f"{cost:.6f}",
                        "rank": rank,
                    }
                )
    return rows


def write_ranking_csv(output_dir, rows):
    path = output_dir / "ranking_summary.csv"
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["seed", "M_ratio", "policy", "total_cold_start_cost", "rank"],
        )
        writer.writeheader()
        writer.writerows(rows)
    return path


def markdown_ranking_table(rows, seed, m_ratio):
    selected = [
        row for row in rows
        if int(row["seed"]) == seed and row["M_ratio"] == fmt_ratio(m_ratio)
    ]
    selected.sort(key=lambda row: int(row["rank"]))
    lines = [
        f"### seed={seed}, M_ratio={fmt_ratio(m_ratio)}",
        "",
        "| rank | policy | total_cold_start_cost |",
        "|---:|---|---:|",
    ]
    for row in selected:
        lines.append(
            f"| {row['rank']} | {row['policy']} | {float(row['total_cold_start_cost']):.0f} |"
        )
    return "\n".join(lines)


def dtlm_rank_stats(rows):
    ranks = [int(row["rank"]) for row in rows if row["policy"] == "dtlm"]
    first_count = sum(1 for rank in ranks if rank == 1)
    return {
        "count": len(ranks),
        "rank_1_count": first_count,
        "worst_rank": max(ranks) if ranks else None,
        "average_rank": sum(ranks) / len(ranks) if ranks else None,
    }


def non_first_dtlm_blocks(rows, seeds, m_ratios):
    blocks = []
    for seed in seeds:
        for m_ratio in m_ratios:
            selected = [
                row for row in rows
                if int(row["seed"]) == seed and row["M_ratio"] == fmt_ratio(m_ratio)
            ]
            selected.sort(key=lambda row: int(row["rank"]))
            dtlm = next(row for row in selected if row["policy"] == "dtlm")
            if int(dtlm["rank"]) == 1:
                continue

            blocks.append(f"### DTLM not rank 1: seed={seed}, M_ratio={fmt_ratio(m_ratio)}")
            blocks.append("")
            blocks.append("| rank | policy | total_cold_start_cost |")
            blocks.append("|---:|---|---:|")
            for row in selected:
                blocks.append(
                    f"| {row['rank']} | {row['policy']} | {float(row['total_cold_start_cost']):.0f} |"
                )
            blocks.append("")
    return blocks


def write_markdown(output_dir, rows, seeds, m_ratios, factors):
    stats = dtlm_rank_stats(rows)
    factor_values = [value for seed_map in factors.values() for value in seed_map.values()]

    lines = [
        "# Per-Function Cost Perturbation Sensitivity",
        "",
        "## Setup",
        "",
        f"- Data: prepare_data(seed={DATA_SEED}, days={DAYS}, working_set_days={WORKING_SET_DAYS})",
        f"- Warmup days: {WARMUP_DAYS}",
        "- Perturbation: for each function i, lambda_i ~ Uniform(0.5, 2.0), c_i' = lambda_i * c_i",
        f"- Perturbation seeds: {', '.join(str(seed) for seed in seeds)}",
        f"- M_ratio: {', '.join(fmt_ratio(value) for value in m_ratios)}",
        f"- Policies: {', '.join(POLICIES)}",
        f"- DTLM selected params path: {SELECTED_PARAMS_PATH}",
        f"- DTLM selected params loaded: {SELECTED_PARAMS_PATH.exists()}",
        f"- DTLM kwargs: `{json.dumps(DTLM_V31_KWARGS, sort_keys=True)}`",
        f"- Lambda sanity range in generated factors: min={min(factor_values):.6f}, max={max(factor_values):.6f}",
        "",
        "## Rankings",
        "",
    ]

    for seed in seeds:
        for m_ratio in m_ratios:
            lines.append(markdown_ranking_table(rows, seed, m_ratio))
            lines.append("")

    lines.extend(
        [
            "## DTLM Rank Statistics",
            "",
            f"- Combinations: {stats['count']}",
            f"- Rank 1 count: {stats['rank_1_count']}",
            f"- Worst rank: {stats['worst_rank']}",
            f"- Average rank: {stats['average_rank']:.3f}",
            "",
            "## Uniform Scaling Comparison",
            "",
            (
                "Uniform scaling multiplies all c_i by the same constant, so score-based eviction "
                "rules that compare scores with a common multiplicative cost term keep the same "
                "argmin order. This per-function perturbation changes the relative c_i structure, "
                "so ranking changes are possible and informative."
            ),
            "",
            "## DTLM Non-First Cases",
            "",
        ]
    )

    non_first_blocks = non_first_dtlm_blocks(rows, seeds, m_ratios)
    if non_first_blocks:
        lines.extend(non_first_blocks)
    else:
        lines.append("DTLM is rank 1 in every completed combination.")

    path = output_dir / "ranking_summary.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_factors(output_dir, factors):
    path = output_dir / "perturbation_factors.json"
    save_json(path, {str(seed): mapping for seed, mapping in factors.items()})
    return path


def compare_dry_run_to_uniform(results_by_key, policies):
    print("\nDry-run comparison against existing uniform baseline files:")
    for policy in policies:
        perturbed = results_by_key[(1001, 0.3, policy)]
        perturbed_cost = total_cost(perturbed)
        candidates = [
            RESULTS_DIR / "sensitivity" / f"{policy}_0.3_1.0.json",
            RESULTS_DIR / "baseline" / f"{policy}_0.3.json",
        ]
        baseline_path = next((path for path in candidates if path.exists()), None)
        if baseline_path is None:
            print(f"  {policy}: no existing uniform baseline file found")
            continue
        baseline_cost = total_cost(load_json(baseline_path))
        delta = perturbed_cost - baseline_cost
        print(
            f"  {policy}: perturbed={perturbed_cost:.0f}, "
            f"uniform={baseline_cost:.0f}, delta={delta:.0f}"
        )


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run 1 seed x 1 M_ratio x 2 policies and write to results/cost_perturbation_dry_run.",
    )
    parser.add_argument(
        "--max-seeds",
        type=int,
        default=None,
        help="Limit perturbation seeds from the front of the seed list.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    validate_policies()

    output_dir = DRY_RUN_OUTPUT_DIR if args.dry_run else OUTPUT_DIR
    seeds = [1001] if args.dry_run else list(PERTURBATION_SEEDS)
    if args.max_seeds is not None:
        seeds = seeds[:args.max_seeds]
    m_ratios = [0.3] if args.dry_run else list(M_RATIOS)
    policies = ["dtlm", "gdsf"] if args.dry_run else list(POLICIES)

    print("Preparing shared data...")
    data = prepare_data(seed=DATA_SEED, days=DAYS, working_set_days=WORKING_SET_DAYS)

    factors = {}
    results_by_key = {}
    total_runs = len(seeds) * len(m_ratios) * len(policies)
    completed = 0
    t0 = time.time()

    for seed in seeds:
        factors[seed] = generate_factors(data["functions_info"], seed)
        perturbed_data = build_perturbed_data(data, factors[seed])
        for m_ratio in m_ratios:
            for policy in policies:
                completed += 1
                print(
                    f"\n[{completed}/{total_runs}] "
                    f"seed={seed}, M_ratio={fmt_ratio(m_ratio)}, policy={policy}"
                )
                results_by_key[(seed, m_ratio, policy)] = run_one(
                    perturbed_data,
                    output_dir,
                    seed,
                    m_ratio,
                    policy,
                )

    write_factors(output_dir, factors)
    rows = build_ranking_rows(results_by_key, seeds, m_ratios, policies)
    csv_path = write_ranking_csv(output_dir, rows)
    md_path = write_markdown(output_dir, rows, seeds, m_ratios, factors)

    if args.dry_run:
        compare_dry_run_to_uniform(results_by_key, policies)

    elapsed = time.time() - t0
    print(f"\nSaved: {csv_path}")
    print(f"Saved: {md_path}")
    print(f"Done in {elapsed:.1f}s")


if __name__ == "__main__":
    main()
