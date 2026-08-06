"""Point-in-Time (PIT) dataset service — immutable, fingerprint-verified snapshots.

Provides content-addressed historical bars/trades/rules/funding data
for deterministic backtesting with no future-data leakage.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.utils.db import get_db_connection
from app.utils.logger import get_logger

logger = get_logger(__name__)

PIT_DATASET_VERSION = "pit-dataset-v1"


class PITDatasetError(ValueError):
    """Raised when PIT integrity checks fail."""


class PITDatasetSnapshot:
    """Immutable data snapshot with content fingerprint."""

    def __init__(
        self,
        instrument_id: str,
        frequency: str,
        as_of: datetime,
        bars: List[Dict[str, Any]],
        fingerprint: str,
    ):
        self.instrument_id = instrument_id
        self.frequency = frequency
        self.as_of = as_of
        self.bars = bars
        self.fingerprint = fingerprint

    def to_dict(self) -> Dict[str, Any]:
        return {
            "instrument_id": self.instrument_id,
            "frequency": self.frequency,
            "as_of": self.as_of.isoformat(),
            "bar_count": len(self.bars),
            "fingerprint": self.fingerprint,
        }


class PITDatasetService:
    """Point-in-Time dataset management with integrity guarantees."""

    def __init__(self):
        self._version = PIT_DATASET_VERSION

    @staticmethod
    def compute_fingerprint(bars: List[Dict[str, Any]]) -> str:
        """SHA-256 fingerprint of ordered bar data — deterministic and collision-resistant."""
        payload = json.dumps(bars, sort_keys=True, default=str).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def validate_integrity(self, bars: List[Dict[str, Any]], expected_fingerprint: str) -> bool:
        """Verify that the bar data matches its expected fingerprint."""
        actual = self.compute_fingerprint(bars)
        if actual != expected_fingerprint:
            raise PITDatasetError(
                f"Fingerprint mismatch: expected {expected_fingerprint[:16]}..., got {actual[:16]}..."
            )
        return True

    def detect_future_data(
        self, bars: List[Dict[str, Any]], as_of: datetime
    ) -> List[int]:
        """Return indices of bars with timestamps after as_of (future-data leak)."""
        violations = []
        for i, bar in enumerate(bars):
            bar_time = bar.get("timestamp") or bar.get("time") or bar.get("open_time")
            if bar_time:
                if isinstance(bar_time, str):
                    bar_time = datetime.fromisoformat(bar_time.replace("Z", "+00:00"))
                if isinstance(bar_time, (int, float)):
                    bar_time = datetime.fromtimestamp(bar_time / 1000, tz=timezone.utc)
                if bar_time > as_of:
                    violations.append(i)
        return violations

    def detect_gaps(
        self, bars: List[Dict[str, Any]], frequency_sec: int
    ) -> List[Tuple[int, int]]:
        """Return (index, expected_gap_count) for bars where timestamp gap exceeds frequency."""
        gaps = []
        for i in range(1, len(bars)):
            t_prev = self._bar_timestamp(bars[i - 1])
            t_curr = self._bar_timestamp(bars[i])
            if t_prev is None or t_curr is None:
                continue
            diff_sec = (t_curr - t_prev).total_seconds()
            if diff_sec > frequency_sec * 1.5:
                gaps.append((i, int(diff_sec / frequency_sec)))
        return gaps

    def detect_duplicates(
        self, bars: List[Dict[str, Any]]
    ) -> List[int]:
        """Return indices of duplicate timestamps."""
        seen = set()
        dupes = []
        for i, bar in enumerate(bars):
            key = self._bar_key(bar)
            if key in seen:
                dupes.append(i)
            seen.add(key)
        return dupes

    def store_snapshot(
        self,
        instrument_id: str,
        frequency: str,
        as_of: datetime,
        bars: List[Dict[str, Any]],
    ) -> PITDatasetSnapshot:
        """Persist a verified PIT snapshot.

        Detects future data, gaps, and duplicates before storing.
        """
        # Pre-store integrity checks
        freq_map = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
        freq_sec = freq_map.get(frequency, 3600)

        future_indices = self.detect_future_data(bars, as_of)
        if future_indices:
            raise PITDatasetError(
                f"Future data detected at bar indices {future_indices[:5]} (as_of={as_of.isoformat()})"
            )

        gaps = self.detect_gaps(bars, freq_sec)
        if gaps:
            logger.warning(
                "PIT gaps detected for %s/%s: %d gap(s), first at index %d",
                instrument_id, frequency, len(gaps), gaps[0][0]
            )

        dupes = self.detect_duplicates(bars)
        if dupes:
            raise PITDatasetError(f"Duplicate bars detected at indices {dupes[:5]}")

        fingerprint = self.compute_fingerprint(bars)

        # Persist to DB
        try:
            with get_db_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    """INSERT INTO qd_pit_dataset_snapshots
                       (instrument_id, frequency, as_of, bar_count, fingerprint, bars_json, version)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (instrument_id, frequency, as_of, fingerprint) DO NOTHING""",
                    (
                        instrument_id,
                        frequency,
                        as_of,
                        len(bars),
                        fingerprint,
                        json.dumps(bars, default=str),
                        self._version,
                    ),
                )
                conn.commit()
                cur.close()
        except Exception as exc:
            logger.error("Failed to store PIT snapshot: %s", exc)
            raise

        return PITDatasetSnapshot(
            instrument_id=instrument_id,
            frequency=frequency,
            as_of=as_of,
            bars=bars,
            fingerprint=fingerprint,
        )

    @staticmethod
    def _bar_timestamp(bar: Dict[str, Any]) -> Optional[datetime]:
        """Extract datetime from a bar record."""
        ts = bar.get("timestamp") or bar.get("time") or bar.get("open_time")
        if ts is None:
            return None
        if isinstance(ts, datetime):
            return ts
        if isinstance(ts, (int, float)):
            return datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
        if isinstance(ts, str):
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return None

    @staticmethod
    def _bar_key(bar: Dict[str, Any]) -> str:
        """Deterministic key for deduplication."""
        ts = PITDatasetService._bar_timestamp(bar)
        return f"{ts.isoformat()}:{bar.get('open')}:{bar.get('close')}" if ts else str(hash(frozenset(bar.items())))
