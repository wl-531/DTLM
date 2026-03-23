import csv
import json
from collections import Counter, defaultdict, deque
from pathlib import Path

import numpy as np

from policies.dtlm import DTLM
from policies.gdsf import GDSF
from runner import prepare_data

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "results" / "dtlm_v3_diagnosis_m1"
SELECTED_PARAMS_PATH = ROOT / "results" / "dtlm_v3" / "selected_params.json"
SEED = 42
DAY_MS = 24 * 60 * 60 * 1000
LOW_PRESSURE_MARGIN = 0.10
ONE_HOUR_MS = 3600000


class DiagnosticDTLM(DTLM):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.exit_reason_by_func = {}
        self.exit_time_by_func = {}
        self.current_request_ts = None
        self.current_request_evictions = 0
        self.current_cold_start_cause = "initial"
        self.scan_records = []
        self.ttl_delete_events = []
        self.eviction_events = []

    def _remove(self, func_id, reason="unknown", current_time=None, pressure=None,
                tau_ms=None, func_class=None):
        if func_id not in self.warm_pool:
            return
        entry = self.warm_pool[func_id]
        self.exit_reason_by_func[func_id] = reason
        self.exit_time_by_func[func_id] = current_time
        if reason == "expiry":
            self.ttl_delete_events.append({
                "timestamp_ms": current_time,
                "func_id": func_id,
                "pressure": pressure,
                "idle_ms": current_time - entry["last_access_time"],
                "tau_ms": tau_ms,
                "func_class": func_class,
                "memory_used_mb": self.current_memory,
            })
        elif reason == "eviction":
            self.eviction_events.append({
                "timestamp_ms": current_time,
                "func_id": func_id,
                "pressure": pressure,
                "priority": entry["priority"],
                "memory_used_mb": self.current_memory,
            })
        super()._remove(func_id, reason, current_time)

    def _evict_one(self, now_ts=None):
        if not self.warm_pool:
            return
        victim = min(self.warm_pool, key=lambda fid: self.warm_pool[fid]["priority"])
        pressure = self.current_memory / self.M if self.M > 0 else 0.0
        self.clock = self.warm_pool[victim]["priority"]
        self._remove(victim, reason="eviction", current_time=now_ts or self.current_request_ts, pressure=pressure)
        self.eviction_count += 1
        self.current_request_evictions += 1

    def check_ttl(self, current_time):
        if self._last_ttl_scan_ms is not None:
            if current_time - self._last_ttl_scan_ms < self.ttl_scan_interval_ms:
                return
        self._last_ttl_scan_ms = current_time

        pressure = self.current_memory / self.M if self.M > 0 else 0.0
        if pressure > self.p_deactivate:
            self.ttl_layer_skipped_scans += 1
            self.scan_records.append({
                "timestamp_ms": current_time,
                "pressure": pressure,
                "active": False,
                "skipped": True,
                "ttl_deleted": 0,
            })
            return

        ttl_deleted = 0
        self.ttl_layer_active_scans += 1
        for func_id in list(self.warm_pool):
            entry = self.warm_pool[func_id]
            idle = current_time - entry["last_access_time"]
            if current_time - entry["load_time"] < self.t_protect_ms:
                continue
            func_class = self._classify_function(func_id, current_time)
            tau_ms = self._get_tau(func_id, current_time)
            if idle >= tau_ms:
                self._remove(
                    func_id,
                    reason="expiry",
                    current_time=current_time,
                    pressure=pressure,
                    tau_ms=tau_ms,
                    func_class=func_class,
                )
                self.ttl_reclaim_count += 1
                ttl_deleted += 1
        self.scan_records.append({
            "timestamp_ms": current_time,
            "pressure": pressure,
            "active": True,
            "skipped": False,
            "ttl_deleted": ttl_deleted,
        })

    def on_request(self, timestamp_ms, func_id):
        self.current_request_ts = timestamp_ms
        self.current_request_evictions = 0
        self.current_cold_start_cause = "initial"
        is_cold = super().on_request(timestamp_ms, func_id)
        if is_cold:
            last_reason = self.exit_reason_by_func.get(func_id)
            if not self.ever_seen.get(func_id, False):
                self.current_cold_start_cause = "initial"
            elif last_reason == "expiry":
                self.current_cold_start_cause = "expiry"
            elif last_reason == "eviction":
                self.current_cold_start_cause = "eviction"
            else:
                self.current_cold_start_cause = "initial"
        return is_cold


def ensure_dir(path):
    path.mkdir(parents=True, exist_ok=True)


def read_json(path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path, payload):
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    print(f"SAVED: {path}")


def write_text(path, text):
    path.write_text(text, encoding="utf-8")
    print(f"SAVED: {path}")


def write_csv(path, fieldnames, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"SAVED: {path}")


def format_percent(value):
    return f"{value * 100:.2f}%"


def quantile(values, q):
    if not values:
        return 0.0
    return float(np.quantile(values, q))


def simulate_policy(policy, stream, warmup_end_ms, functions_info, classify_thresholds=None):
    next_ttl_check_time = 60000
    mem_after_request = []
    per_func = defaultdict(lambda: {
        "requests": 0,
        "cold_starts": 0,
        "expiry_cold_starts": 0,
        "eviction_cold_starts": 0,
        "other_cold_starts": 0,
        "total_cost": 0.0,
    })
    recent_calls = defaultdict(deque)
    tau_sum_by_func = defaultdict(float)
    tau_samples_by_func = defaultdict(int)
    tau_counter = Counter()
    eviction_request_steps = 0

    total = len(stream)
    for index, (timestamp_ms, func_id, app_id, m_mb) in enumerate(stream):
        while timestamp_ms >= next_ttl_check_time:
            policy.check_ttl(next_ttl_check_time)
            next_ttl_check_time += 60000

        is_cold = policy.on_request(timestamp_ms, func_id)
        mem_used = policy.memory_used()
        if timestamp_ms < warmup_end_ms:
            if (index + 1) % 100000 == 0:
                print(f"Progress: {index + 1} / {total}")
            continue

        mem_after_request.append(mem_used)
        stats = per_func[func_id]
        stats["requests"] += 1

        if getattr(policy, "current_request_evictions", 0) > 0:
            eviction_request_steps += 1

        func_calls = recent_calls[func_id]
        func_calls.append(timestamp_ms)
        one_hour_ago = timestamp_ms - ONE_HOUR_MS
        while func_calls and func_calls[0] < one_hour_ago:
            func_calls.popleft()

        if classify_thresholds is not None:
            if len(func_calls) >= classify_thresholds["hot_threshold"]:
                tau_ms = classify_thresholds["tau_hot_ms"]
            elif len(func_calls) >= classify_thresholds["warm_threshold"]:
                tau_ms = classify_thresholds["tau_warm_ms"]
            else:
                tau_ms = classify_thresholds["tau_cold_ms"]
            tau_sum_by_func[func_id] += tau_ms
            tau_samples_by_func[func_id] += 1
            tau_counter[tau_ms] += 1

        if is_cold:
            cause = getattr(policy, "current_cold_start_cause", "initial")
            stats["cold_starts"] += 1
            stats["total_cost"] += functions_info[func_id]["c_i"]
            if cause == "expiry":
                stats["expiry_cold_starts"] += 1
            elif cause == "eviction":
                stats["eviction_cold_starts"] += 1
            else:
                stats["other_cold_starts"] += 1

        if (index + 1) % 100000 == 0:
            print(f"Progress: {index + 1} / {total}")

    return {
        "mem_after_request": mem_after_request,
        "per_func": per_func,
        "recent_calls": recent_calls,
        "tau_sum_by_func": tau_sum_by_func,
        "tau_samples_by_func": tau_samples_by_func,
        "tau_counter": tau_counter,
        "eviction_request_steps": eviction_request_steps,
    }


def build_tau_rows(per_func, recent_calls, tau_sum_by_func, tau_samples_by_func, final_timestamp, params):
    rows = []
    hot_threshold = params["hot_threshold"]
    warm_threshold = params["warm_threshold"]
    tau_hot_ms = params["tau_hot_ms"]
    tau_warm_ms = params["tau_warm_ms"]
    tau_cold_ms = params["tau_cold_ms"]

    for func_id, stats in per_func.items():
        calls = deque(recent_calls.get(func_id, deque()))
        one_hour_ago = final_timestamp - ONE_HOUR_MS
        while calls and calls[0] < one_hour_ago:
            calls.popleft()
        recent_count = len(calls)
        if recent_count >= hot_threshold:
            final_class = "hot"
            final_tau_ms = tau_hot_ms
        elif recent_count >= warm_threshold:
            final_class = "warm"
            final_tau_ms = tau_warm_ms
        else:
            final_class = "cold"
            final_tau_ms = tau_cold_ms
        sample_count = tau_samples_by_func.get(func_id, 0)
        mean_tau_ms = tau_sum_by_func.get(func_id, 0.0) / sample_count if sample_count else 0.0
        rows.append({
            "func_id": func_id,
            "request_count": stats["requests"],
            "final_recent_calls_1h": recent_count,
            "final_class": final_class,
            "final_tau_ms": final_tau_ms,
            "mean_tau_ms": round(mean_tau_ms, 2),
            "sample_count": sample_count,
            "at_upper_cap": int(final_tau_ms == tau_hot_ms),
            "at_lower_floor": int(final_tau_ms == tau_cold_ms),
        })
    rows.sort(key=lambda row: (-row["request_count"], row["func_id"]))
    return rows


def summarize_tau_distribution(tau_rows, tau_counter, params):
    tau_values = [row["final_tau_ms"] for row in tau_rows]
    if not tau_values:
        return {
            "model": "discrete_ttl_overlay",
            "note": "No evaluation requests.",
        }
    return {
        "model": "discrete_ttl_overlay",
        "note": "Current DTLM v3 has no tau_base or clamp formula; it only assigns one of three discrete TTLs by recent-call class.",
        "final_tau_ms": {
            "min": min(tau_values),
            "p50": quantile(tau_values, 0.50),
            "p90": quantile(tau_values, 0.90),
            "p95": quantile(tau_values, 0.95),
            "max": max(tau_values),
        },
        "final_upper_cap_function_ratio": sum(row["at_upper_cap"] for row in tau_rows) / len(tau_rows),
        "final_lower_floor_function_ratio": sum(row["at_lower_floor"] for row in tau_rows) / len(tau_rows),
        "request_level_tau_sample_counts": {str(key): int(value) for key, value in sorted(tau_counter.items())},
        "tau_hot_ms": params["tau_hot_ms"],
        "tau_warm_ms": params["tau_warm_ms"],
        "tau_cold_ms": params["tau_cold_ms"],
    }


def build_top_harmful_rows(dtlm_per_func, gdsf_per_func, tau_rows_by_func):
    rows = []
    for func_id, dtlm_stats in dtlm_per_func.items():
        gdsf_stats = gdsf_per_func.get(func_id, {
            "cold_starts": 0,
            "total_cost": 0.0,
        })
        tau_row = tau_rows_by_func[func_id]
        rows.append({
            "func_id": func_id,
            "request_count": dtlm_stats["requests"],
            "cold_starts": dtlm_stats["cold_starts"],
            "expiry_induced_cold_starts": dtlm_stats["expiry_cold_starts"],
            "eviction_induced_cold_starts": dtlm_stats["eviction_cold_starts"],
            "other_cold_starts": dtlm_stats["other_cold_starts"],
            "dtlm_total_cost": dtlm_stats["total_cost"],
            "gdsf_total_cost": gdsf_stats["total_cost"],
            "delta_cost_vs_gdsf": dtlm_stats["total_cost"] - gdsf_stats["total_cost"],
            "final_tau_ms": tau_row["final_tau_ms"],
            "final_class": tau_row["final_class"],
        })
    rows.sort(key=lambda row: (-row["delta_cost_vs_gdsf"], -row["dtlm_total_cost"], row["func_id"]))
    return rows[:10]


def main():
    ensure_dir(OUT_DIR)
    selected = read_json(SELECTED_PARAMS_PATH)
    params = {
        "p_deactivate": selected["p_deactivate"],
        "hot_threshold": 10,
        "warm_threshold": 1,
        "tau_hot_ms": selected["tau_hot_ms"],
        "tau_warm_ms": selected["tau_warm_ms"],
        "tau_cold_ms": selected["tau_cold_ms"],
        "t_protect_ms": 60000,
        "ttl_scan_interval_ms": 60000,
    }

    data = prepare_data(seed=SEED, days=(3, 12), working_set_days=(5, 12))
    M = data["ws_mean"]
    warmup_end_ms = data["day_offset_ms"] + 2 * DAY_MS
    final_timestamp = data["stream"][-1][0]
    functions_info = data["functions_info"]

    dtlm = DiagnosticDTLM(M, functions_info, **params)
    dtlm_run = simulate_policy(dtlm, data["stream"], warmup_end_ms, functions_info, classify_thresholds=params)

    gdsf = GDSF(M, functions_info)
    gdsf_run = simulate_policy(gdsf, data["stream"], warmup_end_ms, functions_info)

    eval_scan_records = [row for row in dtlm.scan_records if row["timestamp_ms"] >= warmup_end_ms]
    eval_ttl_delete_events = [row for row in dtlm.ttl_delete_events if row["timestamp_ms"] is not None and row["timestamp_ms"] >= warmup_end_ms]
    eval_eviction_events = [row for row in dtlm.eviction_events if row["timestamp_ms"] is not None and row["timestamp_ms"] >= warmup_end_ms]

    mem_used = dtlm_run["mem_after_request"]
    pressure_mean = float(np.mean(mem_used) / M)
    pressure_p50 = quantile(mem_used, 0.50) / M
    pressure_p95 = quantile(mem_used, 0.95) / M
    free_mem = [M - value for value in mem_used]

    dtlm_per_func = dtlm_run["per_func"]
    gdsf_per_func = gdsf_run["per_func"]
    tau_rows = build_tau_rows(
        dtlm_per_func,
        dtlm_run["recent_calls"],
        dtlm_run["tau_sum_by_func"],
        dtlm_run["tau_samples_by_func"],
        final_timestamp,
        params,
    )
    tau_rows_by_func = {row["func_id"]: row for row in tau_rows}
    harmful_rows = build_top_harmful_rows(dtlm_per_func, gdsf_per_func, tau_rows_by_func)

    expiry_cold_starts = sum(stats["expiry_cold_starts"] for stats in dtlm_per_func.values())
    eviction_cold_starts = sum(stats["eviction_cold_starts"] for stats in dtlm_per_func.values())
    other_cold_starts = sum(stats["other_cold_starts"] for stats in dtlm_per_func.values())
    expiry_cost = sum(stats["expiry_cold_starts"] * functions_info[func_id]["c_i"] for func_id, stats in dtlm_per_func.items())
    eviction_cost = sum(stats["eviction_cold_starts"] * functions_info[func_id]["c_i"] for func_id, stats in dtlm_per_func.items())
    total_cold_starts = sum(stats["cold_starts"] for stats in dtlm_per_func.values())
    total_cost = sum(stats["total_cost"] for stats in dtlm_per_func.values())
    gdsf_total_cost = sum(stats["total_cost"] for stats in gdsf_per_func.values())

    total_scans = len(eval_scan_records)
    high_pressure_scan_ratio = sum(1 for row in eval_scan_records if row["pressure"] > params["p_deactivate"]) / total_scans
    eviction_request_ratio = dtlm_run["eviction_request_steps"] / len(mem_used)
    ttl_delete_scan_ratio = sum(1 for row in eval_scan_records if row["ttl_deleted"] > 0) / total_scans
    low_pressure_threshold = max(0.0, params["p_deactivate"] - LOW_PRESSURE_MARGIN)
    clearly_low_pressure_delete_ratio = sum(1 for row in eval_ttl_delete_events if row["pressure"] <= low_pressure_threshold) / len(eval_ttl_delete_events)

    tau_summary = summarize_tau_distribution(tau_rows, dtlm_run["tau_counter"], params)
    tau_saturation = (
        tau_summary["final_upper_cap_function_ratio"] >= 0.5
        or tau_summary["final_lower_floor_function_ratio"] >= 0.5
    )

    dominant_cause = "TTL expiry" if expiry_cost > eviction_cost else "eviction"
    low_pressure_majority = clearly_low_pressure_delete_ratio >= 0.5
    fault_type = "边界没关住" if dominant_cause == "TTL expiry" and low_pressure_majority else "tau 设计失效或其他实现问题"
    recommend_patch = dominant_cause == "TTL expiry" and low_pressure_majority
    tau_saturation_text = (
        "当前实现没有 tau_base；仅有离散 TTL，且存在明显饱和现象"
        if tau_saturation else
        "当前实现没有 tau_base；仅有离散 TTL，且未观察到强烈饱和"
    )

    diagnosis_metrics = {
        "selected_params": selected,
        "M_ratio": 1.0,
        "M_MB": M,
        "evaluation_days": [5, 12],
        "cold_start_breakdown": {
            "total_cold_starts": total_cold_starts,
            "expiry_induced_cold_starts": expiry_cold_starts,
            "eviction_induced_cold_starts": eviction_cold_starts,
            "other_cold_starts": other_cold_starts,
            "expiry_induced_cost": expiry_cost,
            "eviction_induced_cost": eviction_cost,
            "total_cost": total_cost,
            "gdsf_total_cost": gdsf_total_cost,
            "delta_cost_vs_gdsf": total_cost - gdsf_total_cost,
        },
        "pressure_activity": {
            "total_scan_steps": total_scans,
            "pressure_gt_p_deactivate_ratio": high_pressure_scan_ratio,
            "eviction_request_ratio": eviction_request_ratio,
            "ttl_delete_scan_ratio": ttl_delete_scan_ratio,
            "ttl_active_scan_ratio": sum(1 for row in eval_scan_records if row["active"]) / total_scans,
            "ttl_skipped_scan_ratio": sum(1 for row in eval_scan_records if row["skipped"]) / total_scans,
        },
        "memory_utilization": {
            "mean": pressure_mean,
            "p50": pressure_p50,
            "p95": pressure_p95,
            "free_memory_mb_quantiles": {
                "p05": quantile(free_mem, 0.05),
                "p50": quantile(free_mem, 0.50),
                "p95": quantile(free_mem, 0.95),
            },
            "ttl_delete_when_util_leq_threshold": {
                "threshold": low_pressure_threshold,
                "ratio": clearly_low_pressure_delete_ratio,
            },
        },
        "tau_distribution": tau_summary,
        "event_counts": {
            "ttl_delete_events": len(eval_ttl_delete_events),
            "eviction_events": len(eval_eviction_events),
        },
    }

    summary_lines = [
        "# DTLM v3 M=1.0 定向诊断",
        "",
        "## 核心结论",
        f"- M=1.0 的额外成本主要来自：**{dominant_cause}**。",
        f"- 删除是否大多发生在低压/非紧张状态：**{'是' if low_pressure_majority else '否'}**。",
        f"- tau_base 是否存在明显饱和：**{tau_saturation_text}**。",
        f"- 当前故障更像：**{fault_type}**。",
        f"- 是否推荐进入 v3.1 修补：**{'推荐' if recommend_patch else '不推荐'}**。",
        "",
        "## 事实",
        f"- DTLM v3 total cost: {total_cost:.0f}",
        f"- GDSF total cost: {gdsf_total_cost:.0f}",
        f"- Delta cost vs GDSF: {total_cost - gdsf_total_cost:.0f}",
        f"- Expiry-induced cold starts: {expiry_cold_starts}",
        f"- Eviction-induced cold starts: {eviction_cold_starts}",
        f"- Other cold starts: {other_cold_starts}",
        f"- Expiry-induced cost: {expiry_cost:.0f}",
        f"- Eviction-induced cost: {eviction_cost:.0f}",
        f"- pressure > p_deactivate ratio: {format_percent(high_pressure_scan_ratio)}",
        f"- eviction request ratio: {format_percent(eviction_request_ratio)}",
        f"- TTL delete scan ratio: {format_percent(ttl_delete_scan_ratio)}",
        f"- mean / p50 / p95 utilization: {pressure_mean:.3f} / {pressure_p50:.3f} / {pressure_p95:.3f}",
        f"- TTL deletes at util <= {low_pressure_threshold:.2f}: {format_percent(clearly_low_pressure_delete_ratio)}",
        "",
        "## 对 tau_base 的说明",
        "- 当前 dtlm.py 不存在 tau_base 计算、上下限 clamp 或 cap 命中逻辑。",
        "- 当前实现是基于最近 1 小时调用次数的三档离散 TTL：hot / warm / cold。",
        "- 因此本报告把 tau_base_distribution 解释为 effective TTL distribution，并明确标注这不是连续 tau_base。",
    ]

    write_text(OUT_DIR / "diagnosis_summary.md", "\n".join(summary_lines))
    write_json(OUT_DIR / "diagnosis_metrics.json", diagnosis_metrics)
    write_csv(
        OUT_DIR / "top_harmful_functions.csv",
        [
            "func_id",
            "request_count",
            "cold_starts",
            "expiry_induced_cold_starts",
            "eviction_induced_cold_starts",
            "other_cold_starts",
            "dtlm_total_cost",
            "gdsf_total_cost",
            "delta_cost_vs_gdsf",
            "final_tau_ms",
            "final_class",
        ],
        harmful_rows,
    )
    write_csv(
        OUT_DIR / "tau_base_distribution.csv",
        [
            "func_id",
            "request_count",
            "final_recent_calls_1h",
            "final_class",
            "final_tau_ms",
            "mean_tau_ms",
            "sample_count",
            "at_upper_cap",
            "at_lower_floor",
        ],
        tau_rows,
    )


if __name__ == "__main__":
    main()
