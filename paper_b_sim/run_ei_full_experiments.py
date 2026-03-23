"""EI-GDSF 全量实验脚本。

仅跑 EI-DTLM（DTLMExpInf），8 个 baselines 复用 v3.6 既有结果。
结果保存到 results/ei_baseline/ 和 results/ei_sensitivity/。
"""

import json
import time
from pathlib import Path

from runner import prepare_data, run_single

# --- 从 phase10.py 提取的口径配置（不 import phase10 以避免副作用） ---
RESULTS_DIR = Path(__file__).resolve().parent / "results"
EI_BASELINE_DIR = RESULTS_DIR / "ei_baseline"
EI_SENSITIVITY_DIR = RESULTS_DIR / "ei_sensitivity"

SEED = 42
DAYS = (3, 12)
WORKING_SET_DAYS = (5, 12)
WARMUP_DAYS = 2

BASELINE_M_RATIOS = [0.1, 0.2, 0.3, 0.5, 0.7, 1.0]
SENSITIVITY_M_RATIOS = [0.3, 0.5, 0.7]
COLD_START_SCALES = [0.5, 1.0, 2.0]

# v3.1 paper-frozen DTLM 参数
DTLM_V31_KWARGS = {
    "physical_delete_requires_pressure": True,
    "p_deactivate": 0.95,
    "hot_threshold": 10,
    "warm_threshold": 1,
    "t_protect_ms": 60000,
    "ttl_scan_interval_ms": 60000,
    "tau_hot_ms": 1200000,
    "tau_warm_ms": 360000,
    "tau_cold_ms": 120000,
}

# 尝试从 selected_params.json 加载 tau（与 phase10.py 一致）
_PARAMS_PATH = RESULTS_DIR / "dtlm_v3" / "selected_params.json"
if _PARAMS_PATH.exists():
    with open(_PARAMS_PATH, "r", encoding="utf-8") as _f:
        _saved = json.load(_f)
    for _key in ("tau_hot_ms", "tau_warm_ms", "tau_cold_ms"):
        if _key in _saved:
            DTLM_V31_KWARGS[_key] = _saved[_key]
    print(f"[ei_full] Loaded tau from {_PARAMS_PATH}: "
          f"hot={DTLM_V31_KWARGS['tau_hot_ms']}, "
          f"warm={DTLM_V31_KWARGS['tau_warm_ms']}, "
          f"cold={DTLM_V31_KWARGS['tau_cold_ms']}")
else:
    print(f"[ei_full] selected_params.json not found, using fallback tau")

# --- 注册 DTLMExpInf 到 runner.POLICY_MAP ---
from policies.dtlm_ei import DTLMExpInf
import runner
runner.POLICY_MAP["ei_dtlm"] = DTLMExpInf

EI_POLICY = "ei_dtlm"


def fmt(value):
    return f"{value:.1f}"


def run_baseline():
    """运行 EI-DTLM baseline：6 个 M_ratio × cost_scale=1.0"""
    print("=" * 60)
    print("EI-GDSF 全量实验 — Baseline")
    print("=" * 60)

    EI_BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    data = prepare_data(seed=SEED, days=DAYS, working_set_days=WORKING_SET_DAYS)

    total = len(BASELINE_M_RATIOS)
    for i, m_ratio in enumerate(BASELINE_M_RATIOS, 1):
        path = EI_BASELINE_DIR / f"{EI_POLICY}_{fmt(m_ratio)}.json"
        if path.exists():
            print(f"[{i}/{total}] EI-DTLM M={fmt(m_ratio)} — 已有结果，跳过")
            continue

        t0 = time.time()
        result = run_single(
            data,
            EI_POLICY,
            m_ratio,
            cold_start_scale=1.0,
            output_path=str(path),
            warmup_days=WARMUP_DAYS,
            policy_kwargs=dict(DTLM_V31_KWARGS),
        )
        elapsed = time.time() - t0
        m = result["metrics"]
        print(f"[{i}/{total}] EI-DTLM M={fmt(m_ratio)} done: "
              f"CSR={m['cold_start_rate']*100:.2f}% "
              f"Cost={m['total_cold_start_cost']:.0f} "
              f"Time={elapsed:.1f}s")

    print(f"\nBaseline 完成：{total} 个组合")


def run_sensitivity():
    """运行 EI-DTLM sensitivity：3 M_ratio × 3 scale"""
    print("=" * 60)
    print("EI-GDSF 全量实验 — Sensitivity")
    print("=" * 60)

    EI_SENSITIVITY_DIR.mkdir(parents=True, exist_ok=True)
    data = prepare_data(seed=SEED, days=DAYS, working_set_days=WORKING_SET_DAYS)

    combos = [(m, s) for m in SENSITIVITY_M_RATIOS for s in COLD_START_SCALES]
    total = len(combos)

    for i, (m_ratio, scale) in enumerate(combos, 1):
        path = EI_SENSITIVITY_DIR / f"{EI_POLICY}_{fmt(m_ratio)}_{fmt(scale)}.json"
        if path.exists():
            print(f"[{i}/{total}] EI-DTLM M={fmt(m_ratio)} scale={fmt(scale)} — 已有结果，跳过")
            continue

        # scale=1.0 可从 baseline 复制，但为简单起见直接跑（也就几秒）
        t0 = time.time()
        result = run_single(
            data,
            EI_POLICY,
            m_ratio,
            cold_start_scale=scale,
            output_path=str(path),
            warmup_days=WARMUP_DAYS,
            policy_kwargs=dict(DTLM_V31_KWARGS),
        )
        elapsed = time.time() - t0
        m = result["metrics"]
        print(f"[{i}/{total}] EI-DTLM M={fmt(m_ratio)} scale={fmt(scale)} done: "
              f"CSR={m['cold_start_rate']*100:.2f}% "
              f"Cost={m['total_cold_start_cost']:.0f} "
              f"Time={elapsed:.1f}s")

    print(f"\nSensitivity 完成：{total} 个组合")


def main():
    t0 = time.time()
    run_baseline()
    print()
    run_sensitivity()
    print(f"\n全部完成，总耗时 {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
