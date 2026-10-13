"""tools/kit_config.py: kit.json values and the one rule for "authored source"."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import kit_config  # noqa: E402

SHA512 = re.compile(r"[0-9a-f]{128}")


def _project(root: Path, files: list[str], kit: dict | None = None) -> kit_config.KitConfig:
    for name in files:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    if kit is not None:
        (root / "kit.json").write_text(json.dumps(kit), encoding="utf-8")
    return kit_config.load(root)


def _found(config: kit_config.KitConfig, **options) -> list[str]:
    files = kit_config.source_files(config=config, **options)
    return [path.relative_to(config.root).as_posix() for path in files]


def test_the_kit_reads_its_own_kit_json() -> None:
    config = kit_config.load()
    assert config.root == TOOLS.parent
    assert config.godot_minor == ".".join(config.godot_version.split(".")[:2])
    assert config.test_dir == config.root / config.test_root
    assert config.gdunit4_version and len(config.gdunit4_sha256) == 64
    # One checksum for every official download, each its own.
    assert tuple(config.godot_sha512) == kit_config.GODOT_BUILDS
    checksums = list(config.godot_sha512.values())
    assert all(SHA512.fullmatch(checksum) for checksum in checksums), checksums
    assert len(set(checksums)) == len(kit_config.GODOT_BUILDS)


def test_every_download_has_its_own_checksum_key(tmp_path: Path) -> None:
    kit = {"godot": {"linux_x86_64_sha512": "ab" * 64, "macos_universal_sha512": None}}
    config = _project(tmp_path, [], kit)
    assert dict(config.godot_sha512) == {
        "windows_x86_64": "",
        "linux_x86_64": "ab" * 64,
        "linux_arm64": "",
        "macos_universal": "",
    }
    with pytest.raises(TypeError):
        config.godot_sha512["linux_arm64"] = "cd" * 64  # read-only, as the config is
    # Still a value: equal to another load of the same file, and usable as a key.
    assert config == kit_config.load(tmp_path) and len({config, kit_config.load(tmp_path)}) == 1
    other = _project(tmp_path, [], {"godot": {"linux_x86_64_sha512": "cd" * 64}})
    assert config != other
    for build in kit_config.GODOT_BUILDS:
        (tmp_path / "kit.json").write_text(json.dumps({"godot": {f"{build}_sha512": 5}}))
        with pytest.raises(kit_config.ConfigError) as caught:
            kit_config.load(tmp_path)
        assert str(caught.value) == f'kit.json: "godot.{build}_sha512" must be a string'


@pytest.mark.parametrize(
    ("platform", "architecture", "build"),
    [
        ("windows", "x86_64", "windows_x86_64"),
        ("windows", "arm64", "windows_x86_64"),  # the 64-bit x86 build runs there too
        ("linux", "x86_64", "linux_x86_64"),
        ("linux", "arm64", "linux_arm64"),
        ("linux", "riscv64", ""),  # Godot publishes none
        ("linux", "x86", ""),
        ("macos", "arm64", "macos_universal"),
        ("macos", "x86_64", "macos_universal"),
        ("freebsd", "x86_64", ""),
    ],
)
def test_the_download_for_a_system(platform: str, architecture: str, build: str) -> None:
    assert kit_config.godot_build(platform, architecture) == build


def test_this_machine_is_named_like_the_downloads(monkeypatch) -> None:
    assert kit_config.host_platform() in ("windows", "linux", "macos")
    assert kit_config.godot_build() == kit_config.godot_build(
        kit_config.host_platform(), kit_config.host_architecture()
    )
    for platform, named in (("win32", "windows"), ("darwin", "macos"), ("linux", "linux")):
        monkeypatch.setattr(sys, "platform", platform)
        assert kit_config.host_platform() == named
    machines = {"AMD64": "x86_64", "x86_64": "x86_64", "arm64": "arm64", "aarch64": "arm64"}
    for machine, named in (machines | {"riscv64": "riscv64"}).items():
        monkeypatch.setattr(kit_config._platform, "machine", lambda machine=machine: machine)
        assert kit_config.host_architecture() == named


def test_a_missing_kit_json_gives_the_defaults(tmp_path: Path) -> None:
    config = kit_config.load(tmp_path)
    assert config.root == tmp_path.resolve()
    assert dict(config.godot_sha512) == dict.fromkeys(kit_config.GODOT_BUILDS, "")
    assert config.source_exclude == ("addons", "artifacts", "docs")
    assert config.source_include == ()
    assert config.test_root == "test"
    assert config.godot_minor == "4.7"
    assert config.godot_search_roots == ()


def test_values_come_from_kit_json(tmp_path: Path) -> None:
    kit = {
        "godot": {"version": "4.5.1", "search_roots": ["D:/Godot", "~/godot"]},
        "gdunit4": {"version": "6.0.0", "source_archive_sha256": "ab" * 32},
        "source": {"exclude": ["vendor/", "\\build\\"], "include": ["vendor/mine"]},
        "tests": {"root": "/specs/"},
        "gdscript": {"strict": False},
    }
    config = _project(tmp_path, ["vendor/mine/a.gd"], kit)
    assert config.godot_version == "4.5.1" and config.godot_minor == "4.5"
    assert config.godot_search_roots == ("D:/Godot", "~/godot")
    assert (config.gdunit4_version, config.gdunit4_sha256) == ("6.0.0", "ab" * 32)
    # Slashes at the ends are dropped and backslashes become forward slashes.
    assert config.source_exclude == ("vendor", "build")
    assert config.source_include == ("vendor/mine",)
    assert config.test_root == "specs"
    assert config.gdscript_strict is False
    assert config.raw == kit


def test_strict_typing_is_on_unless_kit_json_turns_it_off(tmp_path: Path) -> None:
    assert kit_config.load(tmp_path).gdscript_strict is True
    assert _project(tmp_path, [], {"gdscript": {}}).gdscript_strict is True
    assert _project(tmp_path, [], {"gdscript": {"strict": True}}).gdscript_strict is True
    assert _project(tmp_path, [], {"gdscript": {"strict": False}}).gdscript_strict is False


@pytest.mark.parametrize(
    ("written", "folder"),
    [
        ("test", "test"),
        ("test/", "test"),
        ("res://test", "test"),
        ("res://test/unit/", "test/unit"),
        ("\\test\\unit", "test/unit"),
        ("./test//unit/.", "test/unit"),
        ("game/../test", "test"),
        # Not a folder inside the project: the root itself, or a way out of it.
        ("", ""),
        (".", ""),
        ("res://", ""),
        ("..", ""),
        ("../test", ""),
        ("test/../../elsewhere", ""),
        ("C:/test", ""),
        ("C:\\test", ""),
    ],
)
def test_a_folder_may_be_written_many_ways(written: str, folder: str) -> None:
    assert kit_config.project_folder(written) == folder


def test_the_test_root_is_one_folder_however_it_is_written(tmp_path: Path) -> None:
    for written in ("specs", "specs/", "res://specs", "res://specs/", "./specs", "\\specs\\"):
        config = _project(tmp_path, [], {"tests": {"root": written}})
        assert config.test_root == "specs" and config.test_dir == tmp_path / "specs", written
    for written in ("../specs", "res://", ".", "C:/specs", "specs/../.."):
        (tmp_path / "kit.json").write_text(json.dumps({"tests": {"root": written}}))
        with pytest.raises(kit_config.ConfigError) as caught:
            kit_config.load(tmp_path)
        expected = f'kit.json: "tests.root" must be a folder inside the project, not "{written}"'
        assert str(caught.value) == expected


def test_an_empty_exclude_list_excludes_nothing(tmp_path: Path) -> None:
    config = _project(tmp_path, ["addons/a.gd", "docs/b.gd"], {"source": {"exclude": []}})
    assert _found(config) == ["addons/a.gd", "docs/b.gd"]


def test_kit_json_saved_with_a_bom_still_loads(tmp_path: Path) -> None:
    # Windows PowerShell 5.1 `Set-Content -Encoding utf8` writes one.
    (tmp_path / "kit.json").write_bytes(b"\xef\xbb\xbf" + b'{"tests": {"root": "specs"}}')
    assert kit_config.load(tmp_path).test_root == "specs"


def test_kit_json_that_is_not_json_is_an_error_that_names_the_file(tmp_path: Path) -> None:
    (tmp_path / "kit.json").write_text('{"tests": {"root": "test"}\n "x": 1}', encoding="utf-8")
    with pytest.raises(kit_config.ConfigError, match="kit.json is not valid JSON") as caught:
        kit_config.load(tmp_path)
    # Which file, and where in it.
    assert str(tmp_path / "kit.json") in str(caught.value) and "line 2" in str(caught.value)
    assert isinstance(caught.value, ValueError)
    (tmp_path / "kit.json").write_bytes(b"\xff\xfe{}")
    with pytest.raises(kit_config.ConfigError, match="kit.json is not valid JSON"):
        kit_config.load(tmp_path)


@pytest.mark.parametrize(
    ("kit", "message"),
    [
        ([], "kit.json must hold a JSON object"),
        ({"source": "addons"}, '"source" must be an object'),
        # A bare string would exclude nothing, silently.
        ({"source": {"exclude": "addons"}}, '"source.exclude" must be a list of strings'),
        ({"source": {"include": [1]}}, '"source.include" must be a list of strings'),
        # ...and this one became one search root per character.
        ({"godot": {"search_roots": "D:/Godot"}}, '"godot.search_roots" must be a list'),
        ({"godot": {"version": 4.7}}, '"godot.version" must be a string'),
        ({"tests": {"root": ["test"]}}, '"tests.root" must be a string'),
        ({"gdunit4": {"version": 6}}, '"gdunit4.version" must be a string'),
        ({"gdscript": True}, '"gdscript" must be an object'),
        # "false" in quotes would read as true.
        ({"gdscript": {"strict": "false"}}, '"gdscript.strict" must be true or false'),
        ({"gdscript": {"strict": 0}}, '"gdscript.strict" must be true or false'),
        (
            {"source": {"exclude": ["docs", "../other"]}},
            '"source.exclude" must list folders inside the project, not "../other"',
        ),
        ({"source": {"include": ["."]}}, '"source.include" must list folders inside the project'),
    ],
)
def test_a_value_of_the_wrong_type_is_an_error_that_names_the_key(
    tmp_path: Path, kit: object, message: str
) -> None:
    (tmp_path / "kit.json").write_text(json.dumps(kit), encoding="utf-8")
    with pytest.raises(kit_config.ConfigError) as caught:
        kit_config.load(tmp_path)
    assert message in str(caught.value)


def test_null_and_empty_values_mean_the_default(tmp_path: Path) -> None:
    kit = {
        "godot": {"version": "", "search_roots": None},
        "source": {"exclude": None, "include": None},
        "tests": {"root": None},
        "gdunit4": None,
    }
    config = _project(tmp_path, [], kit)
    assert config.godot_version == "4.7.2" and config.godot_search_roots == ()
    assert config.source_exclude == ("addons", "artifacts", "docs")
    assert config.test_root == "test" and config.gdunit4_version == ""


def test_source_skips_what_godot_does_not_scan_and_what_kit_json_excludes(tmp_path: Path) -> None:
    config = _project(
        tmp_path,
        [
            "main.gd",
            "scripts/core/grid.gd",
            "scripts/core/notes.txt",
            ".godot/cache.gd",
            ".venv/lib/site.gd",
            "scripts/.hidden/secret.gd",
            "export/.gdignore",
            "export/built.gd",
            "samples/demo/project.godot",
            "samples/demo/demo.gd",
            "addons/third_party/plugin.gd",
            "addons/mine/tool.gd",
            "artifacts/report.gd",
            "docs/example.gd",
        ],
        {"source": {"exclude": ["addons", "artifacts", "docs"], "include": ["addons/mine"]}},
    )
    assert _found(config) == ["addons/mine/tool.gd", "main.gd", "scripts/core/grid.gd"]


def test_exclude_names_a_folder_from_the_project_root_only(tmp_path: Path) -> None:
    config = _project(tmp_path, ["docs/a.gd", "scripts/docs/b.gd", "Docs2/c.gd"])
    assert _found(config) == ["Docs2/c.gd", "scripts/docs/b.gd"]


def test_exclude_ignores_letter_case(tmp_path: Path) -> None:
    config = _project(tmp_path, ["Vendor/a.gd", "src/b.gd"], {"source": {"exclude": ["vendor"]}})
    assert _found(config) == ["src/b.gd"]


def test_a_nested_exclude_and_include_work_by_path(tmp_path: Path) -> None:
    kit = {"source": {"exclude": ["game/generated"], "include": ["game/generated/kept"]}}
    files = ["game/a.gd", "game/generated/b.gd", "game/generated/kept/c.gd"]
    config = _project(tmp_path, files, kit)
    assert _found(config) == ["game/a.gd", "game/generated/kept/c.gd"]


def test_an_included_folder_still_skips_dot_and_gdignore_folders(tmp_path: Path) -> None:
    files = ["addons/mine/a.gd", "addons/mine/.cache/b.gd", "addons/mine/out/.gdignore"]
    files.append("addons/mine/out/c.gd")
    config = _project(tmp_path, files, {"source": {"include": ["addons/mine"]}})
    assert _found(config) == ["addons/mine/a.gd"]


def test_a_missing_included_folder_is_an_error(tmp_path: Path) -> None:
    # A typo there would drop the folder from every check without a word.
    with pytest.raises(kit_config.ConfigError) as caught:
        _project(tmp_path, ["a.gd"], {"source": {"include": ["addons/gone"]}})
    assert str(caught.value) == (
        'kit.json: "source.include" names a folder that does not exist: addons/gone'
    )
    # An excluded folder that is not there excludes nothing; that is no mistake.
    config = _project(tmp_path, ["a.gd"], {"source": {"exclude": ["addons/gone"]}})
    assert _found(config) == ["a.gd"]


def test_suffixes_select_and_ignore_case(tmp_path: Path) -> None:
    config = _project(tmp_path, ["a.gd", "b.GD", "c.tscn", "d.gdshader", "e.txt"])
    assert _found(config) == ["a.gd", "b.GD"]
    assert _found(config, suffixes=(".tscn", ".GDSHADER")) == ["c.tscn", "d.gdshader"]


def test_an_explicit_path_wins_over_exclude(tmp_path: Path) -> None:
    files = ["addons/x/a.gd", "addons/x/.git/b.gd", "addons/y/c.gd", "src/d.gd"]
    config = _project(tmp_path, files)
    assert _found(config) == ["src/d.gd"]
    assert _found(config, path="addons") == ["addons/x/a.gd", "addons/y/c.gd"]
    assert _found(config, path="addons/x") == ["addons/x/a.gd"]
    assert _found(config, path=tmp_path / "addons" / "y" / "c.gd") == ["addons/y/c.gd"]
    # The root itself is the whole project, excludes applied.
    assert _found(config, path=".") == ["src/d.gd"]


def test_an_explicit_folder_keeps_the_excludes_inside_it(tmp_path: Path) -> None:
    """Asking for `game` is not asking for `game/generated`: only a folder that is
    itself excluded (or lies in one) is checked whole."""
    kit = {
        "source": {
            "exclude": ["addons", "game/generated", "game/vendor"],
            "include": ["game/generated/kept", "addons/mine"],
        }
    }
    files = [
        "game/a.gd",
        "game/ui/b.gd",
        "game/generated/c.gd",
        "game/generated/kept/d.gd",
        "game/vendor/e.gd",
        "addons/mine/f.gd",
        "addons/theirs/g.gd",
    ]
    config = _project(tmp_path, files, kit)
    assert _found(config) == [
        "addons/mine/f.gd",
        "game/a.gd",
        "game/generated/kept/d.gd",
        "game/ui/b.gd",
    ]
    # The same files as the whole-project list, narrowed to the folder.
    assert _found(config, path="game") == [
        "game/a.gd",
        "game/generated/kept/d.gd",
        "game/ui/b.gd",
    ]
    assert _found(config, path="game/ui") == ["game/ui/b.gd"]
    # Named itself, an excluded folder is checked whole; so is anything inside one.
    assert _found(config, path="game/generated") == [
        "game/generated/c.gd",
        "game/generated/kept/d.gd",
    ]
    assert _found(config, path="game/vendor") == ["game/vendor/e.gd"]
    assert _found(config, path="addons") == ["addons/mine/f.gd", "addons/theirs/g.gd"]
    assert _found(config, path="addons/theirs") == ["addons/theirs/g.gd"]
    assert _found(config, path="game/generated/c.gd") == ["game/generated/c.gd"]
    # "gamely" is not under "game": names are compared whole.
    more = _project(tmp_path, ["game/vendor2/h.gd"], kit)
    assert _found(more, path="game/vendor2") == ["game/vendor2/h.gd"]
    assert "game/vendor2/h.gd" in _found(more, path="game")


def test_a_relative_path_takes_either_slash_on_every_system(tmp_path: Path) -> None:
    """A command line and a kit.json travel between systems, and tools/kit_config.ps1
    reads `addons\\x` as `addons/x` everywhere: so do the Python tools."""
    config = _project(tmp_path, ["addons/x/a.gd", "src/d.gd"])
    for spelled in ("addons/x", "addons\\x", "addons\\x\\", "./addons/x", ".\\addons\\x"):
        assert _found(config, path=spelled) == ["addons/x/a.gd"], spelled
    assert _found(config, path="addons\\x\\a.gd") == ["addons/x/a.gd"]
    root = config.root
    assert kit_config.project_path("addons\\x/a.gd", root) == root / "addons" / "x" / "a.gd"
    # An absolute path is this system's own: nothing in it is rewritten.
    absolute = root / "src" / "d.gd"
    assert kit_config.project_path(absolute, root) == absolute
    assert kit_config.project_path(str(absolute), root) == absolute
    with pytest.raises(ValueError, match="does not exist"):
        kit_config.source_files(path="addons\\missing", config=config)
    with pytest.raises(ValueError, match="outside the project"):
        kit_config.source_files(path="..\\", config=config)


def test_an_explicit_file_with_another_suffix_is_not_returned(tmp_path: Path) -> None:
    config = _project(tmp_path, ["notes.txt"])
    assert _found(config, path="notes.txt") == []


def test_a_bad_explicit_path_raises(tmp_path: Path) -> None:
    config = _project(tmp_path / "project", ["a.gd"])
    (tmp_path / "outside.gd").write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="does not exist"):
        kit_config.source_files(path="missing", config=config)
    with pytest.raises(ValueError, match="outside the project"):
        kit_config.source_files(path=tmp_path / "outside.gd", config=config)
    with pytest.raises(ValueError, match="outside the project"):
        kit_config.source_files(path="../outside.gd", config=config)


def test_results_are_sorted_absolute_and_unique(tmp_path: Path) -> None:
    kit = {"source": {"exclude": [], "include": ["b"]}}
    config = _project(tmp_path, ["b/z.gd", "b/a.gd", "a/m.gd"], kit)
    files = kit_config.source_files(config=config)
    assert files == sorted(files) and len(files) == len(set(files)) == 3
    assert all(path.is_absolute() for path in files)


def _link_folder(link: Path, target: Path) -> bool:
    """A directory link without admin rights: a junction on Windows, a symlink elsewhere."""
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
        return True
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        return False
    return True


def test_directory_links_are_not_followed(tmp_path: Path) -> None:
    config = _project(tmp_path / "project", ["src/a.gd"])
    outside = tmp_path / "elsewhere"
    (outside / "deep").mkdir(parents=True)
    (outside / "deep" / "b.gd").write_text("", encoding="utf-8")
    if not _link_folder(config.root / "linked", outside):
        pytest.skip("cannot create a directory link here")
    # A link back to an ancestor would never end if it were followed.
    assert _link_folder(config.root / "src" / "loop", config.root)
    assert _found(config) == ["src/a.gd"]


def test_to_res(tmp_path: Path) -> None:
    config = _project(tmp_path, ["scripts/core/grid.gd"])
    assert kit_config.to_res(tmp_path / "scripts" / "core" / "grid.gd", config) == (
        "res://scripts/core/grid.gd"
    )
    with pytest.raises(ValueError):
        kit_config.to_res(tmp_path.parent / "elsewhere.gd", config)
