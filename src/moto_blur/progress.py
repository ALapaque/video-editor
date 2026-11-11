"""Affichage de la progression et de la vitesse de traitement."""

from __future__ import annotations

import sys
import time


class Progress:
    def __init__(self, label: str, total_frames: int, video_fps: float):
        self.label = label
        self.total = max(1, total_frames)
        self.video_fps = video_fps
        self.done = 0
        self.t0 = time.perf_counter()
        self._last = 0.0

    def step(self, n: int = 1) -> None:
        self.done += n
        now = time.perf_counter()
        if now - self._last >= 0.5:
            self._last = now
            self._print(now)

    @property
    def fps(self) -> float:
        return self.done / max(time.perf_counter() - self.t0, 1e-9)

    def _print(self, now: float) -> None:
        fps = self.done / max(now - self.t0, 1e-9)
        remaining = (self.total - self.done) / fps if fps > 0 else 0
        m, s = divmod(int(remaining), 60)
        sys.stderr.write(
            f"\r{self.label} : {self.done}/{self.total} images "
            f"({100 * self.done / self.total:.0f} %), {fps:.1f} i/s, reste {m} min {s:02d} s   "
        )
        sys.stderr.flush()

    def close(self) -> str:
        elapsed = time.perf_counter() - self.t0
        fps = self.done / max(elapsed, 1e-9)
        sys.stderr.write("\r" + " " * 100 + "\r")
        ratio = fps / self.video_fps if self.video_fps else 0
        return (
            f"{self.label} : {self.done} images en {elapsed:.1f} s, soit {fps:.1f} i/s "
            f"({ratio:.2f} × la vitesse de lecture)"
        )
