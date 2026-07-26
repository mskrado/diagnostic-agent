"""Live discovery status chart for ``diag install``.

When stdout is a TTY the chart redraws in place as each probe finishes::

    Discovery in progress on local
    ------------------------------
      phase   probing prometheus
      [OK ] prometheus      http://127.0.0.1:9090
      […] loki            probing http://127.0.0.1:3100
      [ - ] mailpit         (not found)
      ...

Non-TTY callers (CI, pipes, unit tests) get a no-op sink so discover stays
silent until the existing final summary is printed.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import TextIO

from .models import ToolKind

# ANSI — supported by Windows Terminal, VS Code, and modern PowerShell hosts.
_CLEAR_LINE = "\033[2K"
_CURSOR_UP = "\033[{n}A"
_HIDE_CURSOR = "\033[?25l"
_SHOW_CURSOR = "\033[?25h"


@dataclass
class _Row:
    kind: ToolKind
    mark: str = "…"
    detail: str = "waiting"
    done: bool = False


@dataclass
class DiscoveryProgress:
    """Redrawable per-tool status chart. Construct via :func:`make_progress`."""

    target: str
    stream: TextIO = field(default_factory=lambda: sys.stdout)
    enabled: bool = True
    _phase: str = "starting"
    _rows: dict[ToolKind, _Row] = field(default_factory=dict)
    _order: list[ToolKind] = field(default_factory=list)
    _drawn_lines: int = 0
    _started: bool = False
    _finished: bool = False

    # -- public API -------------------------------------------------------
    def start(self) -> None:
        if not self.enabled or self._started:
            return
        self._started = True
        try:
            self.stream.write(_HIDE_CURSOR)
            self.stream.flush()
        except Exception:  # noqa: BLE001
            pass
        self._redraw()

    def phase(self, name: str) -> None:
        self._phase = name
        self._redraw()

    def ensure_tools(self, kinds: list[ToolKind]) -> None:
        """Seed rows so the chart shows every tool that will be probed."""
        for kind in kinds:
            if kind in self._rows:
                continue
            self._rows[kind] = _Row(kind=kind)
            self._order.append(kind)
        self._redraw()

    def found_container(self, kind: ToolKind, container_name: str) -> None:
        row = self._row(kind)
        if not row.done:
            row.detail = f"container {container_name}" if container_name else "container"
        self._redraw()

    def probing(self, kind: ToolKind, url: str) -> None:
        row = self._row(kind)
        if row.done:
            return
        row.mark = "…"
        row.detail = f"probing {url}"
        self._phase = f"probing {kind.value}"
        self._redraw()

    def result(
        self,
        kind: ToolKind,
        *,
        reachable: bool,
        url: str = "",
        version: str = "",
    ) -> None:
        row = self._row(kind)
        row.done = True
        if reachable:
            row.mark = "OK "
            detail = url or "(reachable)"
            if version:
                detail = f"{detail}  v{version}"
            row.detail = detail
        else:
            row.mark = " - "
            row.detail = "(not found)"
        self._redraw()

    def finish(self, *, placement: str = "") -> None:
        """Finalize the chart; leave it on screen as the discovery summary."""
        if not self.enabled:
            return
        # Mark anything still waiting as not found.
        for kind in self._order:
            row = self._rows[kind]
            if not row.done:
                row.mark = " - "
                row.detail = "(not found)"
                row.done = True
        self._phase = ""
        self._finished = True
        self._redraw(placement=placement)
        try:
            self.stream.write(_SHOW_CURSOR)
            self.stream.flush()
        except Exception:  # noqa: BLE001
            pass

    def close(self) -> None:
        """Restore the cursor if discovery aborted mid-flight."""
        if not self.enabled or not self._started:
            return
        try:
            self.stream.write(_SHOW_CURSOR)
            self.stream.flush()
        except Exception:  # noqa: BLE001
            pass

    # -- internals --------------------------------------------------------
    def _row(self, kind: ToolKind) -> _Row:
        if kind not in self._rows:
            self._rows[kind] = _Row(kind=kind)
            self._order.append(kind)
        return self._rows[kind]

    def _lines(self, *, placement: str = "") -> list[str]:
        found = sum(1 for r in self._rows.values() if r.mark.strip() == "OK")
        total = len(self._order)
        if self._finished:
            header = f"Discovery ({found}/{total} reachable on {self.target})"
        else:
            header = f"Discovery in progress on {self.target}"
        lines = [header, "-" * len(header)]
        if self._phase and not self._finished:
            lines.append(f"  phase   {self._phase}")
        # Reachable first once finished; during probing keep insertion order
        # so rows don't jump around under the cursor.
        if self._finished:
            ordered = sorted(
                self._order,
                key=lambda k: (self._rows[k].mark.strip() != "OK", k.value),
            )
        else:
            ordered = list(self._order)
        for kind in ordered:
            row = self._rows[kind]
            lines.append(f"  [{row.mark}] {kind.value:<14} {row.detail}")
        if placement:
            lines.append(f"  placement: {placement}")
        return lines

    def _redraw(self, *, placement: str = "") -> None:
        if not self.enabled or not self._started:
            return
        lines = self._lines(placement=placement)
        try:
            if self._drawn_lines:
                self.stream.write(_CURSOR_UP.format(n=self._drawn_lines))
            for line in lines:
                self.stream.write(f"{_CLEAR_LINE}\r{line}\n")
            # If the new frame is shorter, blank leftover rows from the previous one.
            for _ in range(max(0, self._drawn_lines - len(lines))):
                self.stream.write(f"{_CLEAR_LINE}\r\n")
            if self._drawn_lines > len(lines):
                self.stream.write(_CURSOR_UP.format(n=self._drawn_lines - len(lines)))
            self.stream.flush()
            self._drawn_lines = len(lines)
        except Exception:  # noqa: BLE001 - never let UI break discovery
            self.enabled = False


class NullDiscoveryProgress:
    """No-op progress sink for non-TTY / tests."""

    def start(self) -> None:
        return None

    def phase(self, name: str) -> None:
        return None

    def ensure_tools(self, kinds: list[ToolKind]) -> None:
        return None

    def found_container(self, kind: ToolKind, container_name: str) -> None:
        return None

    def probing(self, kind: ToolKind, url: str) -> None:
        return None

    def result(
        self,
        kind: ToolKind,
        *,
        reachable: bool,
        url: str = "",
        version: str = "",
    ) -> None:
        return None

    def finish(self, *, placement: str = "") -> None:
        return None

    def close(self) -> None:
        return None


def make_progress(
    target: str,
    *,
    enabled: bool | None = None,
    stream: TextIO | None = None,
) -> DiscoveryProgress | NullDiscoveryProgress:
    """Return a live chart when stdout is a TTY, otherwise a silent sink."""
    out = stream if stream is not None else sys.stdout
    if enabled is None:
        enabled = bool(getattr(out, "isatty", lambda: False)())
    if not enabled:
        return NullDiscoveryProgress()
    return DiscoveryProgress(target=target, stream=out, enabled=True)
