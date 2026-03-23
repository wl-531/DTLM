"""DTLM with Expiry-Informed eviction (EI-GDSF variant).

Inherits DTLM v3.1 entirely; only overrides _evict_one() to prefer
logically-expired containers when selecting eviction victims.
"""

from policies.dtlm import DTLMPolicy


class DTLMExpInf(DTLMPolicy):
    """DTLM + Expiry-Informed GDSF eviction.

    When eviction is needed:
    1. If any containers are logically expired, evict the one with lowest GDSF score among them.
    2. Otherwise, fall back to vanilla GDSF (parent behavior).
    """

    def _evict_one(self, now_ts):
        if not self.warm_pool:
            return
        expired = [fid for fid, e in self.warm_pool.items() if e["logically_expired"]]
        if expired:
            victim = min(expired, key=lambda fid: self.warm_pool[fid]["priority"])
        else:
            victim = min(self.warm_pool, key=lambda fid: self.warm_pool[fid]["priority"])
        self.clock = self.warm_pool[victim]["priority"]
        self._remove(victim, "eviction", now_ts)
        self.eviction_count += 1
