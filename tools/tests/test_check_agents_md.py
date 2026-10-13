"""tools/check_agents_md.py: a clean page passes, one seeded defect per rule fails, and the
agent files the kit ships hold what the tools and the docs say about them.

The last part reads the project's own AGENTS.md, CLAUDE.md and docs. An owner edits those:
every test there holds for any project that follows docs/customizing-agents-md.md, and
skips, with the reason, where a file or the sample game is gone."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1]
REPO = TOOLS.parent
SCRIPT = TOOLS / "check_agents_md.py"
sys.path.insert(0, str(TOOLS))
import check_agents_md as chk  # noqa: E402
import devtools  # noqa: E402

FENCE = "```"
CLEAN = f"""# AGENTS.md

Rules in `scripts/core/`, config in `kit.json`. Guide: [tools](docs/tools.md).
Never weaken a gate: no `@warning_ignore("<name>")` without a reason.

{FENCE}powershell
pwsh ./tools/check.ps1 -Changed   # every gate
uv run python tools/devtools.py screenshot --filename <feature>_<state>.png
pwsh ./tools/godot.ps1 --headless --script res://tools/lint_project.gd --user-args --fix
$env:GODOT_AGENT_SESSION = "<unique>"
{FENCE}

- Output: `artifacts/check/<gate>.log`, `.godot/`, `user://devtools_<session>/`.
- Not paths: `press/tap`, `/root/Main`, `C:\\path\\to\\game`, `https://example.com/docs/x.md`.
- Shorthand: `check.ps1`, `lint_project.gd`, `test_*.gd`, `position:x`, `1/3`.
- Endings and dot-files: every `.gd` and `.tscn`; `.mcp.json`; `artifacts/shots/menu.json`.
- Mail: someone@example.com. A line of a file: `res://scripts/core/board.gd:12`.

| When | Open |
|---|---|
| A gate fails | `docs/tools.md` |
"""
FILES = (
    "kit.json",
    "tools/check.ps1",
    "tools/godot.ps1",
    "tools/devtools.py",
    "tools/lint_project.gd",
    "docs/tools.md",
    "scripts/core/board.gd",
)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    for name in FILES:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    return tmp_path


def run(project: Path, text: str | bytes | None = CLEAN, *args: str) -> tuple[int, str, str]:
    """The exit code, stdout and stderr of the real command line on `text` as AGENTS.md."""
    if text is not None:
        data = text if isinstance(text, bytes) else text.encode("utf-8")
        (project / "AGENTS.md").write_bytes(data)
    done = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(project), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    return done.returncode, done.stdout, done.stderr


def findings(project: Path, text: str, *args: str) -> list[str]:
    code, out, _err = run(project, text, *args)
    lines = out.splitlines()
    assert code == 1, out
    assert lines[-1] == f"agents-md: FAILED: {len(lines) - 1} finding(s) in AGENTS.md"
    return lines[:-1]


def test_a_clean_page_passes_with_its_size_on_the_last_line(project: Path) -> None:
    code, out, err = run(project)
    assert (code, err) == (0, ""), out + err
    size = len(CLEAN.encode("utf-8"))
    assert out.splitlines() == [
        f"agents-md: ok (AGENTS.md: {len(CLEAN.splitlines())} lines, {size:,} bytes)"
    ]


def test_too_many_lines_fail_and_the_flag_moves_the_budget(project: Path) -> None:
    assert chk.MAX_LINES == 200
    long = CLEAN + "- one more rule\n" * chk.MAX_LINES
    count = len(long.splitlines())
    assert findings(project, long, "--max-bytes", "0") == [
        f"AGENTS.md: {count} lines; the budget is 200 (--max-lines)"
    ]
    assert run(project, long, "--max-bytes", "0", "--max-lines", str(count))[0] == 0
    assert run(project, long, "--max-bytes", "0", "--max-lines", "0")[0] == 0
    assert run(project, CLEAN, "--max-lines", "5")[0] == 1


def test_too_many_bytes_fail_and_the_flag_moves_the_budget(project: Path) -> None:
    heavy = CLEAN + ("- " + "x" * 90 + "\n") * 150
    size = len(heavy.encode("utf-8"))
    assert size > chk.MAX_BYTES == 12288
    assert len(heavy.splitlines()) <= chk.MAX_LINES
    assert findings(project, heavy) == [
        f"AGENTS.md: {size:,} bytes; the budget is 12,288 (--max-bytes)"
    ]
    assert run(project, heavy, "--max-bytes", str(size))[0] == 0
    assert run(project, CLEAN, "--max-bytes", "100")[0] == 1


def test_a_non_ascii_character_fails_with_its_place(project: Path) -> None:
    text = CLEAN + "- Fewer words \u2014 show a number.\n"
    line = len(text.splitlines())
    assert findings(project, text) == [f"AGENTS.md:{line}: non-ASCII character U+2014 at column 15"]


def test_a_byte_order_mark_fails(project: Path) -> None:
    assert findings(project, "\ufeff" + CLEAN) == [
        "AGENTS.md:1: starts with a byte order mark: save it as UTF-8 without one"
    ]


def test_text_that_is_not_utf8_fails_without_a_traceback(project: Path) -> None:
    code, out, err = run(project, CLEAN.encode("utf-8") + b"caf\xe9\n")
    assert code == 1 and err == ""
    assert "AGENTS.md: is not UTF-8 text" in out


def test_a_long_line_fails_and_the_flag_moves_the_limit(project: Path) -> None:
    text = CLEAN + "- " + "word " * 25 + "\n"
    line = len(text.splitlines())
    assert findings(project, text) == [f"AGENTS.md:{line}: 127 columns; the limit is 100"]
    assert run(project, text, "--max-columns", "127")[0] == 0
    assert run(project, text, "--max-columns", "0")[0] == 0


def test_an_import_outside_code_fails_and_inside_code_passes(project: Path) -> None:
    text = CLEAN + "See @docs/tools.md for more.\n@AGENTS.md\n"
    last = len(text.splitlines())
    found = findings(project, text)
    assert [entry.split(":")[1] for entry in found] == [str(last - 1), str(last)]
    assert "@docs/tools.md outside a code span is an import" in found[0]
    assert "@AGENTS.md outside a code span is an import" in found[1]
    inside = f"`@AGENTS.md` and `@export`\n\n{FENCE}gdscript\n@export var x: int = 0\n{FENCE}\n"
    assert run(project, CLEAN + inside)[0] == 0


@pytest.mark.parametrize(
    ("line", "problem"),
    [
        ("Run `tools/gone.ps1` first.", "`tools/gone.ps1` does not exist in the project"),
        (
            "Read `docs/agents/runtime.md`.",
            "`docs/agents/runtime.md` does not exist in the project",
        ),
        ("Suites: `test/unit/`.", "`test/unit/` does not exist in the project"),
        ("Rules: `scripts/sim/`.", "`scripts/sim/` does not exist in the project"),
        (
            "Addon: `addons/agent_devtools/`.",
            "`addons/agent_devtools/` does not exist in the project",
        ),
        ("Windows: `tools\\gone.ps1`.", "`tools/gone.ps1` does not exist in the project"),
        ('`preload("res://tools/gone.gd")`', "`tools/gone.gd` does not exist in the project"),
    ],
)
def test_a_project_path_that_does_not_exist_fails(project: Path, line: str, problem: str) -> None:
    text = CLEAN + line + "\n"
    assert findings(project, text) == [f"AGENTS.md:{len(text.splitlines())}: {problem}"]


def test_a_path_that_is_there_in_other_letters_is_named_as_the_project_spells_it(
    project: Path,
) -> None:
    """Windows and macOS open `Docs/tools.md` for `docs/tools.md`; Linux and git do not.
    After install.ps1 put the kit into a project's own `Docs/`, "does not exist" sent the
    reader looking for a file that was there."""
    lines = (
        "Wrong case: `tools/Check.ps1`, `Tools/check.ps1`, `./DOCS/`.\n"
        "Ship `Scripts/core/<rule>.gd`. Read [the tools](Docs/tools.md).\n"
    )
    where = len(CLEAN.splitlines()) + 1
    rename = "in the project: make the two spellings match (upper and lower case count)"
    assert findings(project, CLEAN + lines) == [
        f"AGENTS.md:{where}: `tools/Check.ps1` is spelled `tools/check.ps1` {rename}",
        f"AGENTS.md:{where}: `Tools/check.ps1` is spelled `tools/check.ps1` {rename}",
        f"AGENTS.md:{where}: `DOCS/` is spelled `docs/` {rename}",
        f"AGENTS.md:{where + 1}: `Scripts/core/` (in front of a placeholder) is spelled "
        f"`scripts/core/` {rename}",
        f"AGENTS.md:{where + 1}: link to `Docs/tools.md`, which is spelled `docs/tools.md` on "
        "disk (upper and lower case count)",
    ]
    assert chk.other_spelling(project, "tools/check.ps1") == ""
    assert chk.other_spelling(project, "tools/gone.ps1") == ""


@pytest.mark.parametrize(
    ("line", "problems"),
    [
        # No `src` folder at all: the folder a page names may be the very thing that is gone.
        ("Rules: `src/sim/`.", ["`src/sim/` does not exist in the project"]),
        ("Entry: `src/player.gd`.", ["`src/player.gd` does not exist in the project"]),
        ("`res://levels/intro.tscn`", ["`levels/intro.tscn` does not exist in the project"]),
        (
            "`pwsh ./bin/build.ps1 -Out dist/`",
            [
                "`bin/build.ps1` does not exist in the project",
                "`dist/` does not exist in the project",
            ],
        ),
    ],
)
def test_a_path_under_a_folder_that_does_not_exist_fails(
    project: Path, line: str, problems: list[str]
) -> None:
    text = CLEAN + line + "\n"
    number = len(text.splitlines())
    assert findings(project, text) == [f"AGENTS.md:{number}: {problem}" for problem in problems]


def test_the_folders_in_front_of_a_placeholder_must_exist(project: Path) -> None:
    """`test/sequences/<name>.json` tells an agent where its file goes. With tests.root
    moved and test/ deleted, the line would send every new sequence to a folder that
    is not there."""
    lines = (
        "Ship `test/sequences/<name>.json` and `res://test/unit/test_<name>.gd`.\n"
        "Also `docs/<page>.md`, `tools/new/*.py`, `press/<key>`, `<tests>/unit/test_<name>.gd`,\n"
        "`artifacts/check/<gate>.log`, `user://shots/<session>/`, `scripts/core/<rule>.gd`.\n"
    )
    where = f"AGENTS.md:{len(CLEAN.splitlines()) + 1}"
    missing = "(in front of a placeholder) does not exist in the project"
    assert findings(project, CLEAN + lines) == [
        f"{where}: `test/sequences/` {missing}",
        f"{where}: `test/unit/` {missing}",
    ]
    (project / "test" / "sequences").mkdir(parents=True)
    (project / "test" / "unit").mkdir()
    assert run(project, CLEAN + lines)[0] == 0


def test_a_word_with_a_slash_that_is_no_path_passes(project: Path) -> None:
    text = CLEAN + "`Hud/Status`, `move_left/right`, `and/or`, `logs/godot.log`, `a / float(b)`,\n"
    text += "`devtools/allow_mutation`, `../other/notes.md`, `1/60`.\n"
    assert run(project, text)[0] == 0


def test_the_kit_s_root_dot_files_must_be_there(project: Path) -> None:
    text = CLEAN + "Style: `.gdlintrc`. Hooks: `.pre-commit-config.yaml`.\n"
    number = len(text.splitlines())
    assert findings(project, text) == [
        f"AGENTS.md:{number}: `.gdlintrc` is not at the project root",
        f"AGENTS.md:{number}: `.pre-commit-config.yaml` is not at the project root",
    ]
    for name in (".gdlintrc", ".pre-commit-config.yaml"):
        (project / name).write_text("", encoding="utf-8")
    assert run(project, text)[0] == 0
    # An ending, a property, a file that sits in folders: not root files, never findings.
    other = CLEAN + "`.gd`, `.uid`, `.import`, `.json`, `.position`, `.gdignore`, `.new()`\n"
    assert run(project, other)[0] == 0


def test_what_gitignore_lists_is_right_to_be_absent(project: Path) -> None:
    text = CLEAN + "Builds land in `build/` and `/dist/web/`; keys in `.env`; `saves/slot.json`.\n"
    number = len(text.splitlines())
    assert findings(project, text) == [
        f"AGENTS.md:{number}: `build/` does not exist in the project",
        f"AGENTS.md:{number}: `saves/slot.json` does not exist in the project",
    ]
    # Nested patterns and a bare `*` are not top-level names: they hide nothing.
    (project / ".gitignore").write_text("*\n/*\ndocs/build/\n", encoding="utf-8")
    assert len(findings(project, text)) == 2
    ignore = "# output\n/build/\nsaves/*\n!saves/keep.json\n\n*.log\n"
    (project / ".gitignore").write_text(ignore, encoding="utf-8")
    assert run(project, text)[0] == 0


def test_a_file_a_kit_tool_writes_later_passes_without_a_flag(project: Path) -> None:
    text = CLEAN + "`AGENTS.kit.md`, `tools/kit_manifest.json`, `test_timings.json`,\n"
    text += "`tools/ci/architecture_allowlist.txt`, `tools/ci/test_timings.json`.\n"
    assert run(project, text)[0] == 0


SAMPLE = """## Layout (sample)

| `src/sim/`, `main.tscn` | Rules |

### Notes

`pwsh ./tools/gone.ps1`
"""


def test_paths_under_a_sample_heading_are_counted_not_failed(project: Path) -> None:
    code, out, _err = run(project, CLEAN + SAMPLE)
    size = len((CLEAN + SAMPLE).encode("utf-8"))
    count = len((CLEAN + SAMPLE).splitlines())
    assert (code, out.splitlines()) == (
        0,
        [
            f"agents-md: ok (AGENTS.md: {count} lines, {size:,} bytes; "
            "3 missing path(s) under (sample) headings left out)"
        ],
    )


def test_a_sample_section_ends_at_the_next_heading_of_its_level(project: Path) -> None:
    text = CLEAN + SAMPLE + "\n## Architecture (default)\n\nRules: `src/sim/`.\n"
    assert findings(project, text) == [
        f"AGENTS.md:{len(text.splitlines())}: `src/sim/` does not exist in the project"
    ]


def test_only_paths_are_left_out_under_a_sample_heading(project: Path) -> None:
    text = CLEAN + SAMPLE + "See @docs/tools.md and [gone](docs/gone.md).\n"
    found = findings(project, text)
    assert len(found) == 2
    assert "is an import" in found[0] and "link to `docs/gone.md`" in found[1]
    # The mark is a contract with doctor.ps1: another spelling is no sample section.
    assert run(project, CLEAN + SAMPLE.replace("(sample)", "(Sample)"))[0] == 1


def test_a_code_span_that_wraps_is_read_on_both_lines(project: Path) -> None:
    text = CLEAN + "Run `pwsh ./tools/check.ps1 -Changed, then\n@export ./tools/gone.ps1` and\n"
    text += "stop. A stray ` here.\n\n@AGENTS.md\n"
    count = len(text.splitlines())
    found = findings(project, text)
    assert found == [
        f"AGENTS.md:{count - 3}: `tools/gone.ps1` does not exist in the project",
        # The blank line closed the stray span: this is prose again, and an import.
        f"AGENTS.md:{count}: @AGENTS.md outside a code span is an import: Claude Code "
        "loads that file on every turn. Write a plain path in backticks",
    ]


def test_a_command_whose_script_is_gone_fails(project: Path) -> None:
    block = (
        f"{FENCE}powershell\n"
        "pwsh ./tools/setup_input_actions_cli.ps1 -Fix\n"
        "uv run python tools/gone.py --all\n"
        "pwsh ./tools/godot.ps1 --headless --script res://tools/gone.gd --user-args --fix\n"
        f"{FENCE}\n"
    )
    start = len(CLEAN.splitlines()) + 1
    assert findings(project, CLEAN + block) == [
        f"AGENTS.md:{start + 1}: `tools/setup_input_actions_cli.ps1` does not exist in the project",
        f"AGENTS.md:{start + 2}: `tools/gone.py` does not exist in the project",
        f"AGENTS.md:{start + 3}: `tools/gone.gd` does not exist in the project",
    ]


def test_a_file_name_that_is_nowhere_in_the_project_fails(project: Path) -> None:
    text = CLEAN + "Read `GONE.md`, run `gone.ps1`, edit `kit.json` and `board.gd`.\n"
    line = len(text.splitlines())
    assert findings(project, text) == [
        f"AGENTS.md:{line}: `GONE.md` is no file in the project "
        "(a placeholder goes in angle brackets, like `<name>.md`)",
        f"AGENTS.md:{line}: `gone.ps1` is no file in the project "
        "(a placeholder goes in angle brackets, like `<name>.ps1`)",
    ]


def test_a_link_to_a_missing_file_fails_relative_to_the_page(project: Path) -> None:
    text = (
        CLEAN + "[gone](docs/gone.md) [ok](docs/tools.md#gates) [web](https://example.com/x.md)\n"
    )
    assert findings(project, text) == [
        f"AGENTS.md:{len(text.splitlines())}: link to `docs/gone.md`, which does not exist "
        "(links are relative to this page)"
    ]
    page = project / "docs" / "tools.md"
    page.write_text("[up](../kit.json) [beside](tools.md) [lost](devtools.md)\n", encoding="utf-8")
    code, out, _err = run(project, None, str(page), "--max-lines", "0")
    assert code == 1
    assert out.splitlines()[0] == (
        "docs/tools.md:1: link to `devtools.md`, which does not exist "
        "(links are relative to this page)"
    )


def test_allow_passes_a_path_that_is_right_to_be_absent(project: Path) -> None:
    text = CLEAN + "Later: `tools/add_input_action.gd`, `LATER.md`, [x](docs/later.md).\n"
    assert len(findings(project, text)) == 3
    allow = ["--allow", "tools/add_*", "--allow", "LATER.md", "--allow", "docs/later.md"]
    assert run(project, text, *allow)[0] == 0


def test_every_problem_of_a_page_is_listed(project: Path) -> None:
    text = "\ufeff" + CLEAN + "@docs/tools.md and `tools/gone.ps1` \u2014 " + "x" * 80 + "\n"
    code, out, _err = run(project, text, "--max-lines", "3")
    assert code == 1
    kinds = ["byte order mark", "lines; the budget", "non-ASCII", "columns; the limit"]
    kinds += ["does not exist in the project", "is an import"]
    for kind in kinds:
        assert kind in out, kind
    assert out.splitlines()[-1] == "agents-md: FAILED: 6 finding(s) in AGENTS.md"


def test_usage_errors_exit_2_with_one_line(project: Path) -> None:
    code, out, err = run(project, None)
    assert (code, out) == (2, "")
    assert err.startswith("check_agents_md: cannot read ") and len(err.splitlines()) == 1
    assert run(project, CLEAN, "--bogus")[0] == 2
    assert run(project, CLEAN, "--max-lines", "-1")[0] == 2
    assert run(project, CLEAN, "--max-lines", "many")[0] == 2
    code, _out, err = run(project, CLEAN, "--root", str(project / "nowhere"))
    assert code == 2 and "--root is not a folder" in err


def test_the_default_page_is_agents_md_under_the_root(project: Path) -> None:
    (project / "OTHER.md").write_text("`tools/gone.ps1`\n", encoding="utf-8")
    assert run(project, CLEAN)[0] == 0
    code, out, _err = run(project, None, str(project / "OTHER.md"))
    assert code == 1 and out.startswith("OTHER.md:1: `tools/gone.ps1`")


# --- The agent files the kit ships ---------------------------------------------------

# The Kit part's share of the budget: the rest (100 lines, 6 KB) is the owner's.
KIT_PART_MAX_LINES = 100
KIT_PART_MAX_BYTES = 6144
KIT_HEADINGS = [
    "## Every change",
    "## Commands",
    "## Runtime loop",
    "## GDScript",
    "## Tests",
    "## Shared machine",
    "## Safety",
    "## Reference",
]
# Right to be missing from the tree when a page other than AGENTS.md names it.
DOC_ALLOW = [
    "src/*",  # the customizing guide's example of an owner's layout
    ".gemini/settings.json",  # another agent's own settings
    ".aider.conf.yml",
    "report.json",  # written by tools/visual_diff/compare.py into its --output-dir
    "Godot_v*",  # a Godot release's folder under .tools/godot/
    # The pages' examples of a name that differs from the kit's in letter case only.
    "Tools/*",
    "Docs/*",
    "Test/*",
    "Tests/*",
    "agents.md",
    "[aA].gd",
    "*.kit-new",  # install.ps1's temporary name for a file it is writing
    "override.cfg",  # Godot's own file beside project.godot: it too may name a main scene
]
DOC_PAGES = [
    "README.md",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "THIRD_PARTY.md",
    "CLAUDE.md",
    ".github/copilot-instructions.md",
    "docs/architecture.md",
    "docs/assets.md",
    "docs/critique.md",
    "docs/customizing-agents-md.md",
    "docs/devtools.md",
    "docs/feel.md",
    "docs/gotchas.md",
    "docs/platforms.md",
    "docs/provenance.md",
    "docs/tools.md",
    "addons/agent_devtools/README.md",
    "tools/visual_diff/README.md",
]
SAMPLE_GAME = (
    "main.tscn",
    "scripts/core/board.gd",
    "scripts/game/game.gd",
    "scripts/game/dev_commands.gd",
    "test/unit/test_board.gd",
    "test/unit/test_game.gd",
    "test/sequences/smoke.json",
)


def shipped(name: str) -> str:
    """The text of one of the project's own pages; the test is skipped when it is gone."""
    path = REPO / name
    if not path.is_file():
        pytest.skip(f"{name} is not in this project")
    return path.read_text(encoding="utf-8")


def has_sample_game() -> bool:
    return all((REPO / name).is_file() for name in SAMPLE_GAME)


def names_the_sample_game(finding: str) -> bool:
    """True for a finding about a file or folder that only the sample game brings."""
    named = re.search(r"`([^`]+)`", finding)
    if named is None:
        return False
    path = named.group(1).rstrip("/")
    return any(
        path in (file, file.rsplit("/", 1)[-1]) or file.startswith(path + "/")
        for file in SAMPLE_GAME
    )


def kit_part(text: str) -> str:
    """AGENTS.md from `# Kit` on: the part that ships with the kit and stays as shipped."""
    lines = text.splitlines()
    if "# Kit" not in lines:
        pytest.skip("AGENTS.md has no `# Kit` part")
    return "\n".join(lines[lines.index("# Kit") :]) + "\n"


@pytest.fixture
def kit_only_project(tmp_path: Path) -> Path:
    """What install.ps1 leaves in a game: the kit's tools and docs, none of the sample game."""
    for folder in ("tools", "docs"):
        for path in (REPO / folder).rglob("*"):
            relative = path.relative_to(REPO)
            if path.is_file() and not {"tests", "__pycache__"} & set(relative.parts):
                (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
                (tmp_path / relative).write_bytes(b"")
    for name in ("kit.json", "project.godot", "pyproject.toml", ".gdlintrc", ".gitignore"):
        (tmp_path / name).write_bytes(b"")
    for folder in ("addons/agent_devtools", "addons/gdUnit4"):
        (tmp_path / folder).mkdir(parents=True)
    return tmp_path


def test_the_project_s_agents_md_passes_its_own_lint() -> None:
    shipped("AGENTS.md")
    done = subprocess.run(
        [sys.executable, str(SCRIPT)], capture_output=True, text=True, encoding="utf-8", check=False
    )
    assert done.returncode == 0, done.stdout + done.stderr
    if has_sample_game():
        # (sample) means "describes the sample game", and that game is in this tree.
        assert "left out" not in done.stdout, (
            "a (sample) section of AGENTS.md names a path the project does not have: "
            "fix the path, or delete the mark once the section is yours\n" + done.stdout
        )


def test_the_kit_part_names_nothing_of_the_sample_game(kit_only_project: Path) -> None:
    """install.ps1 seeds it into games that never had the sample, and it stays as shipped
    after the sample is replaced: a sample file named there is a red gate in both."""
    _code, out, err = run(kit_only_project, kit_part(shipped("AGENTS.md")))
    assert err == ""
    assert [line for line in out.splitlines() if names_the_sample_game(line)] == []
    assert "left out" not in out, "the Kit part has no (sample) section"


def test_outside_its_sample_sections_agents_md_names_nothing_of_the_sample_game(
    kit_only_project: Path,
) -> None:
    """The whole file as install.ps1 seeds it: only (sample) sections may name the sample."""
    text = shipped("AGENTS.md")
    if len(re.findall(r"^## .+\(sample\)$", text, re.MULTILINE)) < 3:
        pytest.skip("a (sample) mark is gone: those sections are the owner's now")
    _code, out, _err = run(kit_only_project, text)
    assert [line for line in out.splitlines() if names_the_sample_game(line)] == []


def test_the_kit_part_keeps_its_outline_and_its_share_of_the_budget() -> None:
    part = kit_part(shipped("AGENTS.md"))
    headings = re.findall(r"^#{2} .+$", part, re.MULTILINE)
    assert [heading.split(" (")[0] for heading in headings] == KIT_HEADINGS
    lines, size = len(part.splitlines()), len(part.encode("utf-8"))
    assert lines <= KIT_PART_MAX_LINES, f"the Kit part is {lines} lines"
    assert size <= KIT_PART_MAX_BYTES, f"the Kit part is {size} bytes"
    assert KIT_PART_MAX_LINES * 2 <= chk.MAX_LINES and KIT_PART_MAX_BYTES * 2 <= chk.MAX_BYTES


def test_a_mark_is_spelled_the_way_doctor_reads_it() -> None:
    """doctor.ps1 warns while the literal "(sample)" remains. The owner deletes the marks one
    by one, so none is required here: a mark that is there is spelled right and explained."""
    text = shipped("AGENTS.md")
    headings = re.findall(r"^#{1,6} .+$", text, re.MULTILINE)
    marks = [mark for heading in headings for mark in re.findall(r"\([^)]*\)$", heading.strip())]
    lookalike = re.compile(r"\(\s*(sample|default|example|template)s?\s*\)", re.IGNORECASE)
    exact = (chk.SAMPLE_MARK, "(default)")
    wrong = [mark for mark in marks if lookalike.fullmatch(mark) and mark not in exact]
    assert wrong == [], f"write (sample) or (default) exactly: {wrong}"
    if chk.SAMPLE_MARK in marks:
        prose = "\n".join(line for line in text.splitlines() if not line.startswith("#"))
        assert "`(sample)`" in prose, (
            "a (sample) heading remains, but the line that tells agents what the mark "
            "means is gone: keep it until the last mark is deleted"
        )


def test_claude_md_imports_agents_md() -> None:
    assert shipped("CLAUDE.md").splitlines()[0] == "@AGENTS.md"


def test_the_copilot_file_points_at_agents_md() -> None:
    copilot = shipped(".github/copilot-instructions.md")
    assert "`AGENTS.md`" in copilot and len(copilot.splitlines()) <= 5


@pytest.mark.parametrize("name", DOC_PAGES)
def test_a_page_of_the_kit_names_real_paths_and_links(name: str) -> None:
    text = shipped(name)
    if not has_sample_game():
        pytest.skip("the sample game is gone, and the kit's pages name it as their example")
    for optional in ("CLAUDE.md", ".claude/settings.json", ".github/copilot-instructions.md"):
        if not (REPO / optional).is_file():
            pytest.skip(f"{optional} is gone, and the kit's pages name it")
    if name == "README.md" and "pwsh ./install.ps1 -Target" not in text:
        pytest.skip("README.md is the game's own now, not the kit's")
    if name == "CLAUDE.md":
        text = text.split("\n", 1)[1]  # its first line is the one import that is wanted
    project = chk.Project(REPO, DOC_ALLOW)
    found, _left_out = chk.findings_of(text.encode("utf-8"), REPO / name, project, 0, 0, 0)
    assert found == [], "\n".join(f"{name}:{number}: {problem}" for number, problem in found)


SHELL_BLOCK = re.compile(r"^```(sh|bash|zsh|shell)\n(.*?)^```$", re.MULTILINE | re.DOTALL)
SHELL_COMMENT = re.compile(r"(^|\s)#(\s|$)")


@pytest.mark.parametrize("name", DOC_PAGES)
def test_no_command_a_page_gives_to_paste_carries_a_comment(name: str) -> None:
    """A zsh somebody types into (the Mac's own shell) does not take `# ...` for a comment:
    after a command the words arrive as arguments, and `pwsh ./tools/setup.ps1 # ...` fails
    on `#`. bash and pwsh do. The words go in the sentence beside the block. A `powershell`
    block is typed into pwsh, where `#` is a comment. AGENTS.md is read by agents, whose
    shells nobody types into: it keeps its comments."""
    text = shipped(name).replace("\r\n", "\n")
    found = [
        f"{name}: {line}"
        for _shell, block in SHELL_BLOCK.findall(text)
        for line in block.splitlines()
        if SHELL_COMMENT.search(line)
    ]
    assert found == [], "\n".join(found)
    # The rule sees what it is for.
    seeded = (
        "```sh\npwsh ./tools/check.ps1  # every gate\n```\n```powershell\n$x = 1  # fine\n```\n"
    )
    assert len([1 for _s, b in SHELL_BLOCK.findall(seeded) if SHELL_COMMENT.search(b)]) == 1


def test_every_command_claude_code_pre_approves_names_a_real_file() -> None:
    settings = json.loads(shipped(".claude/settings.json"))
    allowed = settings["permissions"]["allow"]
    assert allowed and len(set(allowed)) == len(allowed)
    for rule in allowed:
        tool, _, rest = rule.partition("(")
        assert tool in ("Bash", "PowerShell") and rest.endswith(")"), rule
        named = re.findall(r"(?:\./|res://)?(tools/[\w./-]+\.(?:ps1|py|gd))", rest)
        assert named, f"{rule}: names no kit tool"
        for path in named:
            assert chk.exists_exact(REPO, path), f"{rule}: {path} does not exist"
    # Whatever one shell may run without asking, the other may too.
    bash = {rule[len("Bash(") :] for rule in allowed if rule.startswith("Bash(")}
    powershell = {rule[len("PowerShell(") :] for rule in allowed if rule.startswith("PowerShell(")}
    assert bash <= powershell


def test_no_pre_approved_godot_command_takes_engine_arguments() -> None:
    """`godot.ps1 --script res://tools/x.gd *` would also allow `... --script <any file>`:
    the last --script wins, and that file runs with no prompt. A wildcard may only follow
    --user-args, where Godot hands the rest to the tool."""
    allowed = json.loads(shipped(".claude/settings.json"))["permissions"]["allow"]
    godot = [rule for rule in allowed if "tools/godot.ps1" in rule]
    assert len(godot) >= 6
    for rule in godot:
        assert "*" not in rule or rule.endswith(" --user-args *)"), rule
    for rule in allowed:
        assert rule.count("*") <= 1 and (" *)" in rule or "*" not in rule), rule


def test_a_pre_approved_devtools_command_names_its_command() -> None:
    """`devtools.py *` would also allow `devtools.py --project <another game> quit`. The
    CLI reads --project before the command only (tools/tests/test_devtools_cli.py), so a
    rule that fixes the command drives this project's game and no other."""
    allowed = json.loads(shipped(".claude/settings.json"))["permissions"]["allow"]
    rules = [rule for rule in allowed if "tools/devtools.py" in rule]
    named = [
        re.fullmatch(r"(Bash|PowerShell)\(uv run python tools/devtools\.py ([a-z-]+) \*\)", rule)
        for rule in rules
    ]
    assert rules and all(named), rules
    parser = devtools.build_parser()
    commands = next(a.choices for a in parser._actions if isinstance(a.choices, dict))
    for shell in ("Bash", "PowerShell"):
        assert sorted(found[2] for found in named if found[1] == shell) == sorted(commands)
