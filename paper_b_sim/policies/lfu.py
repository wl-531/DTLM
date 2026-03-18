from policies.base import CachePolicy


class LFU(CachePolicy):
    def __init__(self, M, functions_info):
        super().__init__(M, functions_info)
        self.warm = {}
        self._mem_used = 0.0

    def on_request(self, timestamp_ms, func_id):
        if func_id in self.warm:
            self.warm[func_id]["access_count"] += 1
            self.warm[func_id]["last_access"] = timestamp_ms
            return False

        m_i = self.functions_info[func_id]["m_i"]
        while self._mem_used + m_i > self.M and self.warm:
            victim = min(
                self.warm,
                key=lambda f: (self.warm[f]["access_count"], self.warm[f]["last_access"]),
            )
            self.remove_from_cache(victim, "eviction", timestamp_ms)

        self.warm[func_id] = {"m_i": m_i, "access_count": 1, "last_access": timestamp_ms}
        self._mem_used += m_i
        self.mark_cache_inserted(func_id)
        return True

    def memory_used(self):
        return self._mem_used

    def get_state(self):
        return dict(self.warm)
