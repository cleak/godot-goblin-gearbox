# Contributing

- Windows, PowerShell 7, Godot 4.7, GDScript. Another platform or engine version: open an
  issue first.
- `pwsh ./tools/setup.ps1 -InstallGodot`, then `pwsh ./tools/check.ps1` green, with the kit's
  own tests in place (`tools/tests/`, `test/kit/`).
- A fix starts with a failing test: pytest in `tools/tests/` for a tool, gdUnit4 in
  `test/kit/` for the addon. One test: `uv run pytest tools/tests/test_<name>.py -k <test>`.
- A test that starts a real Godot carries `@pytest.mark.real_godot`: without the mark it
  fails (`tools/tests/conftest.py`). `--dist loadgroup`, which `tools/check.ps1` passes, puts
  the marked tests on one worker, one at a time; `-n` without it spreads them.
- A new `tools/*.ps1` with parameters: `[CmdletBinding()]`, ASCII without a byte order mark,
  and an entry in `GATES` in `tools/tests/test_gate_arguments.py`.
- An error is one line that says what to do. No silent fallback, no pass on nothing checked.
- Docs change with the code, and every command a page shows was run. Fewer words.
- A bug report: the command, its output, `pwsh ./tools/doctor.ps1`, and
  `artifacts/check/<gate>.log` when a gate is involved.
