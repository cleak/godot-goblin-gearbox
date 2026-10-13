"""tools/check_typed_ternaries.py: clean trees pass; planted sites are flagged."""

from __future__ import annotations

import json
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(TOOLS / "ci"))
import check_typed_ternaries as chk  # noqa: E402

BAD = "func f() -> void:\n\tvar names: Array[String] = load_names() if ok else []\n"
GOOD = "func f() -> void:\n\tvar names: Array[String] = []\n"
ALLOWLIST = "tools/ci/typed_ternary_allowlist.txt"


def _project(root: Path, files: dict[str, str], kit: dict | None = None) -> Path:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    if kit is not None:
        (root / "kit.json").write_text(json.dumps(kit), encoding="utf-8")
    return root


def _lines(text: str) -> list[int]:
    return [n for n, _ in chk.scan_text(text)]


def test_flags_typed_declarations() -> None:
    text = "\n".join(
        [
            "func a() -> void:",
            "\tvar out: Array[StringName] = m.species() if m != null else []",
            "\tvar d: Dictionary[int, int] = {} if m == null else m.held()",
            "\tvar ok: Array[StringName] = []",
            "\tvar plain: Array = m.rows() if m != null else []",
            "\tvar inferred := m.stats() if m != null else {}",
        ]
    )
    assert _lines(text) == [2, 3]


def test_flags_returns_in_typed_functions_and_lambdas() -> None:
    text = "\n".join(
        [
            "func a() -> Array[StringName]:",
            "\treturn _ids.duplicate() if _valid else []",
            "",
            "",
            "func b() -> Dictionary:",
            "\treturn _rows.duplicate() if _valid else {}",
            "",
            "",
            "func c() -> void:",
            "\tsrc = func(z: Vector2i) -> Dictionary[int, int]:",
            "\t\treturn seed_of(z) if z.x > 0 else {}",
            "\tvar rows: Array[Dictionary] = []",
            "",
            "",
            "func d() -> Array[Dictionary]:",
            "\tvar inner := func() -> Dictionary:",
            "\t\treturn {} if x else y",
            "\treturn trace.rows(w) if w != null else []",
        ]
    )
    assert _lines(text) == [2, 11, 18]


def test_flags_assignments_to_typed_names() -> None:
    text = "\n".join(
        [
            "var _rows: Array[int] = []",
            "var _any: Array = []",
            "",
            "",
            "func a() -> void:",
            "\t_rows = typed() if ok else []",
            "\tself._rows = [] if ok else typed()",
            "\t_any = typed() if ok else []",
        ]
    )
    assert _lines(text) == [6, 7]


def test_multiline_and_parenthesised_ternaries() -> None:
    text = "\n".join(
        [
            "func a() -> Array[int]:",
            "\treturn (",
            "\t\ttyped()",
            "\t\tif ok",
            "\t\telse []",
            "\t)",
        ]
    )
    assert _lines(text) == [2]


def test_ignores_nested_arguments_strings_comments_and_marks() -> None:
    text = "\n".join(
        [
            "func a() -> Array[int]:",
            "\tvar x: Array[int] = f(g() if ok else [])",
            '\tvar s: Array[String] = ["[] if a else b"]',
            "\t# var c: Array[int] = t() if ok else []",
            "\tvar m: Array[int] = t() if ok else []  # typed-ternary-ok: t() is never typed",
            "\tvar i: Array[int] = rows[0] if ok else t()",
            "\treturn x",
        ]
    )
    assert _lines(text) == []


def test_flags_non_empty_literal_operands() -> None:
    text = "\n".join(
        [
            "func a() -> Array[int]:",
            "\tvar n: Array[int] = [x] if ok else t()",
            "\tvar d: Dictionary[int, int] = t() if ok else {1: 2}",
            "\treturn [x, y] if ok else t()",
        ]
    )
    assert _lines(text) == [2, 3, 4]


def test_flags_annotated_declarations() -> None:
    text = "\n".join(
        [
            "@onready var rows: Array[int] = get_rows() if ready else []",
            "@export_range(0, 9) var d: Dictionary[int, int] = {} if a else b()",
            "@onready static var s: Array[int] = t() if ok else []",
            "@onready var ok: Array[int] = []",
        ]
    )
    assert _lines(text) == [1, 2, 3]


def test_commented_declaration_does_not_type_a_name() -> None:
    text = "\n".join(
        [
            "# var rows: Array[int]",
            "var rows = []  # var other: Array[int]",
            "",
            "",
            "func a() -> void:",
            "\trows = get_rows() if ok else []",
            "\tother = get_rows() if ok else []",
        ]
    )
    assert _lines(text) == []


def test_flags_one_line_typed_functions() -> None:
    text = "\n".join(
        [
            "func rows() -> Array[int]: return get_rows() if ok else []",
            "func d() -> Dictionary[int, int]: return {} if ok else t()",
            "func plain() -> Array: return get_rows() if ok else []",
            "func fine() -> Array[int]: return get_rows()",
            "var f := func() -> Array[int]: return t() if ok else []",
        ]
    )
    assert _lines(text) == [1, 2, 5]


def test_typed_names_resolve_by_scope() -> None:
    text = "\n".join(
        [
            "var rows: Array[int] = []",
            "",
            "",
            "func a() -> void:",
            "\tvar cells: Array[int] = []",
            "\tcells = t() if ok else []",
            "",
            "",
            "func b() -> void:",
            "\tvar cells: Variant = null",
            "\tcells = t() if ok else []",
            "\tvar rows = null",
            "\trows = t() if ok else []",
            "\tself.rows = t() if ok else []",
            "",
            "",
            "func c(rows, cells: Array[int]) -> void:",
            "\trows = t() if ok else []",
            "\tcells = t() if ok else []",
            "\tvar f := func():",
            "\t\tcells = t() if ok else []",
        ]
    )
    assert _lines(text) == [6, 14, 19, 21]


def test_mark_on_a_function_header_keeps_its_return_type() -> None:
    text = "\n".join(
        [
            "func a(",
            "\tx: int,",
            ") -> Array[int]:  # typed-ternary-ok: header only",
            "\treturn t() if ok else []",
        ]
    )
    assert _lines(text) == [4]


def test_a_clean_project_passes_and_a_planted_site_fails(tmp_path: Path, capsys) -> None:
    root = _project(tmp_path, {"scripts/a.gd": GOOD, "test/test_a.gd": GOOD})
    assert chk.main([], root=root) == 0
    assert "typed ternaries: ok (2 files)" in capsys.readouterr().out
    (root / "scripts" / "a.gd").write_text(BAD, encoding="utf-8")
    assert chk.main([], root=root) == 1
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "scripts/a.gd:2: untyped [] / {} in a ternary on a typed var `names`"
    assert out[1].startswith("1 site(s).")


def test_the_scan_follows_kit_json_not_a_folder_list(tmp_path: Path, capsys) -> None:
    kit = {"source": {"exclude": ["addons", "vendor"], "include": ["addons/mine"]}}
    files = {
        "anywhere/deep/a.gd": BAD,
        "addons/mine/b.gd": BAD,
        "addons/theirs/c.gd": BAD,
        "vendor/d.gd": BAD,
        ".godot/e.gd": BAD,
    }
    assert chk.main([], root=_project(tmp_path, files, kit)) == 1
    out = capsys.readouterr().out
    assert "addons/mine/b.gd:2" in out and "anywhere/deep/a.gd:2" in out
    assert "theirs" not in out and "vendor" not in out and ".godot" not in out


def test_no_script_at_all_is_a_failure_not_a_pass(tmp_path: Path, capsys) -> None:
    assert chk.main([], root=_project(tmp_path, {"readme.txt": ""})) == 1
    assert "no .gd file to scan" in capsys.readouterr().err


def test_paths_narrow_the_run_and_reach_excluded_folders(tmp_path: Path, capsys) -> None:
    root = _project(tmp_path, {"scripts/a.gd": BAD, "ui/b.gd": GOOD, "addons/x/c.gd": BAD})
    assert chk.main(["ui"], root=root) == 0
    capsys.readouterr()
    assert chk.main(["addons/x", "ui/b.gd"], root=root) == 1
    assert "addons/x/c.gd:2" in capsys.readouterr().out
    assert chk.main(["nowhere"], root=root) == 2
    assert "does not exist" in capsys.readouterr().err


def test_the_allowlist_exempts_files_and_folders_and_must_not_go_stale(
    tmp_path: Path, capsys
) -> None:
    files = {
        "scripts/a.gd": BAD,
        "generated/b.gd": BAD,
        ALLOWLIST: "# why\nscripts/a.gd | load_names() returns an untyped array\n"
        "generated/ | written by the exporter\n",
    }
    root = _project(tmp_path, files)
    assert chk.main([], root=root) == 0
    capsys.readouterr()
    (root / "scripts" / "a.gd").write_text(GOOD, encoding="utf-8")
    assert chk.main([], root=root) == 1
    out = capsys.readouterr().out
    assert "stale entry (nothing left to flag), remove it: scripts/a.gd" in out
    assert "generated/" not in out
    assert chk.main(["generated"], root=root) == 0  # a narrowed run cannot judge the list


def test_an_allowlist_entry_without_a_reason_is_an_error(tmp_path: Path, capsys) -> None:
    root = _project(tmp_path, {"scripts/a.gd": BAD, ALLOWLIST: "scripts/a.gd\n"})
    assert chk.main([], root=root) == 2
    assert "needs `| reason`" in capsys.readouterr().err
