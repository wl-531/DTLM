from policies.base import CachePolicy

FIXED_TTL_MS = 600000


class FixedTTL_LRU(CachePolicy):
    def __init__(self, M, functions_info):
        super().__init__(M, functions_info)
        self.warm = {}
        self._mem_used = 0.0
        self.ttl_expire_count = 0

    def check_ttl(self, current_time_ms):
        expired = [
            fid
            for fid, value in self.warm.items()
            if current_time_ms - value["last_access"] >= FIXED_TTL_MS
        ]
        for fid in expired:
            self.remove_from_cache(fid, "expiry", current_time_ms)
            self.ttl_expire_count += 1

    def on_request(self, timestamp_ms, func_id):
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
