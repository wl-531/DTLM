from policies.base import CachePolicy

EMA_ALPHA = 0.3
DEFAULT_MULTIPLIER = 2.0


class AdaptiveTTL_LRU(CachePolicy):
    """Adaptive TTL + LRU eviction."""

    def __init__(self, M, functions_info, multiplier=DEFAULT_MULTIPLIER):
        super().__init__(M, functions_info)
        self.multiplier = multiplier
        self.warm = {}
        self.ema_iat = {}
        self.last_arrival = {}
        self._mem_used = 0.0

    def _get_ttl(self, func_id):
        if func_id in self.ema_iat:
            return self.ema_iat[func_id] * self.multiplier
        return 600000

    def _update_iat(self, func_id, timestamp_ms):
        if func_id in self.last_arrival:
            iat = timestamp_ms - self.last_arrival[func_id]
            if func_id in self.ema_iat:
                self.ema_iat[func_id] = EMA_ALPHA * iat + (1 - EMA_ALPHA) * self.ema_iat[func_id]
            else:
                self.ema_iat[func_id] = iat
        self.last_arrival[func_id] = timestamp_ms

    def check_ttl(self, current_time_ms):
        expired = []
        for fid, value in self.warm.items():
            ttl = self._get_ttl(fid)
            if current_time_ms - value["last_access"] > ttl:
                expired.append(fid)
        for fid in expired:
            self.remove_from_cache(fid, "expiry", current_time_ms)

    def on_request(self, timestamp_ms, func_id):
        self._update_iat(func_id, timestamp_ms)
        if func_id in self.warm:
            self.warm[func_id]["last_access"] = timestamp_ms
            return False

        m_i = self.functions_info[func_id]["m_i"]
        while self._mem_used + m_i > self.M and self.warm:
            victim = min(self.warm, key=lambda f: self.warm[f]["last_access"])
            self.remove_from_cache(victim, "eviction", timestamp_ms)

        self.warm[func_id] = {"m_i": m_i, "last_access": timestamp_ms}
        self._mem_used += m_i
        self.mark_cache_inserted(func_id)
        return True

    def memory_used(self):
        return self._mem_used

    def get_state(self):
        return dict(self.warm)
