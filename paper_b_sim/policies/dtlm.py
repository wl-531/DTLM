from collections import deque

from policies.base import CachePolicy


class DTLMPolicy(CachePolicy):
    """DTLM: GDSF eviction with pressure-aware TTL overlay."""

    def __init__(
        self,
        M,
        functions_info,
        p_deactivate=0.90,
        hot_threshold=10,
        warm_threshold=1,
        tau_hot_ms=600000,
        tau_warm_ms=180000,
        tau_cold_ms=60000,
        t_protect_ms=60000,
        ttl_scan_interval_ms=60000,
        physical_delete_requires_pressure=False,
        **_ignored_kwargs,
    ):
        super().__init__(M, functions_info)
        self.M = M
        self.functions_info = functions_info
        self.p_deactivate = p_deactivate
        self.hot_threshold = hot_threshold
        self.warm_threshold = warm_threshold
        self.tau_hot_ms = tau_hot_ms
        self.tau_warm_ms = tau_warm_ms
        self.tau_cold_ms = tau_cold_ms
        self.t_protect_ms = t_protect_ms
        self.ttl_scan_interval_ms = ttl_scan_interval_ms
        self.physical_delete_requires_pressure = physical_delete_requires_pressure

        self.clock = 0.0
        self.warm_pool = {}
        self.warm = self.warm_pool
        self.current_memory = 0.0
        self._last_ttl_scan_ms = None

        self.ttl_reclaim_count = 0
        self.eviction_count = 0
        self.ttl_layer_active_scans = 0
        self.ttl_layer_skipped_scans = 0

    def _gdsf_priority(self, func_id):
        entry = self.warm_pool[func_id]
        return self.clock + (entry["freq"] * entry["c_i"]) / entry["m_i"]

    def _remove(self, func_id, reason, now_ts):
        self.remove_from_cache(func_id, reason, now_ts)

    def _evict_one(self, now_ts):
        if not self.warm_pool:
            return
        victim = min(self.warm_pool, key=lambda fid: self.warm_pool[fid]["priority"])
        self.clock = self.warm_pool[victim]["priority"]
        self._remove(victim, "eviction", now_ts)
        self.eviction_count += 1

    def _evict_until_fit(self, needed_mb, now_ts):
        while self.current_memory + needed_mb > self.M and self.warm_pool:
            self._evict_one(now_ts)

    def _classify_function(self, func_id, current_time):
        entry = self.warm_pool[func_id]
        one_hour_ago = current_time - 3600000
        while entry["recent_calls"] and entry["recent_calls"][0] < one_hour_ago:
            entry["recent_calls"].popleft()
        count = len(entry["recent_calls"])
        if count >= self.hot_threshold:
            return "hot"
        if count >= self.warm_threshold:
            return "warm"
        return "cold"

    def _get_tau(self, func_id, current_time):
        func_class = self._classify_function(func_id, current_time)
        if func_class == "hot":
            return self.tau_hot_ms
        if func_class == "warm":
            return self.tau_warm_ms
        return self.tau_cold_ms

    def _clear_logical_expiry(self, entry):
        entry["logically_expired"] = False
        entry["expired_at"] = None

    def _mark_logical_expiry(self, entry, current_time):
        if not entry["logically_expired"]:
            entry["logically_expired"] = True
            entry["expired_at"] = current_time

    def check_ttl(self, current_time):
        if self._last_ttl_scan_ms is not None and current_time - self._last_ttl_scan_ms < self.ttl_scan_interval_ms:
            return
        self._last_ttl_scan_ms = current_time

        pressure = self.current_memory / self.M if self.M > 0 else 0.0
        pressure_active = pressure > self.p_deactivate
        if self.physical_delete_requires_pressure:
            if pressure_active:
                self.ttl_layer_active_scans += 1
            else:
                self.ttl_layer_skipped_scans += 1
        else:
            if pressure_active:
                self.ttl_layer_skipped_scans += 1
                return
            self.ttl_layer_active_scans += 1

        for func_id in list(self.warm_pool):
            entry = self.warm_pool[func_id]
            if current_time - entry["load_time"] < self.t_protect_ms:
                continue
            idle = current_time - entry["last_access_time"]
            tau_ms = self._get_tau(func_id, current_time)
            if idle >= tau_ms:
                if self.physical_delete_requires_pressure:
                    self._mark_logical_expiry(entry, current_time)
                    if pressure_active:
                        self._remove(func_id, "expiry", current_time)
                        self.ttl_reclaim_count += 1
                else:
                    self._remove(func_id, "expiry", current_time)
                    self.ttl_reclaim_count += 1
            elif self.physical_delete_requires_pressure:
                self._clear_logical_expiry(entry)

    def on_request(self, timestamp_ms, func_id):
        if func_id in self.warm_pool:
            entry = self.warm_pool[func_id]
            self._clear_logical_expiry(entry)
            entry["last_access_time"] = timestamp_ms
            entry["freq"] += 1
            entry["recent_calls"].append(timestamp_ms)
            entry["priority"] = self._gdsf_priority(func_id)
            return False

        info = self.functions_info[func_id]
        m_i = info["m_i"]
        self._evict_until_fit(m_i, timestamp_ms)

        self.warm_pool[func_id] = {
            "m_i": m_i,
            "c_i": info["c_i"],
            "last_access_time": timestamp_ms,
            "load_time": timestamp_ms,
            "freq": 1,
            "priority": 0.0,
            "recent_calls": deque([timestamp_ms]),
            "logically_expired": False,
            "expired_at": None,
        }
        self.current_memory += m_i
        self.warm_pool[func_id]["priority"] = self._gdsf_priority(func_id)
        self.mark_cache_inserted(func_id)
        return True

    def memory_used(self):
        return self.current_memory

    def get_state(self):
        logically_expired_count = sum(1 for entry in self.warm_pool.values() if entry["logically_expired"])
        return {
            "warm_count": len(self.warm_pool),
            "memory_used": self.current_memory,
            "memory_budget": self.M,
            "pressure": self.current_memory / self.M if self.M > 0 else 0.0,
            "clock": self.clock,
            "ttl_reclaim_count": self.ttl_reclaim_count,
            "eviction_count": self.eviction_count,
            "ttl_active_scans": self.ttl_layer_active_scans,
            "ttl_skipped_scans": self.ttl_layer_skipped_scans,
            "logically_expired_count": logically_expired_count,
        }


DTLM = DTLMPolicy
