from policies.base import CachePolicy


class GDSF(CachePolicy):
    """FaasCache: Priority = Clock + (Freq × Cost) / Size"""

    def __init__(self, M, functions_info):
        super().__init__(M, functions_info)
        self.warm = {}
        self._mem_used = 0.0
        self.clock = 0.0

    def on_request(self, timestamp_ms, func_id):
        if func_id in self.warm:
            entry = self.warm[func_id]
            entry["freq"] += 1
            entry["priority"] = self.clock + (entry["freq"] * entry["c_i"]) / entry["m_i"]
            return False

        info = self.functions_info[func_id]
        m_i = info["m_i"]
        c_i = info["c_i"]
        while self._mem_used + m_i > self.M and self.warm:
            victim = min(self.warm, key=lambda f: self.warm[f]["priority"])
            self.clock = self.warm[victim]["priority"]
            self.remove_from_cache(victim, "eviction", timestamp_ms)

        self.warm[func_id] = {
            "m_i": m_i,
            "c_i": c_i,
            "freq": 1,
            "priority": self.clock + c_i / m_i,
        }
        self._mem_used += m_i
        self.mark_cache_inserted(func_id)
        return True

    def memory_used(self):
        return self._mem_used

    def get_state(self):
        return dict(self.warm)
