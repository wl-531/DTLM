"""补跑 EI-DTLM M=0.2 的 sensitivity 实验（3 个组合）。"""

import json
import time
from pathlib import Path

from runner import prepare_data, run_single
from policies.dtlm_ei import DTLMExpInf
import runner

runner.POLICY_MAP["ei_dtlm"] = DTLMExpInf

RESULTS_DIR = Path(__file__).resolve().parent / "results"
EI_SENSITIVITY_DIR = RESULTS_DIR / "ei_sensitivity"

SEED = 42
DAYS = (3, 12)
WORKING_SET_DAYS = (5, 12)
WARMUP_DAYS = 2

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

_PARAMS_PATH = RESULTS_DIR / "dtlm_v3" / "selected_params.json"
if _PARAMS_PATH.exists():
    with open(_PARAMS_PATH, "r", encoding="utf-8") as _f:
        _saved = json.load(_f)
    for _key in ("tau_hot_ms", "tau_warm_ms", "tau_cold_ms"):
        if _key in _saved:
            DTLM_V31_KWARGS[_key] = _saved[_key]
    print(f"[m02] Loaded tau from {_PARAMS_PATH}")


def fmt(v):
    return f"{v:.1f}"


def main():
    EI_SENSITIVITY_DIR.mkdir(parents=True, exist_ok=True)
    data = prepare_data(seed=SEED, days=DAYS, working_set_days=WORKING_SET_DAYS)

    m_ratio = 0.2
    scales = [0.5, 1.0, 2.0]

    for i, scale in enumerate(scales, 1):
        path = EI_SENSITIVITY_DIR / f"ei_dtlm_{fmt(m_ratio)}_{fmt(scale)}.json"
        if path.exists():
            print(f"[{i}/3] EI-DTLM M={fmt(m_ratio)} scale={fmt(scale)} — already exists, skip")
            continue

        t0 = time.time()
        result = run_single(
            data,
            "ei_dtlm",
            m_ratio,
            cold_start_scale=scale,
            output_path=str(path),
            warmup_days=WARMUP_DAYS,
            policy_kwargs=dict(DTLM_V31_KWARGS),
        )
        elapsed = time.time() - t0
        m = result["metrics"]
        print(f"[{i}/3] EI-DTLM M={fmt(m_ratio)} scale={fmt(scale)} done: "
              f"CSR={m['cold_start_rate']*100:.2f}% "
              f"Cost={m['total_cold_start_cost']:.0f} "
              f"Time={elapsed:.1f}s")

    print("\n补跑完成：3/3")


if __name__ == "__main__":
    main()
