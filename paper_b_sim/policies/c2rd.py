from policies.base import CachePolicy

DEFAULT_ALPHA_BETA = 0.175  # ms/(MB·min), gives median T_i ≈ 10 min
DEFAULT_T_MIN_MS = 60000    # 1 min safety floor
DEFAULT_T_MAX_MS = 3600000  # 60 min safety ceiling


class C2RD_SR(CachePolicy):
    """C2RD single-node ski-rental retention baseline.

    Per-function retention time T_i = c_i / (alpha_beta * m_i) minutes.
    Container expired when idle >= T_i. Eviction fallback: min remaining retention.
    """

    def __init__(self, M, functions_info, alpha_beta=DEFAULT_ALPHA_BETA,
                 t_min_ms=DEFAULT_T_MIN_MS, t_max_ms=DEFAULT_T_MAX_MS):
        super().__init__(M, functions_info)
        self.alpha_beta = alpha_beta
        self.t_min_ms = t_min_ms
        self.t_max_ms = t_max_ms
        self.warm = {}
        self._mem_used = 0.0

        # Precompute per-function retention time in ms
        self._retention_ms = {}
        for fid, info in functions_info.items():
            m_i = info["m_i"]
            c_i = info["c_i"]
            T_minutes = c_i / (alpha_beta * m_i)
            T_ms = T_minutes * 60000
            T_ms = max(t_min_ms, min(t_max_ms, T_ms))
            self._retention_ms[fid] = T_ms

    def _retention_time_ms(self, func_id):
        return self._retention_ms.get(func_id, 600000)

    def _remaining_retention(self, func_id, current_time_ms):
        idle = current_time_ms - self.warm[func_id]["last_access"]
        return self._retention_time_ms(func_id) - idle

    def check_ttl(self, current_time_ms):
        expired = []
        for fid in self.warm:
            if self._remaining_retention(fid, current_time_ms) <= 0:
                expired.append(fid)
        for fid in expired:
            self.remove_from_cache(fid, "expiry", current_time_ms)

    def _select_victim(self, current_time_ms):
        """Min remaining retention, tie-break: min c_i/m_i, then LRU."""
        def key(fid):
            remaining = self._remaining_retention(fid, current_time_ms)
            info = self.functions_info.get(fid, {})
            value_density = info.get("c_i", 0) / max(info.get("m_i", 1), 1e-9)
            last_access = self.warm[fid]["last_access"]
            return (remaining, value_density, last_access)
        return min(self.warm, key=key)

    def on_request(self, timestamp_ms, func_id):
        if func_id in self.warm:
            self.warm[func_id]["last_access"] = timestamp_ms
            return False

        m_i = self.functions_info[func_id]["m_i"]
        while self._mem_used + m_i > self.M and self.warm:
            victim = self._select_victim(timestamp_ms)
            self.remove_from_cache(victim, "eviction", timestamp_ms)

        self.warm[func_id] = {"m_i": m_i, "last_access": timestamp_ms}
        self._mem_used += m_i
        self.mark_cache_inserted(func_id)
        return True

    def memory_used(self):
        return self._mem_used

    def get_state(self):
        return dict(self.warm)
