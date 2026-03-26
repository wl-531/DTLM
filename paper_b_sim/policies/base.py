class CachePolicy:
    def __init__(self, M, functions_info):
        self.M = M
        self.functions_info = functions_info
        self.ever_seen = {func_id: False for func_id in functions_info}
        self.last_removal_reason = {func_id: None for func_id in functions_info}
        self.per_function_stats = {
            func_id: {
                "cold_start_cost": 0.0,
                "cold_start_count": 0,
                "request_count": 0,
            }
            for func_id in functions_info
        }
        self.deletion_log = []
        self.utilization_samples = []
        self.cold_start_log = []
        self.current_cold_start_cause = None
        self.warmup_end_ms = None
        self._last_cold_admitted = True

    def _get_cache_container(self):
        if hasattr(self, "warm_pool"):
            return self.warm_pool
        if hasattr(self, "warm"):
            return self.warm
        raise AttributeError("CachePolicy subclass must define warm or warm_pool")

    def _set_memory_used(self, new_value):
        if hasattr(self, "_mem_used"):
            self._mem_used = new_value
        if hasattr(self, "current_memory"):
            self.current_memory = new_value

    def mark_cache_inserted(self, func_id):
        self.ever_seen[func_id] = True

    def get_per_function_stats(self, func_id):
        if func_id not in self.per_function_stats:
            self.per_function_stats[func_id] = {
                "cold_start_cost": 0.0,
                "cold_start_count": 0,
                "request_count": 0,
            }
        return self.per_function_stats[func_id]

    def record_utilization_sample(self, now_ts):
        memory_utilization = self.memory_used() / self.M if self.M > 0 else 0.0
        self.utilization_samples.append({
            "timestamp": now_ts,
            "memory_utilization": memory_utilization,
        })

    def remove_from_cache(self, func_id: str, reason: str, now_ts: float):
        """All cache removals must go through this helper."""
        if reason not in {"expiry", "eviction"}:
            raise ValueError(f"Unsupported removal reason: {reason}")

        container = self._get_cache_container()
        if func_id not in container:
            return None

        current_memory_used = self.memory_used()
        memory_utilization = current_memory_used / self.M if self.M > 0 else 0.0
        entry = container[func_id]
        m_i = entry.get("m_i", 0.0)

        del container[func_id]
        self._set_memory_used(max(0.0, current_memory_used - m_i))
        self.last_removal_reason[func_id] = reason
        self.deletion_log.append({
            "func_id": func_id,
            "reason": reason,
            "timestamp": now_ts,
            "memory_utilization": memory_utilization,
        })
        return entry

    def on_request(self, timestamp_ms, func_id):
        self._last_cold_admitted = True  # 每次请求重置，防止旧状态残留
        raise NotImplementedError

    def check_ttl(self, current_time_ms):
        pass

    def get_state(self):
        raise NotImplementedError

    def memory_used(self):
        raise NotImplementedError
