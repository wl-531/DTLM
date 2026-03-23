"""组2 分歧度实验：DTLM v3.1 vs GDSF，4 个 M_ratio，snapshot_interval=60s"""
import json
import time
from pathlib import Path

from runner import prepare_data, run_divergence_pair

# --- 与 run_small_matrix.py 完全一致的实验参数 ---
SEED = 42
DAYS = (3, 12)
WORKING_SET_DAYS = (5, 12)
WARMUP_DAYS = 2
M_RATIOS = [0.3, 0.5, 0.7, 1.0]
SNAPSHOT_INTERVAL_SEC = 60

_PROJECT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = _PROJECT_DIR / "results" / "divergence_v3_1"

# --- DTLM v3.1 参数（与 run_small_matrix.py 一致） ---
_TAU_FALLBACK = {
    "tau_hot_ms": 1200000,
    "tau_warm_ms": 360000,
    "tau_cold_ms": 120000,
}


def _load_tau_values():
    """仅从 selected_params.json 读取 tau，其余参数硬编码"""
    taus = dict(_TAU_FALLBACK)
    params_path = _PROJECT_DIR / "results" / "dtlm_v3" / "selected_params.json"
    if params_path.exists():
        with open(params_path, "r", encoding="utf-8") as f:
            saved = json.load(f)
        for key in ("tau_hot_ms", "tau_warm_ms", "tau_cold_ms"):
            if key in saved:
                taus[key] = saved[key]
        print(f"Loaded tau from {params_path}: {taus}")
    else:
        print(f"selected_params.json not found, using fallback: {taus}")
    return taus


def _build_dtlm_v31_kwargs(taus):
    return {
        "physical_delete_requires_pressure": True,
        "p_deactivate": 0.95,
        "hot_threshold": 10,
        "warm_threshold": 1,
        "t_protect_ms": 60000,
        "ttl_scan_interval_ms": 60000,
        **taus,
    }


def _make_json_serializable(payload):
    """将快照中的 set 转为 sorted list"""
    snapshots = []
    for row in payload["snapshots"]:
        s = dict(row)
        for key in ("dtlm_warm_set", "gdsf_warm_set", "dtlm_only", "gdsf_only"):
            if isinstance(s.get(key), set):
                s[key] = sorted(s[key])
        snapshots.append(s)
    return {"snapshots": snapshots, "summary": payload["summary"]}


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    taus = _load_tau_values()
    dtlm_kwargs = _build_dtlm_v31_kwargs(taus)
    print(f"DTLM v3.1 kwargs: {dtlm_kwargs}")

    data = prepare_data(seed=SEED, days=DAYS, working_set_days=WORKING_SET_DAYS)

    for m_ratio in M_RATIOS:
        print(f"\n{'='*60}")
        print(f"Divergence: DTLM v3.1 vs GDSF, M_ratio={m_ratio}")
        print(f"{'='*60}")

        t0 = time.time()
        result = run_divergence_pair(
            data,
            dtlm_config={
                "M_ratio": m_ratio,
                "warmup_days": WARMUP_DAYS,
                "policy_kwargs": dtlm_kwargs,
            },
            gdsf_config={
                "M_ratio": m_ratio,
                "warmup_days": WARMUP_DAYS,
            },
            snapshot_interval_sec=SNAPSHOT_INTERVAL_SEC,
        )
        elapsed = time.time() - t0

        out_path = OUTPUT_DIR / f"divergence_M{m_ratio}.json"
        serializable = _make_json_serializable(result)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(serializable, f, indent=2)

        s = result["summary"]
        print(f"  snapshots: {len(result['snapshots'])}")
        print(f"  mean_jaccard: {s['mean_jaccard']:.4f}")
        print(f"  divergent_ratio: {s['divergent_snapshot_ratio']:.4f}")
        print(f"  total_delta_cost: {s['total_delta_cost']:.1f}")
        print(f"  time: {elapsed:.1f}s")
        print(f"  SAVED: {out_path}")

    print(f"\nAll divergence experiments done. Results in {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
