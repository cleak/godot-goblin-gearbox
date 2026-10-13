# Third-party software

## In the repository

| Component | Where | Version | License |
| --- | --- | --- | --- |
| [gdUnit4](https://github.com/MikeSchulze/gdUnit4) | `addons/gdUnit4/` | 6.2.2 | MIT, `addons/gdUnit4/LICENSE` |

Copied unmodified from the release archive that `kit.json` pins (`gdunit4.version`,
`gdunit4.source_archive_sha256`), so a clone runs tests with no install step.

```powershell
uv run python tools/install_gdunit4.py --verify   # compare every file with upstream
uv run python tools/install_gdunit4.py --force    # re-create the folder from the archive
```

`install.ps1` copies it into a project that has no `addons/gdUnit4`.

In a game made from the kit, the kit itself is third-party too:

| Component | Where | License |
| --- | --- | --- |
| The kit, by Caleb Leak | `tools/`, `addons/agent_devtools/` | MIT, `tools/LICENSE`, `addons/agent_devtools/LICENSE` |

## Downloaded by setup, not redistributed

| Tool | Fetched by | Version | License |
| --- | --- | --- | --- |
| [Godot Engine](https://godotengine.org) | `tools/setup.ps1 -InstallGodot` (`tools/install_godot.ps1`): the official GitHub release, SHA-512 checked against `kit.json` | 4.7.2 | MIT |
| [gdtoolkit](https://github.com/Scony/godot-gdscript-toolkit) (gdlint, gdformat) | `uv sync` | 4.5.0 | MIT |
| [Pillow](https://python-pillow.org) | `uv sync` | 11 or 12 | MIT-CMU |
| [pre-commit](https://pre-commit.com) | `uv sync` | 4 | MIT |
| [pytest](https://pytest.org) | `uv sync` | 8 | MIT |
| [pytest-xdist](https://github.com/pytest-dev/pytest-xdist) | `uv sync` | 3 | MIT |
| [Ruff](https://docs.astral.sh/ruff/) | `uv sync` | below 1 | MIT |

`pyproject.toml` lists these; `uv.lock` pins them and their dependencies.
[uv](https://docs.astral.sh/uv/) (MIT or Apache-2.0) is a prerequisite. It downloads
CPython 3.12 (PSF-2.0) when the machine has none.
