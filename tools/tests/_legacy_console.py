"""A stand-in for piped output on Windows: stdout and stderr in a legacy code page.

There `print("日本語")` raises UnicodeEncodeError unless the tool made its streams safe
(tools/ci/gate_support.py `plain_output`). Not a test module: imported by the tests.
"""

from __future__ import annotations

import io
import sys


class LegacyConsole:
    """Replaces sys.stdout and sys.stderr with strict cp1252 streams; `text()` reads back
    what was written to both."""

    def __init__(self, monkeypatch) -> None:
        self._buffers = [io.BytesIO(), io.BytesIO()]
        self._streams = [io.TextIOWrapper(raw, encoding="cp1252") for raw in self._buffers]
        monkeypatch.setattr(sys, "stdout", self._streams[0])
        monkeypatch.setattr(sys, "stderr", self._streams[1])

    def text(self) -> str:
        for stream in self._streams:
            stream.flush()
        return "".join(raw.getvalue().decode("cp1252") for raw in self._buffers)
