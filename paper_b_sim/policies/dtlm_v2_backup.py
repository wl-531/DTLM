import math

import numpy as np

from policies.base import CachePolicy

EMA_ALPHA = 0.3
WARMUP_DURATION_MS = 2 * 24 * 60 * 60 * 1000
FALLBACK_TTL_MS = 600000
DEFAULT_K_TAU = 3.0
DEFAULT_P_HIGH = 0.85
DEFAULT_TAU_MIN_MS = 60000
DEFAULT_TAU_MAX_MS = 1800000
DEFAULT_T_PROTECT_MS = 60000
EMA_PRESSURE_ALPHA = 0.1


class DTLM(CachePolicy):
    def __init__(
        self,
        M,
        functions_info,
        k_tau=DEFAULT_K_TAU,
        p_high=DEFAULT_P_HIGH,
        tau_min_ms=DEFAULT_TAU_MIN_MS,
        tau_max_ms=DEFAULT_TAU_MAX_MS,
        t_protect_ms=DEFAULT_T_PROTECT_MS,
        quantile_multiplier=None,
        sim_start_ms=0,
    ):
        super().__init__(M, functions_info)
        self.k_tau = quantile_multiplier if quantile_multiplier is not None else k_tau
        self.quantile_multiplier = self.k_tau
        self.p_high = p_high
        self.tau_min_ms = tau_min_ms
        self.tau_max_ms = tau_max_ms
        self.t_protect_ms = t_protect_ms
        self.sim_start_ms = sim_start_ms
        self.warm_pool = {}
        self.warm = self.warm_pool
        self.ema_iat = {}
        self.last_arrival = {}
        self._mem_used = 0.0
        self.current_memory = 0.0
        self.ema_pressure = 0.0
        self.ttl_expire_count = 0
        self.eviction_count = 0
        self.ttl_skip_high_pressure_count = 0
        c_values = [info["c_i"] for info in functions_info.values()]
        self.median_c = float(np.median(c_values)) if c_values else 1.0

    def _update_iat(self, func_id, timestamp_ms):
        if func_id in self.last_arrival:
            iat = timestamp_ms - self.last_arrival[func_id]
            if func_id in self.ema_iat:
                self.ema_iat[func_id] = EMA_ALPHA * iat + (1 - EMA_ALPHA) * self.ema_iat[func_id]
            else:
                self.ema_iat[func_id] = iat
        self.last_arrival[func_id] = timestamp_ms

    def _clamp(self, value, lo, hi):
        return max(lo, min(hi, value))

    def _tau_base(self, func_id):
        if func_id not in self.ema_iat:
            return FALLBACK_TTL_MS
        return self._clamp(self.k_tau * self.ema_iat[func_id], self.tau_min_ms, self.tau_max_ms)

    def _update_ema_pressure(self):
        instant = self.current_memory / self.M if self.M > 0 else 1.0
        self.ema_pressure = (1.0 - EMA_PRESSURE_ALPHA) * self.ema_pressure + EMA_PRESSURE_ALPHA * instant
        return self.ema_pressure

    def _remove(self, func_id, reason, now_ts):
        self.remove_from_cache(func_id, reason, now_ts)

    def check_ttl(self, current_time_ms):
        ema_pressure = self._update_ema_pressure()
        for func_id in list(self.warm_pool):
            entry = self.warm_pool[func_id]
            idle = current_time_ms - entry["last_access_time"]
            if current_time_ms - entry["load_time"] < self.t_protect_ms:
                continue
            if idle >= entry["tau_base"]:
                if ema_pressure < self.p_high:
                    self._remove(func_id, "expiry", current_time_ms)
                    self.ttl_expire_count += 1
                else:
                    self.ttl_skip_high_pressure_count += 1

    def _eviction_score(self, func_id, current_time_ms):
        entry = self.warm_pool[func_id]
        idle_s = (current_time_ms - entry["last_access_time"]) / 1000.0
        tau_s = max(entry["tau_base"] / 1000.0, 1e-6)
        reuse_prob = math.exp(-idle_s / tau_s)
        normalized_cost = entry["c_i"] / self.median_c if self.median_c > 0 else entry["c_i"]
        benefit = 0.7 * reuse_prob + 0.3 * normalized_cost
        return (benefit / entry["m_i"], -idle_s)

    def on_request(self, timestamp_ms, func_id):
        self._update_iat(func_id, timestamp_ms)
        tau_base = self._tau_base(func_id)
        if func_id in self.warm_pool:
            entry = self.warm_pool[func_id]
            entry["last_access_time"] = timestamp_ms
            entry["tau_base"] = tau_base
            return False

        m_i = self.functions_info[func_id]["m_i"]
        c_i = self.functions_info[func_id]["c_i"]
        while self._mem_used + m_i > self.M and self.warm_pool:
            victim = min(self.warm_pool, key=lambda fid: self._eviction_score(fid, timestamp_ms))
            self._remove(victim, "eviction", timestamp_ms)
            self.eviction_count += 1

        self.warm_pool[func_id] = {
            "m_i": m_i,
            "c_i": c_i,
            "last_access_time": timestamp_ms,
            "load_time": timestamp_ms,
            "tau_base": tau_base,
        }
        self._mem_used += m_i
        self.current_memory = self._mem_used
        self.mark_cache_inserted(func_id)
        return True

    def memory_used(self):
        return self._mem_used

    def get_state(self):
        return dict(self.warm_pool)
