"""Paper-frozen experiment entry for `paper_b_sim`."""

import contextlib
import json
import sys
import time
import traceback
from pathlib import Path

from engine import simulate
from metrics import summary
from runner import DAY_MS, POLICY_MAP, prepare_data, run_divergence_pair, run_single

ROOT = Path(__file__).resolve().parent
RESULTS_DIR = ROOT / "results"
LOG_PATH = ROOT / "full_run_log.txt"

SEED = 42
DAYS = (3, 12)
WORKING_SET_DAYS = (5, 12)
WARMUP_DAYS = 2
COLD_START_SCALE = 1.0

M_RATIOS_BASELINE = [0.1, 0.2, 0.3, 0.5, 0.7, 1.0]
M_RATIOS_SENSITIVITY = [0.3, 0.5, 0.7]
M_RATIOS_DELETION = [0.3, 0.5, 0.7, 1.0]
COLD_START_SCALES = [0.5, 1.0, 2.0]

RESULT_SUBDIRS = {
    "naive_overlay": RESULTS_DIR / "naive_overlay",
    "gate_ablation": RESULTS_DIR / "gate_ablation",
    "baseline": RESULTS_DIR / "baseline",
    "sensitivity": RESULTS_DIR / "sensitivity",
    "ei_ablation": RESULTS_DIR / "ei_ablation",
    "deletion_timing": RESULTS_DIR / "deletion_timing",
    "divergence": RESULTS_DIR / "divergence",
}

BASELINE_POLICY_CANDIDATES = [
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

PLANNED_BUT_MISSING_NAMES = ["fifo", "s_cache", "dtlm_vanilla_gdsf"]
EI_ABLATION_CANDIDATES = ["dtlm", "ei_dtlm"]

DTLM_VFINAL_KWARGS = {
    "physical_delete_requires_pressure": True,
    "p_deactivate": 0.95,
    "hot_threshold": 10,
    "warm_threshold": 1,
    "tau_hot_ms": 1200000,
    "tau_warm_ms": 360000,
    "tau_cold_ms": 120000,
    "t_protect_ms": 60000,
    "ttl_scan_interval_ms": 60000,
}

DTLM_NAIVE_KWARGS = {
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


class Tee:
    """Send redirected stdout/stderr to multiple streams."""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for stream in self.streams:
            stream.write(data)
        return len(data)

    def flush(self):
        for stream in self.streams:
            stream.flush()


class RunLogger:
    """Console + file logger with optional stdout/stderr capture."""

    def __init__(self, path):
        self.path = Path(path)
        self.handle = self.path.open("w", encoding="utf-8")

    def close(self):
        self.handle.close()

    def log(self, message=""):
        print(message)
        self.handle.write(message + "\n")
        self.handle.flush()

    @contextlib.contextmanager
    def capture(self, echo=False):
        out = Tee(sys.stdout, self.handle) if echo else self.handle
        err = Tee(sys.stderr, self.handle) if echo else self.handle
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            yield

    def log_exception(self, label, exc, context):
        self.log(f"错误原因: {label}: {exc}")
        self.handle.write("\n" + "-" * 72 + "\n")
        self.handle.write(f"TRACEBACK CONTEXT: {label}\n")
        for key, value in context.items():
            self.handle.write(f"{key}: {value}\n")
        traceback.print_exc(file=self.handle)
        self.handle.write("-" * 72 + "\n\n")
        self.handle.flush()


def fmt_ratio(value):
    return f"{value:.1f}"


def fmt_metric(value):
    if value == "field not available":
        return value
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def ensure_output_dirs():
    for path in RESULT_SUBDIRS.values():
        path.mkdir(parents=True, exist_ok=True)


def baseline_policies():
    return [name for name in BASELINE_POLICY_CANDIDATES if name in POLICY_MAP]


def ei_ablation_policies():
    return [name for name in EI_ABLATION_CANDIDATES if name in POLICY_MAP]


def policy_kwargs_for(policy_name):
    if policy_name in {"dtlm", "ei_dtlm"}:
        return dict(DTLM_VFINAL_KWARGS)
    return None


def immediate_files(directory):
    if not directory.exists():
        return set()
    return {path.name for path in directory.iterdir() if path.is_file()}


def count_immediate_files(directory):
    return len(immediate_files(directory))


def new_tracker(exp_id, name, output_dir):
    return {
        "exp_id": exp_id,
        "name": name,
        "output_dir": Path(output_dir),
        "before_files": immediate_files(output_dir),
        "successes": 0,
        "failures": 0,
        "skips": 0,
        "issues": [],
        "sample_payload": None,
        "metric_summary": "field not available",
        "policy_runs": {},
    }


def _policy_run_bucket(tracker, policy_name):
    if policy_name is None:
        return None
    return tracker["policy_runs"].setdefault(
        policy_name,
        {"success": 0, "failure": 0, "skip": 0},
    )


def record_success(tracker, payload, policy_name=None):
    tracker["successes"] += 1
    if tracker["sample_payload"] is None and payload is not None:
        tracker["sample_payload"] = payload
    bucket = _policy_run_bucket(tracker, policy_name)
    if bucket is not None:
        bucket["success"] += 1


def record_issue(tracker, kind, label, reason, policy_name=None):
    tracker["issues"].append(
        {
            "kind": kind,
            "label": label,
            "reason": str(reason),
            "policy": policy_name,
        }
    )
    if kind == "skip":
        tracker["skips"] += 1
    else:
        tracker["failures"] += 1
    bucket = _policy_run_bucket(tracker, policy_name)
    if bucket is not None:
        bucket["skip" if kind == "skip" else "failure"] += 1


def experiment_status(tracker):
    if tracker["successes"] and not tracker["failures"] and not tracker["skips"]:
        return "成功"
    if tracker["successes"]:
        return "部分成功"
    return "失败"


def summarize_metrics(payload):
    if not payload:
        return "field not available"
    metrics = payload.get("metrics", {})
    summary_block = payload.get("summary", {})
    expiry = payload.get("expiry_physical_deletion", {})

    fragments = []
    if "total_cold_start_cost" in metrics:
        fragments.append(f"total_cold_start_cost={fmt_metric(metrics['total_cold_start_cost'])}")
    if "total_cold_starts" in metrics:
        fragments.append(f"total_cold_starts={fmt_metric(metrics['total_cold_starts'])}")
    if "avg_memory_utilization" in metrics:
        fragments.append(f"avg_memory_utilization={fmt_metric(metrics['avg_memory_utilization'])}")
    if "mean_jaccard" in summary_block:
        fragments.append(f"mean_jaccard={fmt_metric(summary_block['mean_jaccard'])}")
    if "total_delta_cost" in summary_block:
        fragments.append(f"total_delta_cost={fmt_metric(summary_block['total_delta_cost'])}")
    if "count" in expiry:
        fragments.append(f"expiry_count={fmt_metric(expiry['count'])}")
    if "mean_pressure" in expiry:
        fragments.append(f"expiry_mean_pressure={fmt_metric(expiry['mean_pressure'])}")

    return ", ".join(fragments) if fragments else "field not available"


def print_experiment_summary(logger, tracker):
    after_files = immediate_files(tracker["output_dir"])
    new_files = len(after_files - tracker["before_files"])
    logger.log("")
    logger.log(f"{tracker['exp_id']} 摘要")
    logger.log(f"实验名称: {tracker['name']}")
    logger.log(f"状态: {experiment_status(tracker)}")
    logger.log(f"输出目录: {tracker['output_dir']}")
    logger.log(f"新生成文件数: {new_files}")
    logger.log(f"关键指标摘要: {tracker['metric_summary']}")


def run_standard_experiment(data, policy_name, m_ratio, output_path, policy_kwargs=None, cold_start_scale=COLD_START_SCALE):
    return run_single(
        data,
        policy_name,
        m_ratio,
        cold_start_scale=cold_start_scale,
        policy_kwargs=policy_kwargs,
        output_path=str(output_path),
        warmup_days=WARMUP_DAYS,
    )


def run_dtlm_with_deletion_capture(data, m_ratio, output_path, policy_kwargs):
    """Minimal extension for raw expiry-deletion pressure samples."""
    policy_cls = POLICY_MAP["dtlm"]
    functions_info = data["functions_info"]
    memory_budget = data["ws_mean"] * m_ratio
    warmup_end_ms = data["day_offset_ms"] + WARMUP_DAYS * DAY_MS

    start = time.time()
    policy = policy_cls(memory_budget, functions_info, **policy_kwargs)
    results = simulate(policy, data["stream"], warmup_end_ms=warmup_end_ms)
    metrics = summary(results, functions_info, memory_budget, policy=policy, skip_warmup=True)
    metrics["runtime_seconds"] = round(time.time() - start, 1)

    pressure_samples = [
        row["memory_utilization"]
        for row in policy.deletion_log
        if row["reason"] == "expiry" and row["timestamp"] >= warmup_end_ms
    ]
    payload = {
        "policy": "dtlm",
        "M_ratio": m_ratio,
        "M_MB": round(memory_budget, 1),
        "cold_start_scale": COLD_START_SCALE,
        "seed": data["seed"],
        "days": list(data["days"]),
        "working_set_days": list(data["working_set_days"]),
        "warmup_days": WARMUP_DAYS,
        "working_set_mean_MB": round(data["ws_mean"], 1),
        "policy_kwargs": dict(policy_kwargs),
        "metrics": metrics,
        "final_state": policy.get_state(),
        "expiry_physical_deletion": {
            "count": len(pressure_samples),
            "pressure_samples": [round(value, 6) for value in pressure_samples],
            "mean_pressure": (
                round(sum(pressure_samples) / len(pressure_samples), 6)
                if pressure_samples else "field not available"
            ),
        },
    }

    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    print(f"SAVED: {output_path}")
    return payload


def run_captured_task(logger, tracker, label, run_callable, context, policy_name=None):
    logger.log(f"{tracker['exp_id']} 执行: {label}")
    try:
        with logger.capture(echo=False):
            payload = run_callable()
        record_success(tracker, payload, policy_name=policy_name)
        return payload
    except Exception as exc:
        record_issue(tracker, "error", label, exc, policy_name=policy_name)
        logger.log_exception(label, exc, context)
        return None


def skip_task(logger, tracker, label, reason, policy_name=None):
    record_issue(tracker, "skip", label, reason, policy_name=policy_name)
    logger.log(f"跳过原因: {label}: {reason}")


def exp1_naive_overlay(data, logger):
    tracker = new_tracker("EXP 1", "naive_overlay", RESULT_SUBDIRS["naive_overlay"])
    output_path = RESULT_SUBDIRS["naive_overlay"] / "naive_1.0.json"
    logger.log("\n" + "=" * 72)
    logger.log("EXP 1 - naive_overlay")
    logger.log(f"output: {output_path}")
    logger.log("=" * 72)

    if data is None:
        skip_task(logger, tracker, "naive_1.0", "shared data preparation failed")
    else:
        payload = run_captured_task(
            logger,
            tracker,
            "dtlm naive M=1.0",
            lambda: run_dtlm_with_deletion_capture(
                data,
                1.0,
                output_path,
                dict(DTLM_NAIVE_KWARGS),
            ),
            {
                "exp": tracker["name"],
                "policy": "dtlm",
                "M_ratio": 1.0,
                "output_path": str(output_path),
            },
            policy_name="dtlm",
        )
        tracker["metric_summary"] = summarize_metrics(payload)

    print_experiment_summary(logger, tracker)
    return tracker


def exp2_gate_ablation(data, logger):
    tracker = new_tracker("EXP 2", "gate_ablation", RESULT_SUBDIRS["gate_ablation"])
    gate_off_path = RESULT_SUBDIRS["gate_ablation"] / "gate_off_1.0.json"
    gate_on_path = RESULT_SUBDIRS["gate_ablation"] / "gate_on_1.0.json"
    logger.log("\n" + "=" * 72)
    logger.log("EXP 2 - gate_ablation")
    logger.log(f"output: {RESULT_SUBDIRS['gate_ablation']}")
    logger.log("=" * 72)

    if data is None:
        skip_task(logger, tracker, "gate_off_1.0", "shared data preparation failed", policy_name="dtlm")
        skip_task(logger, tracker, "gate_on_1.0", "shared data preparation failed", policy_name="dtlm")
    else:
        gate_off_kwargs = dict(DTLM_VFINAL_KWARGS)
        gate_off_kwargs["physical_delete_requires_pressure"] = False
        gate_on_kwargs = dict(DTLM_VFINAL_KWARGS)
        gate_on_kwargs["physical_delete_requires_pressure"] = True

        gate_off = run_captured_task(
            logger,
            tracker,
            "gate_off M=1.0",
            lambda: run_standard_experiment(data, "dtlm", 1.0, gate_off_path, policy_kwargs=gate_off_kwargs),
            {
                "exp": tracker["name"],
                "variant": "gate_off",
                "M_ratio": 1.0,
                "output_path": str(gate_off_path),
            },
            policy_name="dtlm",
        )
        gate_on = run_captured_task(
            logger,
            tracker,
            "gate_on M=1.0",
            lambda: run_standard_experiment(data, "dtlm", 1.0, gate_on_path, policy_kwargs=gate_on_kwargs),
            {
                "exp": tracker["name"],
                "variant": "gate_on",
                "M_ratio": 1.0,
                "output_path": str(gate_on_path),
            },
            policy_name="dtlm",
        )
        fragments = []
        if gate_off:
            fragments.append(f"gate_off total_cold_start_cost={fmt_metric(gate_off['metrics'].get('total_cold_start_cost'))}")
        if gate_on:
            fragments.append(f"gate_on total_cold_start_cost={fmt_metric(gate_on['metrics'].get('total_cold_start_cost'))}")
        tracker["metric_summary"] = ", ".join(fragments) if fragments else "field not available"

    print_experiment_summary(logger, tracker)
    return tracker


def exp3_baseline(data, logger):
    tracker = new_tracker("EXP 3", "baseline", RESULT_SUBDIRS["baseline"])
    policies = baseline_policies()
    missing = [name for name in PLANNED_BUT_MISSING_NAMES if name not in POLICY_MAP]
    logger.log("\n" + "=" * 72)
    logger.log("EXP 3 - baseline")
    logger.log(f"available policies: {', '.join(policies) if policies else 'none'}")
    logger.log(f"output: {RESULT_SUBDIRS['baseline']}")
    logger.log("=" * 72)

    for missing_name in missing:
        skip_task(
            logger,
            tracker,
            f"policy {missing_name}",
            "policy not found in POLICY_MAP",
            policy_name=missing_name,
        )

    if data is None:
        for policy_name in policies:
            skip_task(logger, tracker, f"policy {policy_name}", "shared data preparation failed", policy_name=policy_name)
    else:
        for policy_name in policies:
            kwargs = policy_kwargs_for(policy_name)
            for m_ratio in M_RATIOS_BASELINE:
                output_path = RESULT_SUBDIRS["baseline"] / f"{policy_name}_{fmt_ratio(m_ratio)}.json"
                payload = run_captured_task(
                    logger,
                    tracker,
                    f"{policy_name} M={fmt_ratio(m_ratio)}",
                    lambda policy_name=policy_name, m_ratio=m_ratio, output_path=output_path, kwargs=kwargs: run_standard_experiment(
                        data,
                        policy_name,
                        m_ratio,
                        output_path,
                        policy_kwargs=kwargs,
                    ),
                    {
                        "exp": tracker["name"],
                        "policy": policy_name,
                        "M_ratio": m_ratio,
                        "output_path": str(output_path),
                    },
                    policy_name=policy_name,
                )
                if payload and policy_name == "gdsf" and m_ratio == 1.0:
                    tracker["metric_summary"] = summarize_metrics(payload)

    if tracker["metric_summary"] == "field not available":
        tracker["metric_summary"] = f"success_runs={tracker['successes']}, failed_runs={tracker['failures']}, skipped_items={tracker['skips']}"

    print_experiment_summary(logger, tracker)
    return tracker


def exp4_sensitivity(data, logger):
    tracker = new_tracker("EXP 4", "sensitivity", RESULT_SUBDIRS["sensitivity"])
    policies = baseline_policies()
    missing = [name for name in PLANNED_BUT_MISSING_NAMES if name not in POLICY_MAP]
    logger.log("\n" + "=" * 72)
    logger.log("EXP 4 - sensitivity")
    logger.log(f"available policies: {', '.join(policies) if policies else 'none'}")
    logger.log(f"output: {RESULT_SUBDIRS['sensitivity']}")
    logger.log("=" * 72)

    for missing_name in missing:
        skip_task(
            logger,
            tracker,
            f"policy {missing_name}",
            "policy not found in POLICY_MAP",
            policy_name=missing_name,
        )

    if data is None:
        for policy_name in policies:
            skip_task(logger, tracker, f"policy {policy_name}", "shared data preparation failed", policy_name=policy_name)
    else:
        for policy_name in policies:
            kwargs = policy_kwargs_for(policy_name)
            for m_ratio in M_RATIOS_SENSITIVITY:
                for scale in COLD_START_SCALES:
                    output_path = RESULT_SUBDIRS["sensitivity"] / f"{policy_name}_{fmt_ratio(m_ratio)}_{fmt_ratio(scale)}.json"
                    payload = run_captured_task(
                        logger,
                        tracker,
                        f"{policy_name} M={fmt_ratio(m_ratio)} scale={fmt_ratio(scale)}",
                        lambda policy_name=policy_name, m_ratio=m_ratio, output_path=output_path, kwargs=kwargs, scale=scale: run_standard_experiment(
                            data,
                            policy_name,
                            m_ratio,
                            output_path,
                            policy_kwargs=kwargs,
                            cold_start_scale=scale,
                        ),
                        {
                            "exp": tracker["name"],
                            "policy": policy_name,
                            "M_ratio": m_ratio,
                            "cold_start_scale": scale,
                            "output_path": str(output_path),
                        },
                        policy_name=policy_name,
                    )
                    if payload and policy_name == "dtlm" and m_ratio == 0.7 and scale == 1.0:
                        tracker["metric_summary"] = summarize_metrics(payload)

    if tracker["metric_summary"] == "field not available":
        tracker["metric_summary"] = f"success_runs={tracker['successes']}, failed_runs={tracker['failures']}, skipped_items={tracker['skips']}"

    print_experiment_summary(logger, tracker)
    return tracker


def exp5_ei_ablation(data, logger):
    tracker = new_tracker("EXP 5", "ei_ablation", RESULT_SUBDIRS["ei_ablation"])
    available = ei_ablation_policies()
    missing = [name for name in EI_ABLATION_CANDIDATES if name not in POLICY_MAP]
    logger.log("\n" + "=" * 72)
    logger.log("EXP 5 - ei_ablation")
    logger.log(f"available policies: {', '.join(available) if available else 'none'}")
    logger.log(f"output: {RESULT_SUBDIRS['ei_ablation']}")
    logger.log("=" * 72)

    for missing_name in missing:
        skip_task(
            logger,
            tracker,
            f"policy {missing_name}",
            "policy not found in POLICY_MAP",
            policy_name=missing_name,
        )

    if not available:
        skip_task(logger, tracker, "ei_ablation", "no legal EI ablation policy is available")
    elif data is None:
        for policy_name in available:
            skip_task(logger, tracker, f"policy {policy_name}", "shared data preparation failed", policy_name=policy_name)
    else:
        for policy_name in available:
            kwargs = policy_kwargs_for(policy_name)
            for m_ratio in M_RATIOS_BASELINE:
                output_path = RESULT_SUBDIRS["ei_ablation"] / f"{policy_name}_{fmt_ratio(m_ratio)}.json"
                payload = run_captured_task(
                    logger,
                    tracker,
                    f"{policy_name} M={fmt_ratio(m_ratio)}",
                    lambda policy_name=policy_name, m_ratio=m_ratio, output_path=output_path, kwargs=kwargs: run_standard_experiment(
                        data,
                        policy_name,
                        m_ratio,
                        output_path,
                        policy_kwargs=kwargs,
                    ),
                    {
                        "exp": tracker["name"],
                        "policy": policy_name,
                        "M_ratio": m_ratio,
                        "output_path": str(output_path),
                    },
                    policy_name=policy_name,
                )
                if payload and policy_name == "ei_dtlm" and m_ratio == 1.0:
                    tracker["metric_summary"] = summarize_metrics(payload)

    if tracker["metric_summary"] == "field not available":
        tracker["metric_summary"] = f"success_runs={tracker['successes']}, failed_runs={tracker['failures']}, skipped_items={tracker['skips']}"

    print_experiment_summary(logger, tracker)
    return tracker


def exp6_deletion_timing(data, logger):
    tracker = new_tracker("EXP 6", "deletion_timing", RESULT_SUBDIRS["deletion_timing"])
    logger.log("\n" + "=" * 72)
    logger.log("EXP 6 - deletion_timing")
    logger.log(f"output: {RESULT_SUBDIRS['deletion_timing']}")
    logger.log("=" * 72)

    if data is None:
        for m_ratio in M_RATIOS_DELETION:
            skip_task(logger, tracker, f"dtlm M={fmt_ratio(m_ratio)}", "shared data preparation failed", policy_name="dtlm")
    else:
        for m_ratio in M_RATIOS_DELETION:
            output_path = RESULT_SUBDIRS["deletion_timing"] / f"dtlm_{fmt_ratio(m_ratio)}.json"
            payload = run_captured_task(
                logger,
                tracker,
                f"dtlm M={fmt_ratio(m_ratio)}",
                lambda m_ratio=m_ratio, output_path=output_path: run_dtlm_with_deletion_capture(
                    data,
                    m_ratio,
                    output_path,
                    dict(DTLM_VFINAL_KWARGS),
                ),
                {
                    "exp": tracker["name"],
                    "policy": "dtlm",
                    "M_ratio": m_ratio,
                    "output_path": str(output_path),
                },
                policy_name="dtlm",
            )
            if payload and m_ratio == 1.0:
                tracker["metric_summary"] = summarize_metrics(payload)

    print_experiment_summary(logger, tracker)
    return tracker


def exp7_divergence(data, logger):
    tracker = new_tracker("EXP 7", "divergence", RESULT_SUBDIRS["divergence"])
    logger.log("\n" + "=" * 72)
    logger.log("EXP 7 - divergence")
    logger.log(f"output: {RESULT_SUBDIRS['divergence']}")
    logger.log("=" * 72)

    if data is None:
        for m_ratio in M_RATIOS_DELETION:
            skip_task(logger, tracker, f"dtlm_vs_gdsf M={fmt_ratio(m_ratio)}", "shared data preparation failed")
    else:
        for m_ratio in M_RATIOS_DELETION:
            output_path = RESULT_SUBDIRS["divergence"] / f"dtlm_vs_gdsf_{fmt_ratio(m_ratio)}.json"

            def runner():
                result = run_divergence_pair(
                    data,
                    dtlm_config={
                        "M_ratio": m_ratio,
                        "warmup_days": WARMUP_DAYS,
                        "policy_kwargs": dict(DTLM_VFINAL_KWARGS),
                    },
                    gdsf_config={
                        "M_ratio": m_ratio,
                        "warmup_days": WARMUP_DAYS,
                    },
                    snapshot_interval_sec=60,
                )
                payload = {
                    "policy_pair": ["dtlm", "gdsf"],
                    "M_ratio": m_ratio,
                    "snapshot_interval_sec": 60,
                    "summary": result["summary"],
                    "snapshots": [
                        {
                            **row,
                            "dtlm_warm_set": sorted(row["dtlm_warm_set"]),
                            "gdsf_warm_set": sorted(row["gdsf_warm_set"]),
                            "dtlm_only": sorted(row["dtlm_only"]),
                            "gdsf_only": sorted(row["gdsf_only"]),
                        }
                        for row in result["snapshots"]
                    ],
                }
                with output_path.open("w", encoding="utf-8") as handle:
                    json.dump(payload, handle, indent=2)
                print(f"SAVED: {output_path}")
                return payload

            payload = run_captured_task(
                logger,
                tracker,
                f"dtlm_vs_gdsf M={fmt_ratio(m_ratio)}",
                runner,
                {
                    "exp": tracker["name"],
                    "M_ratio": m_ratio,
                    "output_path": str(output_path),
                },
            )
            if payload and m_ratio == 1.0:
                tracker["metric_summary"] = summarize_metrics(payload)

    print_experiment_summary(logger, tracker)
    return tracker


def fully_successful_policies(policy_runs, expected_successes):
    policies = []
    for policy_name, stats in sorted(policy_runs.items()):
        if stats["success"] == expected_successes and stats["failure"] == 0 and stats["skip"] == 0:
            policies.append(policy_name)
    return policies


def planned_files_map():
    baseline_names = baseline_policies()
    ei_names = ei_ablation_policies()

    return {
        "naive_overlay": {"naive_1.0.json"},
        "gate_ablation": {"gate_off_1.0.json", "gate_on_1.0.json"},
        "baseline": {
            f"{policy_name}_{fmt_ratio(m_ratio)}.json"
            for policy_name in baseline_names
            for m_ratio in M_RATIOS_BASELINE
        },
        "sensitivity": {
            f"{policy_name}_{fmt_ratio(m_ratio)}_{fmt_ratio(scale)}.json"
            for policy_name in baseline_names
            for m_ratio in M_RATIOS_SENSITIVITY
            for scale in COLD_START_SCALES
        },
        "ei_ablation": {
            f"{policy_name}_{fmt_ratio(m_ratio)}.json"
            for policy_name in ei_names
            for m_ratio in M_RATIOS_BASELINE
        },
        "deletion_timing": {f"dtlm_{fmt_ratio(m_ratio)}.json" for m_ratio in M_RATIOS_DELETION},
        "divergence": {f"dtlm_vs_gdsf_{fmt_ratio(m_ratio)}.json" for m_ratio in M_RATIOS_DELETION},
    }


def load_json_field(path, field_name):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return payload["metrics"][field_name]


def final_summary(trackers, logger):
    logger.log("\n" + "=" * 72)
    logger.log("FINAL SUMMARY")
    logger.log("=" * 72)

    baseline_success_policies = fully_successful_policies(trackers["baseline"]["policy_runs"], len(M_RATIOS_BASELINE))
    ei_success_policies = fully_successful_policies(trackers["ei_ablation"]["policy_runs"], len(M_RATIOS_BASELINE))

    logger.log(f"baseline 实际成功运行 policies: {baseline_success_policies if baseline_success_policies else '[]'}")
    logger.log(f"ei_ablation 实际成功运行 policies: {ei_success_policies if ei_success_policies else '[]'}")

    all_issues = []
    for tracker in trackers.values():
        for issue in tracker["issues"]:
            all_issues.append((tracker["exp_id"], tracker["name"], issue))

    if all_issues:
        logger.log("失败/跳过项:")
        for exp_id, exp_name, issue in all_issues:
            logger.log(
                f"- {exp_id} {exp_name} | {issue['kind']} | {issue['label']} | reason={issue['reason']}"
            )
    else:
        logger.log("失败/跳过项: none")

    planned = planned_files_map()
    expected_counts = {
        "naive_overlay": 1,
        "gate_ablation": 2,
        "baseline": len(baseline_success_policies) * 6,
        "sensitivity": len(baseline_success_policies) * 9,
        "ei_ablation": len(ei_success_policies) * 6,
        "deletion_timing": 4,
        "divergence": 4,
    }

    logger.log("results/ 子目录文件统计:")
    for name, directory in RESULT_SUBDIRS.items():
        actual = count_immediate_files(directory)
        expected = expected_counts[name]
        actual_files = immediate_files(directory)
        extras = sorted(actual_files - planned[name])
        status = "OK" if actual == expected else "MISMATCH"
        logger.log(f"- {name}: actual={actual}, expected={expected}, status={status}")
        if extras:
            logger.log(f"  extra_files={extras}")

    naive_path = RESULT_SUBDIRS["naive_overlay"] / "naive_1.0.json"
    gdsf_path = RESULT_SUBDIRS["baseline"] / "gdsf_1.0.json"
    dtlm_path = RESULT_SUBDIRS["baseline"] / "dtlm_1.0.json"

    try:
        naive_cost = load_json_field(naive_path, "total_cold_start_cost")
        gdsf_cost = load_json_field(gdsf_path, "total_cold_start_cost")
        ratio = float(naive_cost) / float(gdsf_cost)
        logger.log(f"naive total_cold_start_cost: {fmt_metric(naive_cost)}")
        logger.log(f"gdsf total_cold_start_cost: {fmt_metric(gdsf_cost)}")
        logger.log(f"naive / gdsf: {ratio:.6f}")
    except Exception as exc:
        logger.log_exception(
            "paper check naive vs gdsf",
            exc,
            {
                "naive_path": str(naive_path),
                "gdsf_path": str(gdsf_path),
            },
        )

    try:
        dtlm_cost = load_json_field(dtlm_path, "total_cold_start_cost")
        expected_value = 1181620.0
        equivalent = abs(float(dtlm_cost) - expected_value) < 1e-9
        logger.log(f"dtlm total_cold_start_cost: {fmt_metric(dtlm_cost)}")
        logger.log(f"dtlm equals 1181620: {equivalent}")
    except Exception as exc:
        logger.log_exception(
            "paper check dtlm",
            exc,
            {
                "dtlm_path": str(dtlm_path),
            },
        )

    overall_has_errors = any(tracker["failures"] or tracker["skips"] for tracker in trackers.values())
    final_state = "FULL_RUN_COMPLETE_WITH_ERRORS" if overall_has_errors else "FULL_RUN_COMPLETE"
    logger.log(final_state)
    return final_state


def main():
    ensure_output_dirs()
    logger = RunLogger(LOG_PATH)
    try:
        logger.log(f"log_path: {LOG_PATH}")
        logger.log(f"Static runner policies: {', '.join(sorted(POLICY_MAP))}")
        logger.log(f"Planned-but-missing names: {', '.join(PLANNED_BUT_MISSING_NAMES)}")
        logger.log(f"seed={SEED}, days={DAYS}, working_set_days={WORKING_SET_DAYS}, warmup_days={WARMUP_DAYS}")

        try:
            with logger.capture(echo=False):
                data = prepare_data(seed=SEED, days=DAYS, working_set_days=WORKING_SET_DAYS)
        except Exception as exc:
            data = None
            logger.log_exception(
                "prepare_data",
                exc,
                {
                    "seed": SEED,
                    "days": DAYS,
                    "working_set_days": WORKING_SET_DAYS,
                },
            )

        trackers = {
            "naive_overlay": exp1_naive_overlay(data, logger),
            "gate_ablation": exp2_gate_ablation(data, logger),
            "baseline": exp3_baseline(data, logger),
            "sensitivity": exp4_sensitivity(data, logger),
            "ei_ablation": exp5_ei_ablation(data, logger),
            "deletion_timing": exp6_deletion_timing(data, logger),
            "divergence": exp7_divergence(data, logger),
        }
        final_summary(trackers, logger)
    finally:
        logger.close()


if __name__ == "__main__":
    main()
