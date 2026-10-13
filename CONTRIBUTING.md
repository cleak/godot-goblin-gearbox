# Contributing

- Windows and Linux, PowerShell 7, Godot 4.7, GDScript. macOS is written and was never run:
  a run of `docs/platforms.md`, "Check a Mac in ten minutes", is a welcome issue. Another
  engine version: open an issue first.
- `pwsh ./tools/setup.ps1 -InstallGodot`, then `pwsh ./tools/check.ps1` green, with the kit's
  own tests in place (`tools/tests/`, `test/kit/`). Green is its last line,
  `check: PASSED: 14 passed, 0 failed, 1 skipped`, after about three and a half minutes:
  1,232 pytest cases, 126 gdUnit4 cases.
- CI runs it on Windows and Linux: a test for one system says so through `windows_only`,
  `posix_only` or `linux_only` (`tools/tests/_gate_fixtures.py`), and a branch for another
  system is tested with that system's name passed in.
- A fix starts with a failing test: pytest in `tools/tests/` for a tool, gdUnit4 in
  `test/kit/` for the addon. One test: `uv run pytest tools/tests/test_<name>.py -k <test>`.
- A test that starts a real Godot carries `@pytest.mark.real_godot`: without the mark it
  fails (`tools/tests/conftest.py`). `--dist loadgroup`, which `tools/check.ps1` passes, puts
  the marked tests on one worker, one at a time; `-n` without it spreads them.
- Fixtures: `tools/tests/_gate_fixtures.py`. `project_godot(main_scene=...)` gives the
  fixture project a game to start: without one `scenes` is skipped and `godot.ps1` refuses a
  run of the game. `clean_env()` is the environment for a program a test starts.
- No PowerShell a test starts writes your PowerShell startup profile: `conftest.py` puts
  `NO_STARTUP_PROFILE` into the environment every such program inherits, and `clean_env()`
  carries it. A test that needs a PowerShell without it gives that one a profile folder of
  its own (`STARTUP_PROFILE_VARIABLE`, set to an existing folder) and takes the variable out
  with `forget()`, not `del`: Windows keeps a name in any letter case.
- `tools/tests/test_powershell_floor.py` reads every script for a call PowerShell 7.0 lacks.
  The proof is a run of the tests with an old `pwsh` first on `PATH`.
- A new `tools/*.ps1` with parameters: `[CmdletBinding()]`, ASCII without a byte order mark,
  and an entry in `GATES` in `tools/tests/test_gate_arguments.py`.
- An error is one line that says what to do. No silent fallback, no pass on nothing checked.
- A passing test's temp folder goes when the test ends (`pyproject.toml`); the folders of
  failed tests stay under the temp folder's `pytest-of-<user>/` until a later run fails too.
- Docs change with the code, and every command a page shows was run, or the page says it
  was not. No `# comment` after a command in a `sh` or `bash` block: a zsh somebody types
  into hands it over as arguments. Fewer words.
- A bug report: the command, its output, `pwsh ./tools/doctor.ps1`, and
  `artifacts/check/<gate>.log` when a gate is involved.
