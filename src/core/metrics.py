from __future__ import annotations
"""Lightweight in-process metrics registry.
Thread-safe counters for proposal lifecycle + analysis timing.
Not persistent; resets on process restart (sufficient for basic observability / scraping).
"""
from dataclasses import dataclass, asdict
from threading import Lock
import time
from typing import Dict, Any

@dataclass
class _MetricState:
    start_ts: float = time.time()
    proposals_generated: int = 0
    proposals_applied: int = 0
    proposals_undone: int = 0
    last_analysis_duration_ms: float = 0.0
    total_diff_bytes_applied: int = 0
    total_files_touched: int = 0
    last_apply_ts: float | None = None
    last_analysis_ts: float | None = None
    # Indexing
    last_index_build_ts: float | None = None
    index_file_count: int = 0
    index_total_bytes: int = 0

    def snapshot(self) -> Dict[str, Any]:
        d = asdict(self)
        d['uptime_s'] = time.time() - self.start_ts
        d['acceptance_rate'] = (self.proposals_applied / self.proposals_generated) if self.proposals_generated else 0.0
        return d

_state = _MetricState()
_lock = Lock()

def inc_generated(n: int = 1) -> None:
    if n <= 0:
        return
    with _lock:
        _state.proposals_generated += n

def inc_applied(diff_text: str) -> None:
    # diff_text may be multi-file; approximate file count via '+++ b/' markers
    files = diff_text.count('+++ b/') or 1 if diff_text else 0
    with _lock:
        _state.proposals_applied += 1
        _state.last_apply_ts = time.time()
        _state.total_diff_bytes_applied += len(diff_text.encode('utf-8')) if diff_text else 0
        _state.total_files_touched += files

def inc_undone() -> None:
    with _lock:
        _state.proposals_undone += 1

def record_analysis(duration_ms: float) -> None:
    with _lock:
        _state.last_analysis_duration_ms = duration_ms
        _state.last_analysis_ts = time.time()

def record_index_build(file_count: int, total_bytes: int) -> None:
    with _lock:
        _state.index_file_count = file_count
        _state.index_total_bytes = total_bytes
        _state.last_index_build_ts = time.time()


def export_metrics() -> Dict[str, Any]:
    with _lock:
        return _state.snapshot()
