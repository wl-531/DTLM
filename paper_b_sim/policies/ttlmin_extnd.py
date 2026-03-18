from policies.base import CachePolicy

EMA_ALPHA = 0.3
DEFAULT_MULTIPLIER = 2.0
EXTEND_THRESHOLD = 0.8


class TTLminExtnd(CachePolicy):
    """TTLmin,extnd adapted from CDN semantics to serverless IAT-based TTLs."""

    def __init__(self, M, functions_info, multiplier=DEFAULT_MULTIPLIER, extend_threshold=EXTEND_THRESHOLD):
        super().__init__(M, functions_info)
        self.multiplier = multiplier
        self.extend_threshold = extend_threshold
        self.warm = {}
        self.ema_iat = {}
        self.last_arrival = {}
        self._mem_used = 0.0
        self.ttl_extend_count = 0
        self.ttl_expire_count = 0
        self.eviction_count = 0

    def _get_base_ttl(self, func_id):
        if func_id in self.ema_iat:
            return self.ema_iat[func_id] * self.multiplier
        return 600000

    def _remaining_ttl(self, func_id, timestamp_ms):
        return self._get_base_ttl(func_id) - (timestamp_ms - self.warm[func_id]["last_access"])

    def _update_iat(self, func_id, timestamp_ms):
        if func_id in self.last_arrival:
            iat = timestamp_ms - self.last_arrival[func_id]
            if func_id in self.ema_iat:
                self.ema_iat[func_id] = EMA_ALPHA * iat + (1 - EMA_ALPHA) * self.ema_iat[func_id]
            else:
                self.ema_iat[func_id] = iat
        self.last_arrival[func_id] = timestamp_ms

    def check_ttl(self, current_time_ms):
        pressure = self._mem_used / self.M if self.M > 0 else 0.0
        expired = []
        for fid, value in self.warm.items():
            ttl = self._get_base_ttl(fid)
            idle = current_time_ms - value["last_access"]
            if idle >= ttl:
                if pressure < self.extend_threshold:
                    value["last_access"] = current_time_ms
                    self.ttl_extend_count += 1
                else:
                    expired.append(fid)
        for fid in expired:
            self.remove_from_cache(fid, "expiry", current_time_ms)
            self.ttl_expire_count += 1

    def on_request(self, timestamp_ms, func_id):
        self._update_iat(func_id, timestamp_ms)
        if func_id in self.warm:
            self.warm[func_id]["last_access"] = timestamp_ms
            return False

        m_i = self.functions_info[func_id]["m_i"]
        while self._mem_used + m_i > self.M and self.warm:
            expired = [fid for fid in self.warm if self._remaining_ttl(fid, timestamp_ms) <= 0]
            if expired:
                victim = min(expired, key=lambda f: self.warm[f]["last_access"])
            else:
                victim = max(self.warm, key=lambda f: self._remaining_ttl(f, timestamp_ms))
            self.remove_from_cache(victim, "eviction", timestamp_ms)
            self.eviction_count += 1

        self.warm[func_id] = {"m_i": m_i, "last_access": timestamp_ms, "load_time": timestamp_ms}
        self._mem_used += m_i
        self.mark_cache_inserted(func_id)
        return True

    def memory_used(self):
        return self._mem_used

    def get_state(self):
        return dict(self.warm)
