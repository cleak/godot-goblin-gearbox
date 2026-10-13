"""kit.json is read by three loaders: tools/kit_config.py, .ps1 and .gd.

Each is written by hand, so each is run here on the same projects and the answers are
compared: the values, the one-line error for a kit.json that cannot be used, and the list
of authored files. A difference means two gates of one project check different files, or
one of them passes a kit.json the next one refuses.

PowerShell runs once for all the projects and so does Godot (the GDScript loader takes the
project folder as an argument for exactly this).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from _gate_fixtures import PWSH, REPO, clean_env, real_godot

TOOLS = REPO / "tools"
sys.path.insert(0, str(TOOLS))
import kit_config  # noqa: E402

pytestmark = pytest.mark.skipif(os.name != "nt" or PWSH is None, reason="needs PowerShell 7")

VALUES = (
    "godot_version",
    "godot_minor",
    "godot_windows_sha512",
    "godot_search_roots",
    "gdunit4_version",
    "gdunit4_sha256",
    "source_exclude",
    "source_include",
    "test_root",
    "gdscript_strict",
)
EXTENSION_SETS = ("gd", "gd,tscn")

TREE = (
    "main.gd",
    "main.tscn",
    "src/a.gd",
    "src/B.GD",
    "src/notes.txt",
    "addons/x/plugin.gd",
    "addons/mine/tool.gd",
    "addons/mine/.cache/hidden.gd",
    "addons/mine/out/.gdignore",
    "addons/mine/out/built.gd",
    "artifacts/report.gd",
    "docs/example.gd",
    "Vendor/lib.gd",
    "vendor2/lib.gd",
    "build/out.gd",
    "gen/made.gd",
    "game/play.gd",
    "game/generated/made.gd",
    "game/generated/kept/kept.gd",
    "game/generated/deep/deep.gd",
    "specs/test_a.gd",
    ".hidden/secret.gd",
    "export/.gdignore",
    "export/built.gd",
    "samples/demo/project.godot",
    "samples/demo/demo.gd",
)

# name -> kit.json text (None: no file). Every one of these loads.
USABLE = {
    "no kit.json": None,
    "an empty object": "{}",
    "the kit's own keys": json.dumps(
        {
            "godot": {"version": "4.7.2", "windows_x86_64_sha512": "ab" * 64, "search_roots": []},
            "gdunit4": {"version": "6.1.0", "source_archive_sha256": "cd" * 32},
            "source": {"exclude": ["addons", "artifacts", "docs"], "include": ["addons/mine"]},
            "tests": {"root": "specs"},
            "gdscript": {"strict": True},
        }
    ),
    "every spelling of a folder": json.dumps(
        {
            "godot": {"version": "4.10.1", "search_roots": ["D:/Godot", "%LOCALAPPDATA%\\Godot"]},
            "source": {
                "exclude": [
                    "vendor/",
                    "\\build\\",
                    "res://gen",
                    "./game/../game/generated//",
                    # Inside an excluded folder: it counts when "game" is asked for by
                    # name, not when "game/generated" is.
                    "game\\generated\\deep",
                ],
                "include": ["res://game/generated/kept/"],
            },
            "tests": {"root": "res://specs/"},
            "gdscript": {"strict": False},
        }
    ),
    "null and empty mean the default": json.dumps(
        {
            "godot": {"version": "", "search_roots": None, "windows_x86_64_sha512": None},
            "gdunit4": None,
            "source": {"exclude": None, "include": None},
            "tests": {"root": None},
            "gdscript": {"strict": None},
        }
    ),
    "an empty exclude list": '{"source": {"exclude": []}}',
    "one-part version": '{"godot": {"version": "5"}}',
    "a test root with dots": '{"tests": {"root": "./specs/../specs2/"}}',
    "unknown keys": '{"mine": [1, 2], "source": {"other": 3}, "Source": "not the key"}',
    # Every form of number JSON has, and text that only looks like a mistake.
    "numbers and words": (
        '{"mine": [0, -0, 10, 0.5, 1e5, 1E-5, -12.5e+3, true, false, null],\r\n'
        '\t"note": "01, 1. NaN ,] \\t \\" ,}", "tests": {"root": "specs"}}\n'
    ),
    "a key written twice": '{"tests": {"root": "first"}, "tests": {"root": "specs"}}',
    "saved with a byte order mark": b"\xef\xbb\xbf" + b'{"tests": {"root": "specs"}}',
    "non-ASCII text": '{"source": {"exclude": ["d\u00f6cs"]}}'.encode(),
}

# name -> (kit.json text, the error every loader gives). "..." stands for the parser's own
# words, which differ by language; the file is named after them.
BROKEN = {
    "cut short": ('{"source": {"exclude": ["addons"]}', "kit.json is not valid JSON: ..."),
    "empty file": ("", "kit.json is not valid JSON: ..."),
    "a trailing comma": ('{"tests": {"root": "specs",}}', "kit.json is not valid JSON: ..."),
    "a trailing comma in a list": (
        '{"source": {"exclude": ["a", ]}}',
        "kit.json is not valid JSON: ...",
    ),
    "a comment": ('{"tests": {"root": "specs"} // mine\n}', "kit.json is not valid JSON: ..."),
    "single quotes": ("{'tests': {'root': 'specs'}}", "kit.json is not valid JSON: ..."),
    # What one parser takes and another does not: Godot's takes control characters and
    # loose numbers, Python's takes NaN and Infinity.
    "a tab inside a string": ('{"tests": {"root": "sp\tecs"}}', "kit.json is not valid JSON: ..."),
    "a line break inside a string": (
        '{"tests": {"root": "specs\n"}}',
        "kit.json is not valid JSON: ...",
    ),
    "a form feed between values": (
        '{\f"tests": {"root": "specs"}}',
        "kit.json is not valid JSON: ...",
    ),
    "a number with a leading zero": ('{"mine": 01}', "kit.json is not valid JSON: ..."),
    "a negative number with a leading zero": (
        '{"mine": [1, -01]}',
        "kit.json is not valid JSON: ...",
    ),
    "a number that ends in a point": ('{"mine": 1.}', "kit.json is not valid JSON: ..."),
    "NaN": ('{"mine": NaN}', "kit.json is not valid JSON: ..."),
    "minus infinity": ('{"mine": -Infinity}', "kit.json is not valid JSON: ..."),
    "not UTF-8": (b'{"tests": {"root": "sp\xe9cs"}}', "kit.json is not valid JSON: ..."),
    "UTF-16": ('{"tests": {"root": "specs"}}'.encode("utf-16"), "kit.json is not valid JSON: ..."),
    "a list": ('["not", "an", "object"]', "kit.json must hold a JSON object (...)"),
    "a list of one object": (
        '[{"tests": {"root": "specs"}}]',
        "kit.json must hold a JSON object (...)",
    ),
    "a string": ('"kit"', "kit.json must hold a JSON object (...)"),
    "null": ("null", "kit.json must hold a JSON object (...)"),
    "source is a string": ('{"source": "scripts"}', 'kit.json: "source" must be an object'),
    "tests is a list": ('{"tests": ["test"]}', 'kit.json: "tests" must be an object'),
    "gdscript is a flag": ('{"gdscript": false}', 'kit.json: "gdscript" must be an object'),
    "a string for a list": (
        '{"source": {"exclude": "addons"}}',
        'kit.json: "source.exclude" must be a list of strings',
    ),
    "a number in a list": (
        '{"source": {"include": ["src", 3]}}',
        'kit.json: "source.include" must be a list of strings',
    ),
    "null in a list": (
        '{"source": {"exclude": [null]}}',
        'kit.json: "source.exclude" must be a list of strings',
    ),
    "a list in a list": (
        '{"godot": {"search_roots": [["D:/Godot"]]}}',
        'kit.json: "godot.search_roots" must be a list of strings',
    ),
    "a string for search roots": (
        '{"godot": {"search_roots": "D:/Godot"}}',
        'kit.json: "godot.search_roots" must be a list of strings',
    ),
    "a number for the version": (
        '{"godot": {"version": 4.7}}',
        'kit.json: "godot.version" must be a string',
    ),
    "a number for the checksum": (
        '{"gdunit4": {"source_archive_sha256": 0}}',
        'kit.json: "gdunit4.source_archive_sha256" must be a string',
    ),
    "a number for the pin": (
        '{"gdunit4": {"version": 6}}',
        'kit.json: "gdunit4.version" must be a string',
    ),
    "a list for the test root": (
        '{"tests": {"root": ["test"]}}',
        'kit.json: "tests.root" must be a string',
    ),
    "a flag for the test root": (
        '{"tests": {"root": true}}',
        'kit.json: "tests.root" must be a string',
    ),
    "a word for strict": (
        '{"gdscript": {"strict": "yes"}}',
        'kit.json: "gdscript.strict" must be true or false',
    ),
    "a number for strict": (
        '{"gdscript": {"strict": 1}}',
        'kit.json: "gdscript.strict" must be true or false',
    ),
    "a test root above the project": (
        '{"tests": {"root": "../elsewhere"}}',
        'kit.json: "tests.root" must be a folder inside the project, not "../elsewhere"',
    ),
    "a test root that climbs out": (
        '{"tests": {"root": "specs/../../elsewhere"}}',
        'kit.json: "tests.root" must be a folder inside the project, not "specs/../../elsewhere"',
    ),
    "a test root on a drive": (
        '{"tests": {"root": "C:/specs"}}',
        'kit.json: "tests.root" must be a folder inside the project, not "C:/specs"',
    ),
    "the project as the test root": (
        '{"tests": {"root": "res://"}}',
        'kit.json: "tests.root" must be a folder inside the project, not "res://"',
    ),
    "an exclude outside the project": (
        '{"source": {"exclude": ["docs", "../other"]}}',
        'kit.json: "source.exclude" must list folders inside the project, not "../other"',
    ),
    "an empty include": (
        '{"source": {"include": [""]}}',
        'kit.json: "source.include" must list folders inside the project, not ""',
    ),
    "an include that is not there": (
        '{"source": {"include": ["addons/mine", "addons/typo"]}}',
        'kit.json: "source.include" names a folder that does not exist: addons/typo',
    ),
    "an include that is a file": (
        '{"source": {"include": ["main.gd"]}}',
        'kit.json: "source.include" names a folder that does not exist: main.gd',
    ),
    # Two problems: every loader reports the same one.
    "sections are checked first": (
        '{"godot": {"version": 4}, "tests": 3}',
        'kit.json: "tests" must be an object',
    ),
    "values are checked in one order": (
        '{"gdscript": {"strict": 1}, "source": {"include": 5}, "godot": {"version": 4}}',
        'kit.json: "godot.version" must be a string',
    ),
    "a missing include is reported last": (
        '{"source": {"include": ["nowhere"]}, "gdscript": {"strict": 0}}',
        'kit.json: "gdscript.strict" must be true or false',
    ),
}

# Asked for by name (kit.json: the "every spelling of a folder" project).
NAMED_PATHS = (
    ".",
    "src",
    "src/a.gd",
    "src/notes.txt",
    "game",
    "game/generated",
    "game/generated/kept",
    "game/generated/deep",
    "GAME/Generated",
    "vendor",
    "addons",
    "addons/mine",
    "export",
    ".hidden",
    "missing",
    "../outside.gd",
)

# Asked for by name in the "directory links" project: "linked" points outside the project,
# "alias" at its game folder, "src/loop" back at the project itself.
LINK_PATHS = (
    "linked",
    "linked/deep/far.gd",
    "alias",
    "alias/generated",
    "alias/play.gd",
    "src/loop",
    "src/loop/src",
    "src/loop/alias/play.gd",
    "src",
)
NAMED = {"every spelling of a folder": NAMED_PATHS, "directory links": LINK_PATHS}

POWERSHELL_DRIVER = r"""
param([string]$List)
$ErrorActionPreference = "Stop"
foreach ($root in (Get-Content -LiteralPath $List)) {
    . (Join-Path $root "tools/kit_config.ps1")
    # named: a list of pairs, since a PowerShell dictionary merges "GAME" with "game".
    $record = [ordered]@{ error = ""; files = [ordered]@{}; named = @() }
    try {
        $config = Get-KitConfig
        $record.godot_version = $config.GodotVersion
        $record.godot_minor = $config.GodotMinor
        $record.godot_windows_sha512 = $config.GodotSha512
        $record.godot_search_roots = @($config.GodotSearchRoots)
        $record.gdunit4_version = $config.Gdunit4Version
        $record.gdunit4_sha256 = $config.Gdunit4Sha256
        $record.source_exclude = @($config.SourceExclude)
        $record.source_include = @($config.SourceInclude)
        $record.test_root = $config.TestRoot
        $record.gdscript_strict = $config.GdscriptStrict
        foreach ($set in "gd", "gd,tscn") {
            $extensions = @($set.Split(",") | ForEach-Object { ".$_" })
            $record.files[$set] = @(
                Get-KitSourceFiles -Extensions $extensions |
                    ForEach-Object { ConvertTo-KitRelativePath $_.FullName }
            )
        }
        $asked = Join-Path $root "named_paths.txt"
        if (Test-Path -LiteralPath $asked) {
            foreach ($path in (Get-Content -LiteralPath $asked)) {
                $pair = [ordered]@{ path = $path; found = @(); error = "" }
                try {
                    $pair.found = @(
                        Get-KitSourceFiles -Path $path |
                            ForEach-Object { ConvertTo-KitRelativePath $_.FullName }
                    )
                } catch {
                    $pair.error = $_.Exception.Message
                }
                $record.named += $pair
            }
        }
    } catch {
        $record.error = $_.Exception.Message
    }
    # Escaped: the console code page decides how anything else leaves this process.
    $record | ConvertTo-Json -Compress -Depth 5 -EscapeHandling EscapeNonAscii
}
"""

GODOT_PROBE = """extends SceneTree

const KitConfig := preload("res://tools/kit_config.gd")


func _initialize() -> void:
	var list: String = OS.get_cmdline_user_args()[0]
	for root: String in FileAccess.get_file_as_string(list).split("\\n", false):
		var record: Dictionary = KitConfig.read(root)
		var files: Dictionary = {}
		if str(record["error"]).is_empty():
			for extensions: String in ["gd", "gd,tscn"]:
				var relative: Array[String] = []
				for path: String in KitConfig.source_files(extensions.split(","), root):
					relative.append(path.trim_prefix(root).trim_prefix("/"))
				files[extensions] = relative
		record["files"] = files
		print("PARITY " + JSON.stringify(record))
	quit(0)
"""


def _slug(name: str) -> str:
    return "".join(letter if letter.isalnum() else "_" for letter in name)


def _link_folder(link: Path, target: Path) -> None:
    import _winapi

    _winapi.CreateJunction(str(target), str(link))


def _build(root: Path, kit: str | bytes | None) -> Path:
    for name in TREE:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
    (root / "tools").mkdir()
    shutil.copyfile(TOOLS / "kit_config.ps1", root / "tools" / "kit_config.ps1")
    if kit is not None:
        (root / "kit.json").write_bytes(kit if isinstance(kit, bytes) else kit.encode("utf-8"))
    return root


@pytest.fixture(scope="module")
def projects(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    base = tmp_path_factory.mktemp("parity")
    built = {}
    for name, kit in USABLE.items():
        built[name] = _build(base / _slug(name), kit)
    for name, (kit, _message) in BROKEN.items():
        built[name] = _build(base / _slug(name), kit)
    # Directory links: one to a folder outside, one back to an ancestor (never ends if
    # followed). Both loaders that walk the disk on their own must skip them, and so must
    # Godot's.
    linked = _build(base / "links", '{"source": {"exclude": []}}')
    outside = base / "outside_the_project"
    (outside / "deep").mkdir(parents=True)
    (outside / "deep" / "far.gd").write_bytes(b"")
    _link_folder(linked / "linked", outside)
    _link_folder(linked / "alias", linked / "game")
    _link_folder(linked / "src" / "loop", linked)
    built["directory links"] = linked
    for name, paths in NAMED.items():
        (built[name] / "named_paths.txt").write_text("\n".join(paths) + "\n", encoding="utf-8")
    (base / "outside.gd").write_bytes(b"")
    return built


def _python_record(root: Path, *, named: tuple[str, ...] = ()) -> dict:
    record: dict = {"error": "", "files": {}, "named": {}}
    try:
        config = kit_config.load(root)
    except kit_config.ConfigError as error:
        record["error"] = str(error)
        return record
    for key in VALUES:
        value = getattr(config, key)
        record[key] = list(value) if isinstance(value, tuple) else value
    for extensions in EXTENSION_SETS:
        suffixes = [f".{extension}" for extension in extensions.split(",")]
        found = kit_config.source_files(suffixes, config=config)
        record["files"][extensions] = [path.relative_to(config.root).as_posix() for path in found]
    for path in named:
        try:
            found = kit_config.source_files(path=path, config=config)
        except ValueError as error:
            record["named"][path] = str(error)
        else:
            record["named"][path] = [item.relative_to(config.root).as_posix() for item in found]
    return record


@pytest.fixture(scope="module")
def powershell_records(projects: dict[str, Path]) -> dict[str, dict]:
    work = next(iter(projects.values())).parent
    driver = work / "driver.ps1"
    driver.write_text(POWERSHELL_DRIVER, encoding="ascii")
    roots = work / "roots.txt"
    roots.write_text("\n".join(str(root) for root in projects.values()) + "\n", encoding="utf-8")
    done = subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-File", str(driver), str(roots)],
        env=clean_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    lines = [line for line in done.stdout.splitlines() if line.strip()]
    assert len(lines) == len(projects), done.stdout + done.stderr
    return dict(zip(projects, (json.loads(line) for line in lines), strict=True))


@pytest.fixture(scope="module")
def godot_records(projects: dict[str, Path]) -> dict[str, dict]:
    godot = real_godot()
    if godot is None:
        pytest.skip("no Godot binary: set GODOT_BIN, or install one in .tools/godot/")
    work = next(iter(projects.values())).parent
    host = work / "godot_host"
    (host / "tools").mkdir(parents=True)
    (host / "project.godot").write_text("config_version=5\n", encoding="utf-8")
    shutil.copyfile(TOOLS / "kit_config.gd", host / "tools" / "kit_config.gd")
    (host / "probe.gd").write_text(GODOT_PROBE, encoding="utf-8", newline="\n")
    roots = work / "godot_roots.txt"
    listed = "\n".join(root.as_posix() for root in projects.values())
    roots.write_text(listed, encoding="utf-8", newline="\n")
    command = [godot, "--headless", "--path", str(host), "--script", "res://probe.gd", "--"]
    done = subprocess.run(
        [*command, roots.as_posix()],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,
    )
    lines = [line[7:] for line in done.stdout.splitlines() if line.startswith("PARITY ")]
    assert done.returncode == 0 and len(lines) == len(projects), done.stdout + done.stderr
    assert "SCRIPT ERROR" not in done.stdout + done.stderr, done.stdout + done.stderr
    return dict(zip(projects, (json.loads(line) for line in lines), strict=True))


def _same_error(expected: str, message: str, kit_json: Path) -> bool:
    """`expected`, where "(...)" is the file and a final "..." is the parser's own words
    followed by the file. Godot writes the path with forward slashes."""
    shown = message.replace("\\", "/")
    where = f"({kit_json.as_posix()})"
    if expected.endswith("(...)"):
        return shown == expected.replace("(...)", where)
    if expected.endswith("..."):
        head = expected[:-3]
        return shown.startswith(head) and shown.endswith(where) and "\n" not in shown
    return message == expected


def _values(record: dict) -> dict:
    return {key: record[key] for key in VALUES}


def _files(record: dict) -> dict:
    # Order is each language's own (case rules differ); the set is what must agree.
    return {key: sorted(paths) for key, paths in record["files"].items()}


@pytest.mark.parametrize("name", USABLE)
def test_powershell_reads_what_python_reads(
    name: str, projects: dict[str, Path], powershell_records: dict[str, dict]
) -> None:
    mine = _python_record(projects[name])
    theirs = powershell_records[name]
    assert mine["error"] == theirs["error"] == ""
    assert _values(theirs) == _values(mine)
    assert _files(theirs) == _files(mine)


@pytest.mark.parametrize("name", USABLE)
def test_gdscript_reads_what_python_reads(
    name: str, projects: dict[str, Path], godot_records: dict[str, dict]
) -> None:
    mine = _python_record(projects[name])
    theirs = godot_records[name]
    assert mine["error"] == theirs["error"] == ""
    assert _values(theirs) == _values(mine)
    assert _files(theirs) == _files(mine)


@pytest.mark.parametrize("name", BROKEN)
def test_a_broken_kit_json_is_the_same_one_line_in_python_and_powershell(
    name: str, projects: dict[str, Path], powershell_records: dict[str, dict]
) -> None:
    expected = BROKEN[name][1]
    kit_json = projects[name] / "kit.json"
    mine = _python_record(projects[name])["error"]
    assert _same_error(expected, mine, kit_json), mine
    theirs = powershell_records[name]["error"]
    assert _same_error(expected, theirs, kit_json), theirs


@pytest.mark.parametrize("name", BROKEN)
def test_a_broken_kit_json_is_the_same_one_line_in_gdscript(
    name: str, projects: dict[str, Path], godot_records: dict[str, dict]
) -> None:
    theirs = godot_records[name]["error"]
    assert _same_error(BROKEN[name][1], theirs, projects[name] / "kit.json"), theirs
    # Nothing is listed for a kit.json that cannot be used.
    assert godot_records[name]["files"] == {}


def test_the_expected_files_are_the_authored_ones(projects: dict[str, Path]) -> None:
    # The comparisons above would also pass if every loader were wrong the same way.
    plain = _python_record(projects["no kit.json"])["files"]["gd,tscn"]
    assert sorted(plain) == sorted(
        [
            "main.gd",
            "main.tscn",
            "src/a.gd",
            "src/B.GD",
            "Vendor/lib.gd",
            "vendor2/lib.gd",
            "build/out.gd",
            "gen/made.gd",
            "game/play.gd",
            "game/generated/made.gd",
            "game/generated/kept/kept.gd",
            "game/generated/deep/deep.gd",
            "specs/test_a.gd",
        ]
    )
    spelled = _python_record(projects["every spelling of a folder"])
    excluded = ["vendor", "build", "gen", "game/generated", "game/generated/deep"]
    assert spelled["source_exclude"] == excluded
    assert spelled["source_include"] == ["game/generated/kept"]
    assert spelled["test_root"] == "specs" and spelled["gdscript_strict"] is False
    assert sorted(spelled["files"]["gd"]) == sorted(
        [
            "main.gd",
            "src/a.gd",
            "src/B.GD",
            "addons/x/plugin.gd",
            "addons/mine/tool.gd",
            "artifacts/report.gd",
            "docs/example.gd",
            "vendor2/lib.gd",
            "game/play.gd",
            "game/generated/kept/kept.gd",
            "specs/test_a.gd",
        ]
    )


def test_directory_links_are_followed_by_no_loader(
    projects: dict[str, Path], powershell_records: dict[str, dict], godot_records: dict[str, dict]
) -> None:
    mine = _files(_python_record(projects["directory links"]))
    links = ("linked", "loop", "alias")
    assert not [path for path in mine["gd"] if any(link in path for link in links)]
    assert "src/a.gd" in mine["gd"] and "game/play.gd" in mine["gd"]
    assert _files(powershell_records["directory links"]) == mine
    assert _files(godot_records["directory links"]) == mine


def _named(
    name: str, projects: dict[str, Path], powershell_records: dict[str, dict]
) -> dict[str, list[str] | str]:
    """path -> the files Python lists for it, or its error; PowerShell must agree."""
    mine = _python_record(projects[name], named=NAMED[name])["named"]
    pairs = powershell_records[name]["named"]
    assert [pair["path"] for pair in pairs] == list(NAMED[name])
    theirs = {pair["path"]: pair["error"] or pair["found"] for pair in pairs}
    for path in NAMED[name]:
        if isinstance(mine[path], str):
            # Each language words its own "no such path" error; it is the same kind.
            kind = "does not exist" if path == "missing" else "outside the project"
            assert kind in mine[path] and kind in theirs[path], (path, mine[path], theirs[path])
        else:
            assert sorted(theirs[path]) == sorted(mine[path]), path
    return mine


def test_a_path_asked_for_by_name_gives_the_same_files_in_python_and_powershell(
    projects: dict[str, Path], powershell_records: dict[str, dict]
) -> None:
    mine = _named("every spelling of a folder", projects, powershell_records)
    # A folder gives its authored files: an excluded folder inside it stays out and an
    # included one comes back...
    assert sorted(mine["game"]) == ["game/generated/kept/kept.gd", "game/play.gd"]
    assert sorted(mine["."]) == sorted(
        _python_record(projects["every spelling of a folder"])["files"]["gd"]
    )
    # ...unless the folder asked for is itself excluded: then all of it is checked, the
    # folders inside it that are excluded by name too.
    assert sorted(mine["game/generated"]) == [
        "game/generated/deep/deep.gd",
        "game/generated/kept/kept.gd",
        "game/generated/made.gd",
    ]
    assert mine["game/generated/deep"] == ["game/generated/deep/deep.gd"]
    assert mine["GAME/Generated"] and mine["vendor"] == ["Vendor/lib.gd"]
    assert mine["src/notes.txt"] == [] and mine["src/a.gd"] == ["src/a.gd"]
    # Named or not, what Godot does not scan stays out.
    assert mine["export"] == ["export/built.gd"] and mine[".hidden"] == [".hidden/secret.gd"]
    assert sorted(mine["addons/mine"]) == ["addons/mine/tool.gd"]


def test_a_directory_link_asked_for_by_name_is_followed_to_where_it_points(
    projects: dict[str, Path], powershell_records: dict[str, dict]
) -> None:
    """Python resolves the path it is given. PowerShell walked the link as if it were a
    folder (other paths for the same files), also when it pointed outside the project."""
    mine = _named("directory links", projects, powershell_records)
    assert "outside the project" in mine["linked"]
    assert "outside the project" in mine["linked/deep/far.gd"]
    game = [
        "game/generated/deep/deep.gd",
        "game/generated/kept/kept.gd",
        "game/generated/made.gd",
        "game/play.gd",
    ]
    assert sorted(mine["alias"]) == game and sorted(mine["alias/generated"]) == game[:3]
    assert mine["alias/play.gd"] == ["game/play.gd"] == mine["src/loop/alias/play.gd"]
    everything = _python_record(projects["directory links"])["files"]["gd"]
    assert sorted(mine["src/loop"]) == sorted(everything)
    assert sorted(mine["src/loop/src"]) == sorted(mine["src"]) == ["src/B.GD", "src/a.gd"]


@pytest.mark.real_godot
def test_gdscript_reads_its_own_project_by_default(tmp_path: Path) -> None:
    godot = real_godot()
    if godot is None:
        pytest.skip("no Godot binary: set GODOT_BIN, or install one in .tools/godot/")
    kit = {"source": {"exclude": ["docs"], "include": []}, "tests": {"root": "res://specs/"}}
    root = _build(tmp_path / "own", json.dumps(kit))
    (root / "project.godot").write_text("config_version=5\n", encoding="utf-8")
    shutil.copyfile(TOOLS / "kit_config.gd", root / "tools" / "kit_config.gd")
    probe = (
        'extends SceneTree\n\nconst KitConfig := preload("res://tools/kit_config.gd")\n\n\n'
        "func _initialize() -> void:\n"
        '\tprint("OWN ", JSON.stringify([KitConfig.config_problem(), KitConfig.test_root(), '
        "KitConfig.gdscript_strict(), KitConfig.source_exclude(), "
        'KitConfig.source_files(["gd"])]))\n\tquit(0)\n'
    )
    (root / "probe.gd").write_text(probe, encoding="utf-8", newline="\n")
    command = [godot, "--headless", "--path", str(root), "--script", "res://probe.gd"]

    def run() -> tuple[list, str]:
        done = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", timeout=120, check=False
        )
        lines = [line[4:] for line in done.stdout.splitlines() if line.startswith("OWN ")]
        assert done.returncode == 0 and len(lines) == 1, done.stdout + done.stderr
        return json.loads(lines[0]), done.stdout + done.stderr

    (problem, test_root, strict, exclude, files), _output = run()
    assert (problem, test_root, strict, exclude) == ("", "specs", True, ["docs"])
    config = kit_config.load(root)
    expected = [
        "res://" + path.relative_to(root).as_posix()
        for path in kit_config.source_files(config=config)
    ]
    assert sorted(files) == sorted(expected) and "res://addons/x/plugin.gd" in files

    # A kit.json that cannot be used gives no files and says why; never the defaults.
    (root / "kit.json").write_text('{"source": {"exclude": "docs"}}', encoding="utf-8")
    (problem, test_root, _strict, exclude, files), output = run()
    assert problem == 'kit.json: "source.exclude" must be a list of strings'
    assert (test_root, exclude, files) == ("", [], [])
    assert f"ERROR: {problem}" in output
