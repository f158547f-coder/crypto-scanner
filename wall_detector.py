"""TensorCharts-style wall detector.

Detects large bid/ask walls (order walls) in the orderbook.
Yellow walls = large bid orders (support)
Dark blue walls = large ask orders (resistance)

Walls are grouped into price clusters and ranked by USDT volume.
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field
from config import PRINT_DEBUG


# --- Configuration ---
WALL_MIN_MULTIPLIER = 5.0    # Wall must be 5x the median order size
WALL_MIN_USDT = 50_000       # Minimum wall size in USDT
WALL_CLUSTER_PCT = 0.001     # Group orders within 0.1% into one wall
WALL_MAX_DISTANCE_PCT = 0.03 # Only detect walls within 3% of price
WALL_HISTORY_SEC = 3600      # Track walls for 1 hour (H1 timeframe)
WALL_MIN_PERSISTENCE = 3     # Wall must appear in N consecutive snapshots


@dataclass
class Wall:
    """Represents a detected order wall."""
    price: float
    side: str              # 'bid' or 'ask'
    volume_usdt: float     # Total USDT volume at this price cluster
    volume_qty: float      # Total quantity
    first_seen: float      # Timestamp first detected
    last_seen: float       # Timestamp last confirmed
    snapshots: int = 1     # How many snapshots it appeared in
    strength: float = 0.0  # Relative strength (multiplier over median)
    touched: bool = False  # Price has reached this wall
    signalled: bool = False

    @property
    def age_sec(self) -> float:
        return self.last_seen - self.first_seen

    @property
    def is_persistent(self) -> bool:
        return self.snapshots >= WALL_MIN_PERSISTENCE

    @property
    def wall_type(self) -> str:
        """Yellow = bid wall (support), Blue = ask wall (resistance)."""
        return 'yellow' if self.side == 'bid' else 'blue'


class WallTracker:
    """Track order walls over time for a single symbol."""

    def __init__(self):
        self.walls: list[Wall] = []

    def update(self, bids: dict[float, float], asks: dict[float, float],
               current_price: float) -> list[Wall]:
        """Update wall detection from orderbook snapshot.
        Returns list of persistent walls (appeared multiple times).
        """
        now = time.time()

        # Detect walls in current snapshot
        bid_walls = self._detect_walls(bids, 'bid', current_price)
        ask_walls = self._detect_walls(asks, 'ask', current_price)
        new_walls = bid_walls + ask_walls

        # Match with existing walls and update
        matched = set()
        for nw in new_walls:
            best_match = None
            best_dist = float('inf')
            for i, ew in enumerate(self.walls):
                if ew.side != nw.side or i in matched:
                    continue
                dist = abs(ew.price - nw.price) / max(current_price, 1e-9)
                if dist < WALL_CLUSTER_PCT * 2 and dist < best_dist:
                    best_match = i
                    best_dist = dist

            if best_match is not None:
                # Update existing wall
                ew = self.walls[best_match]
                ew.volume_usdt = nw.volume_usdt
                ew.volume_qty = nw.volume_qty
                ew.last_seen = now
                ew.snapshots += 1
                ew.strength = nw.strength
                # Update price as weighted average
                ew.price = (ew.price + nw.price) / 2
                matched.add(best_match)
            else:
                # New wall
                nw.first_seen = now
                nw.last_seen = now
                self.walls.append(nw)

        # Clean up old walls
        self.walls = [
            w for w in self.walls
            if now - w.last_seen < WALL_HISTORY_SEC
            and not w.signalled
        ]

        # Check if price touched any wall
        for w in self.walls:
            dist = abs(current_price - w.price) / max(current_price, 1e-9)
            if dist < WALL_CLUSTER_PCT * 3:
                w.touched = True

        # Return persistent walls sorted by strength
        persistent = [w for w in self.walls if w.is_persistent]
        persistent.sort(key=lambda w: w.volume_usdt, reverse=True)
        return persistent

    def _detect_walls(
        self, orders: dict[float, float], side: str, current_price: float
    ) -> list[Wall]:
        """Detect walls from one side of the orderbook."""
        if not orders:
            return []

        # Filter orders within max distance
        nearby = {}
        for price, qty in orders.items():
            dist = abs(price - current_price) / max(current_price, 1e-9)
            if dist <= WALL_MAX_DISTANCE_PCT:
                nearby[price] = qty

        if len(nearby) < 10:
            return []

        # Calculate median order size in USDT
        usdt_sizes = [price * qty for price, qty in nearby.items()]
        usdt_sizes.sort()
        median_usdt = usdt_sizes[len(usdt_sizes) // 2]

        if median_usdt <= 0:
            return []

        # Find orders that are significantly larger than median
        wall_candidates = []
        for price, qty in nearby.items():
            usdt_val = price * qty
            multiplier = usdt_val / median_usdt
            if multiplier >= WALL_MIN_MULTIPLIER and usdt_val >= WALL_MIN_USDT:
                wall_candidates.append((price, qty, usdt_val, multiplier))

        # Cluster nearby wall orders
        if not wall_candidates:
            return []

        wall_candidates.sort(key=lambda x: x[0])
        clusters: list[list] = []
        current_cluster = [wall_candidates[0]]

        for i in range(1, len(wall_candidates)):
            price_diff = abs(wall_candidates[i][0] - current_cluster[-1][0])
            if price_diff / max(current_price, 1e-9) < WALL_CLUSTER_PCT:
                current_cluster.append(wall_candidates[i])
            else:
                clusters.append(current_cluster)
                current_cluster = [wall_candidates[i]]
        clusters.append(current_cluster)

        # Create Wall objects from clusters
        walls = []
        now = time.time()
        for cluster in clusters:
            total_usdt = sum(c[2] for c in cluster)
            total_qty = sum(c[1] for c in cluster)
            # Weighted average price
            avg_price = sum(c[0] * c[2] for c in cluster) / total_usdt
            max_mult = max(c[3] for c in cluster)

            walls.append(Wall(
                price=avg_price,
                side=side,
                volume_usdt=total_usdt,
                volume_qty=total_qty,
                first_seen=now,
                last_seen=now,
                strength=max_mult,
            ))

        return walls

    def get_active_walls(self, current_price: float,
                         max_distance_pct: float = 0.02) -> list[Wall]:
        """Get persistent walls near current price."""
        result = []
        for w in self.walls:
            if not w.is_persistent or w.signalled:
                continue
            dist = abs(current_price - w.price) / max(current_price, 1e-9)
            if dist <= max_distance_pct:
                result.append(w)
        result.sort(key=lambda w: abs(current_price - w.price))
        return result


# Global wall trackers per symbol
_trackers: dict[str, WallTracker] = {}


def get_tracker(symbol: str) -> WallTracker:
    """Get or create a WallTracker for a symbol."""
    if symbol not in _trackers:
        _trackers[symbol] = WallTracker()
    return _trackers[symbol]
