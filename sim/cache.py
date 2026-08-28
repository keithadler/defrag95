"""VCACHE: the Win95 protected-mode block cache, modelled as cluster-grained LRU.

Win95 sized VCACHE dynamically; on a 16MB machine the steady-state disk cache
was a few megabytes. Modelling it matters because it is what makes repeated
reads of SYSTEM.DAT and of directory clusters free, which in turn is what
decides how much a layout policy can still win.
"""

from __future__ import annotations

from collections import OrderedDict


class ClusterCache:
    def __init__(self, capacity_clusters: int):
        self.capacity = max(0, capacity_clusters)
        self._lru: "OrderedDict[int, bool]" = OrderedDict()
        self.hits = 0
        self.misses = 0

    def probe(self, cluster: int) -> bool:
        """Return True on a hit; either way the cluster becomes most-recent."""
        if self.capacity == 0:
            self.misses += 1
            return False
        if cluster in self._lru:
            self._lru.move_to_end(cluster)
            self.hits += 1
            return True
        self.misses += 1
        self._lru[cluster] = True
        if len(self._lru) > self.capacity:
            self._lru.popitem(last=False)
        return False

    def insert(self, cluster: int) -> None:
        if self.capacity == 0:
            return
        self._lru[cluster] = True
        self._lru.move_to_end(cluster)
        if len(self._lru) > self.capacity:
            self._lru.popitem(last=False)

    def invalidate(self, cluster: int) -> None:
        self._lru.pop(cluster, None)

    def clear(self) -> None:
        self._lru.clear()
