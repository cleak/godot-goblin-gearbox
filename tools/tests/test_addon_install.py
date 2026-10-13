"""tools/_addon_install.py and tools/install_gdunit4.py: the pin, the archive checks,
the swap with rollback, and the compare against upstream. No network: archives are built here."""

from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from io import BytesIO
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import _addon_install as addon  # noqa: E402
import install_gdunit4  # noqa: E402
import kit_config  # noqa: E402

VERSION = "9.7.0"
PREFIX = f"Fixture-{VERSION}/addons/fixture/"
FILES = {
    "plugin.cfg": f'[plugin]\nname="Fixture"\nversion="{VERSION}"\n',
    "plugin.gd": "extends EditorPlugin\n",
    "bin/Tool.gd": "extends SceneTree\n",
    "icon.png": b"\x89PNG\r\n\x00binary",
}


def _spec(destination: Path) -> addon.AddonSpec:
    """A made-up addon laid out like gdUnit4's release archive."""
    return addon.AddonSpec(
        key="fixture",
        url_template="https://example.invalid/fixture/v{version}.zip",
        inner_glob="Fixture-{version}/addons/fixture",
        destination=destination,
        required_files=("plugin.cfg", "plugin.gd", "bin/Tool.gd"),
        plugin_name="Fixture",
    )


def _archive(extra: dict[str, str | bytes] | None = None, files: dict | None = None) -> bytes:
    output = BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, content in (extra or {}).items():
            member = zipfile.ZipInfo("placeholder")
            member.filename = name  # as given: ZipInfo() would tidy a backslash on Windows
            archive.writestr(member, content)
        for name, content in (FILES if files is None else files).items():
            archive.writestr(PREFIX + name, content)
        archive.writestr(f"Fixture-{VERSION}/README.md", "not part of the addon")
    return output.getvalue()


def _install(destination: Path, payload: bytes, **overrides) -> None:
    options = {
        "expected_hash": hashlib.sha256(payload).hexdigest(),
        "version": VERSION,
        "force": True,
    }
    addon.install_payload(_spec(destination), payload, **(options | overrides))


def _tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_a_verified_archive_installs_only_the_addon_folder(tmp_path: Path) -> None:
    destination = tmp_path / "addons" / "fixture"
    _install(destination, _archive())
    assert set(_tree(destination)) == set(FILES)
    assert _tree(destination)["icon.png"] == FILES["icon.png"]
    # Nothing else is left beside it: no staging folder, no backup.
    assert [path.name for path in (tmp_path / "addons").iterdir()] == ["fixture"]


def test_a_reinstall_replaces_the_tree_and_keeps_godots_generated_files(tmp_path: Path) -> None:
    destination = tmp_path / "addons" / "fixture"
    _install(destination, _archive())
    (destination / "old.gd").write_text("# removed upstream", encoding="utf-8")
    (destination / "old.gd.uid").write_bytes(b"uid://old\n")
    (destination / "plugin.gd.uid").write_bytes(b"uid://keepme\n")
    (destination / "icon.png.import").write_bytes(b"[remap]\n")
    (destination / "plugin.gd").write_text("# edited locally", encoding="utf-8")
    _install(destination, _archive())
    tree = _tree(destination)
    assert set(tree) == set(FILES) | {"plugin.gd.uid", "icon.png.import"}
    assert tree["plugin.gd"] == b"extends EditorPlugin\n"
    assert tree["plugin.gd.uid"] == b"uid://keepme\n"
    assert [path.name for path in (tmp_path / "addons").iterdir()] == ["fixture"]


def test_without_force_an_existing_install_is_checked_not_replaced(tmp_path: Path) -> None:
    destination = tmp_path / "addons" / "fixture"
    _install(destination, _archive())
    marker = destination / "local.txt"
    marker.write_text("mine", encoding="utf-8")
    _install(destination, _archive(), force=False)
    assert marker.is_file()
    (destination / "plugin.cfg").write_text('[plugin]\nversion="1.0.0"\n', encoding="utf-8")
    with pytest.raises(RuntimeError, match="reports 1.0.0, expected 9.7.0"):
        _install(destination, _archive(), force=False)
    (destination / "bin" / "Tool.gd").unlink()
    with pytest.raises(RuntimeError, match="incomplete; missing: bin/Tool.gd"):
        addon.verify_install(_spec(destination), destination, "1.0.0")


@pytest.fixture
def existing(tmp_path: Path) -> Path:
    destination = tmp_path / "addons" / "fixture"
    destination.mkdir(parents=True)
    (destination / "keep.txt").write_text("keep", encoding="utf-8")
    return destination


def _untouched(destination: Path) -> bool:
    siblings = [path.name for path in destination.parent.iterdir()]
    return siblings == ["fixture"] and _tree(destination) == {"keep.txt": b"keep"}


def test_force_never_replaces_a_folder_that_is_not_this_addon(existing: Path) -> None:
    # `--destination addons --force` once swapped the whole addons folder for gdUnit4.
    with pytest.raises(RuntimeError, match="refusing to replace .*not a Fixture folder"):
        _install(existing, _archive())
    assert _untouched(existing)
    # Another addon's folder is not this addon's either.
    (existing / "plugin.cfg").write_text('[plugin]\nname="Other"\nversion="9.7.0"\n', "utf-8")
    with pytest.raises(RuntimeError, match="refusing to replace"):
        _install(existing, _archive())
    assert set(_tree(existing)) == {"keep.txt", "plugin.cfg"}
    # Nor is a file.
    blocker = existing.parent / "blocker"
    blocker.write_text("", encoding="utf-8")
    with pytest.raises(RuntimeError, match="refusing to replace"):
        _install(blocker, _archive())
    assert blocker.is_file()


def test_an_empty_folder_is_installed_into_without_force(tmp_path: Path) -> None:
    destination = tmp_path / "addons" / "fixture"
    destination.mkdir(parents=True)
    _install(destination, _archive(), force=False)
    assert set(_tree(destination)) == set(FILES)


def test_a_bad_hash_touches_nothing(existing: Path) -> None:
    with pytest.raises(RuntimeError, match="hash mismatch"):
        _install(existing, _archive(), expected_hash="0" * 64)
    assert _untouched(existing)


@pytest.mark.parametrize("version", ["../9.7.0", "9", "9.7.0/..", "v9.7.0", ""])
def test_a_version_that_could_be_a_path_is_rejected(existing: Path, version: str) -> None:
    with pytest.raises(ValueError, match="dotted numeric version"):
        _install(existing, _archive(), version=version)
    assert _untouched(existing)


@pytest.mark.parametrize(
    "member",
    ["../escape.txt", "/absolute.txt", "a/../../escape.txt", "back\\slash.txt", "C:/drive.txt"],
)
def test_an_archive_that_could_write_outside_its_folder_touches_nothing(
    existing: Path, member: str
) -> None:
    with pytest.raises(RuntimeError, match="unsafe"):
        _install(existing, _archive(extra={member: "escaped"}))
    assert _untouched(existing)
    assert not (existing.parent.parent / "escape.txt").exists()


def test_a_symlink_member_is_rejected(existing: Path) -> None:
    output = BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        link = zipfile.ZipInfo(PREFIX + "link")
        link.create_system = 3
        link.external_attr = 0o120777 << 16
        archive.writestr(link, "/etc/passwd")
    with pytest.raises(RuntimeError, match="symbolic link"):
        _install(existing, output.getvalue())
    assert _untouched(existing)


def test_an_archive_without_the_addon_or_with_the_wrong_version_touches_nothing(
    existing: Path,
) -> None:
    with pytest.raises(RuntimeError, match="did not contain exactly one"):
        _install(existing, _archive(files={}))
    wrong = dict(FILES) | {"plugin.cfg": '[plugin]\nversion="9.6.0"\n'}
    with pytest.raises(RuntimeError, match="reports 9.6.0, expected 9.7.0"):
        _install(existing, _archive(files=wrong))
    incomplete = {name: text for name, text in FILES.items() if name != "plugin.gd"}
    with pytest.raises(RuntimeError, match="incomplete"):
        _install(existing, _archive(files=incomplete))
    assert _untouched(existing)


def _compare(destination: Path, payload: bytes) -> list[str]:
    return addon.compare_payload(
        _spec(destination),
        payload,
        expected_hash=hashlib.sha256(payload).hexdigest(),
        version=VERSION,
    )


def test_compare_lists_what_differs_from_upstream(tmp_path: Path) -> None:
    destination = tmp_path / "addons" / "fixture"
    assert _compare(destination, _archive()) == [f"not installed: {destination}"]
    _install(destination, _archive())
    # Godot's own files and git's line endings are not differences.
    (destination / "plugin.gd.uid").write_text("uid://x\n", encoding="utf-8")
    (destination / "icon.png.import").write_text("[remap]\n", encoding="utf-8")
    (destination / "plugin.gd").write_bytes(b"extends EditorPlugin\r\n")
    assert _compare(destination, _archive()) == []
    (destination / "bin" / "Tool.gd").write_text("extends SceneTree\n# patched\n", "utf-8")
    (destination / "plugin.gd").unlink()
    (destination / "extra.gd").write_text("", encoding="utf-8")
    (destination / "icon.png").write_bytes(b"\x89PNG\n\x00binary")  # a binary's bytes are exact
    assert _compare(destination, _archive()) == [
        "missing: plugin.gd",
        "extra: extra.gd",
        "modified: bin/Tool.gd",
        "modified: icon.png",
    ]
    with pytest.raises(RuntimeError, match="hash mismatch"):
        addon.compare_payload(
            _spec(destination), _archive(), expected_hash="0" * 64, version=VERSION
        )


def _kit(root: Path, section: object) -> kit_config.KitConfig:
    (root / "kit.json").write_text(json.dumps({"fixture": section}), encoding="utf-8")
    return kit_config.load(root)


def test_the_pin_is_read_from_kit_json(tmp_path: Path) -> None:
    spec = _spec(tmp_path / "addons" / "fixture")
    digest = "AB" * 32
    config = _kit(tmp_path, {"version": "1.2.3", "source_archive_sha256": digest})
    assert addon.load_pin(spec, config) == ("1.2.3", digest.lower())
    for section, message in (
        (None, 'no "fixture" section'),
        ({"version": "../1.2.3", "source_archive_sha256": digest}, "fixture.version"),
        ({"source_archive_sha256": digest}, "fixture.version"),
        ({"version": "1.2.3", "source_archive_sha256": "abc"}, "source_archive_sha256"),
        ({"version": "1.2.3"}, "source_archive_sha256"),
    ):
        with pytest.raises(RuntimeError, match=message):
            addon.load_pin(spec, _kit(tmp_path, section))


def test_the_vendored_gdunit4_is_the_pinned_version() -> None:
    version, digest = addon.load_pin(install_gdunit4.SPEC)
    assert len(digest) == 64
    addon.verify_install(install_gdunit4.SPEC, install_gdunit4.SPEC.destination, version)
    assert install_gdunit4.SPEC.destination == kit_config.REPO / "addons" / "gdUnit4"


def test_the_command_installs_checks_and_compares_without_touching_the_project(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    payload = _archive()
    monkeypatch.setattr(install_gdunit4, "SPEC", _spec(tmp_path / "unused"))
    monkeypatch.setattr(install_gdunit4, "load_pin", lambda _spec: (VERSION, "f" * 64))
    downloads: list[str] = []

    def fake_download(spec, version):
        downloads.append(spec.url_template.format(version=version))
        return payload

    monkeypatch.setattr(addon, "download", fake_download)
    target = tmp_path / "elsewhere" / "fixture"
    digest = hashlib.sha256(payload).hexdigest()
    run = install_gdunit4.main
    # The pinned hash does not match this archive: nothing is installed.
    assert run(["--destination", str(target)]) == 1
    assert "hash mismatch" in capsys.readouterr().err and not target.exists()
    assert run(["--destination", str(target), "--sha256", digest]) == 0
    assert set(_tree(target)) == set(FILES)
    assert downloads == ["https://example.invalid/fixture/v9.7.0.zip"] * 2
    # Already there: checked, not downloaded again.
    assert run(["--destination", str(target), "--sha256", digest]) == 0
    assert len(downloads) == 2
    assert run(["--destination", str(target), "--sha256", digest, "--verify"]) == 0
    (target / "plugin.gd").write_text("# edited", encoding="utf-8")
    assert run(["--destination", str(target), "--sha256", digest, "--verify"]) == 1
    assert "modified: plugin.gd" in capsys.readouterr().out
    assert run(["--destination", str(target), "--sha256", digest, "--force"]) == 0
    assert _tree(target)["plugin.gd"] == b"extends EditorPlugin\n"
    # The addons folder itself is not a destination: refused before any download.
    (target.parent / "my_addon").mkdir()
    (target.parent / "my_addon" / "plugin.cfg").write_text('[plugin]\nname="Mine"\n', "utf-8")
    before, downloaded = _tree(target.parent), len(downloads)
    capsys.readouterr()
    assert run(["--destination", str(target.parent), "--sha256", digest, "--force"]) == 1
    assert "refusing to replace" in capsys.readouterr().err
    assert _tree(target.parent) == before and len(downloads) == downloaded


def test_another_version_needs_its_own_hash(monkeypatch, capsys) -> None:
    monkeypatch.setattr(install_gdunit4, "load_pin", lambda _spec: (VERSION, "f" * 64))
    with pytest.raises(SystemExit) as stopped:
        install_gdunit4.main(["--version", "9.8.0"])
    assert stopped.value.code == 2
    assert "pass --sha256" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        install_gdunit4.main(["--version", "../9.8.0", "--sha256", "f" * 64])
