"""tools/visual_diff/compare.py: metrics, the noise mask, the files it writes, exit codes."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

Image = pytest.importorskip("PIL.Image", reason="Pillow is in the dev group: run uv sync")

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
from visual_diff import compare as visual  # noqa: E402

GREY = (20, 40, 60)
STRICT = {"pixel_threshold": 0, "max_rms": 0.0, "max_changed_fraction": 0.0}


def _png(path: Path, dots: dict[tuple[int, int], tuple[int, int, int]], size=(8, 8)) -> Path:
    image = Image.new("RGB", size, GREY)
    for xy, colour in dots.items():
        image.putpixel(xy, colour)
    image.save(path)
    return path


def test_identical_images_pass_and_the_review_files_are_written(tmp_path: Path) -> None:
    baseline = _png(tmp_path / "baseline.png", {})
    current = _png(tmp_path / "current.png", {})
    report = visual.compare(baseline, current, tmp_path / "out", **STRICT)
    assert report["ok"] is True
    assert report["metrics"] == {
        "rms": 0.0,
        "changed_pixels": 0,
        "changed_fraction": 0.0,
        "pixel_threshold": 0,
        "noise_pixels": 0,
        "noise_fraction": 0.0,
    }
    assert "error" not in report
    for name in ("absolute_difference.png", "heatmap.png", "contact_sheet.png"):
        assert (tmp_path / "out" / name).is_file()
    assert Image.open(tmp_path / "out" / "contact_sheet.png").size == (24, 8)


def test_one_changed_pixel_fails_strict_limits_with_exact_metrics(tmp_path: Path) -> None:
    baseline = _png(tmp_path / "baseline.png", {}, size=(10, 10))
    current = _png(tmp_path / "current.png", {(3, 3): (120, 40, 60)}, size=(10, 10))
    report = visual.compare(baseline, current, tmp_path / "out", **STRICT)
    assert report["ok"] is False
    assert report["metrics"]["changed_pixels"] == 1
    assert report["metrics"]["changed_fraction"] == 0.01
    # One channel of one pixel off by 100, over 300 channel values.
    assert report["metrics"]["rms"] == pytest.approx((100**2 / 300) ** 0.5, abs=1e-6)


def test_a_change_in_blue_alone_counts(tmp_path: Path) -> None:
    # Blue is a ninth of a grey value: weighted to grey, a blue step of 5 would be 1 and
    # stay under the threshold. The largest channel difference is 5.
    baseline = _png(tmp_path / "baseline.png", {})
    current = _png(tmp_path / "current.png", {(1, 1): (20, 40, 65)})
    report = visual.compare(
        baseline, current, tmp_path / "out", pixel_threshold=4, max_rms=99, max_changed_fraction=0
    )
    assert report["metrics"]["changed_pixels"] == 1 and report["ok"] is False
    report = visual.compare(
        baseline, current, tmp_path / "out", pixel_threshold=5, max_rms=99, max_changed_fraction=0
    )
    assert report["metrics"]["changed_pixels"] == 0 and report["ok"] is True


def test_each_limit_fails_on_its_own(tmp_path: Path) -> None:
    baseline = _png(tmp_path / "baseline.png", {})
    current = _png(tmp_path / "current.png", {(0, 0): (255, 255, 255)})
    loose = {"pixel_threshold": 4, "max_rms": 99.0, "max_changed_fraction": 1.0}
    assert visual.compare(baseline, current, tmp_path / "out", **loose)["ok"] is True
    assert (
        visual.compare(baseline, current, tmp_path / "out", **(loose | {"max_rms": 1.0}))["ok"]
        is False
    )
    tight = loose | {"max_changed_fraction": 0.01}
    assert visual.compare(baseline, current, tmp_path / "out", **tight)["ok"] is False


def test_different_sizes_never_pass(tmp_path: Path) -> None:
    baseline = _png(tmp_path / "baseline.png", {})
    current = _png(tmp_path / "current.png", {}, size=(8, 9))
    report = visual.compare(baseline, current, tmp_path / "out", **STRICT)
    assert report["ok"] is False and report["error"] == "size_mismatch"
    assert report["baseline_size"] == [8, 8] and report["other_sizes"] == [[8, 9]]


def test_noise_captures_mask_what_moves_on_its_own(tmp_path: Path) -> None:
    white = (255, 255, 255)
    baseline = _png(tmp_path / "baseline.png", {})
    flicker_a = _png(tmp_path / "a2.png", {(1, 1): white})
    flicker_b = _png(tmp_path / "a3.png", {(2, 2): (20, 40, 61)})
    same = _png(tmp_path / "same.png", {(1, 1): white, (2, 2): white})  # only where it flickers
    real = _png(tmp_path / "real.png", {(1, 1): white, (5, 6): white})
    noise = [flicker_a, flicker_b]
    report = visual.compare(baseline, same, tmp_path / "out", noise_paths=noise, **STRICT)
    assert report["ok"] is True and report["metrics"]["noise_pixels"] == 2
    report = visual.compare(baseline, real, tmp_path / "out", noise_paths=noise, **STRICT)
    assert report["ok"] is False and report["metrics"]["changed_pixels"] == 1
    # The fraction is over the pixels that were compared.
    assert report["metrics"]["changed_fraction"] == pytest.approx(1 / 62, abs=1e-8)
    # Without the noise captures the flicker itself fails.
    assert visual.compare(baseline, same, tmp_path / "out", **STRICT)["ok"] is False


def test_noise_pad_grows_the_mask_without_wrapping_at_the_edge(tmp_path: Path) -> None:
    white = (255, 255, 255)
    baseline = _png(tmp_path / "baseline.png", {})
    corner = _png(tmp_path / "a2.png", {(0, 0): white})
    beside = _png(tmp_path / "beside.png", {(1, 1): white})  # a pixel from the noise
    far = _png(tmp_path / "far.png", {(7, 7): white})  # the opposite corner
    options = {"noise_paths": [corner], "noise_pad": 1, **STRICT}
    report = visual.compare(baseline, beside, tmp_path / "out", **options)
    assert report["ok"] is True and report["metrics"]["noise_pixels"] == 4
    assert visual.compare(baseline, far, tmp_path / "out", **options)["ok"] is False
    unpadded = {"noise_paths": [corner], **STRICT}
    assert visual.compare(baseline, beside, tmp_path / "out", **unpadded)["ok"] is False


def test_a_noise_mask_over_the_whole_image_never_passes(tmp_path: Path, capsys) -> None:
    # Every pixel masked: nothing was compared, so "0 changed pixels" is no pass.
    baseline = _png(tmp_path / "baseline.png", {})
    black = tmp_path / "black.png"
    Image.new("RGB", (8, 8), (0, 0, 0)).save(black)
    loose = {"pixel_threshold": 4, "max_rms": 99.0, "max_changed_fraction": 1.0}
    for limit in (0.5, 1.0):
        report = visual.compare(
            baseline,
            black,
            tmp_path / "out",
            noise_paths=[black],
            max_noise_fraction=limit,
            **loose,
        )
        assert report["ok"] is False and report["error"] == "nothing_compared"
        assert report["metrics"]["noise_fraction"] == 1.0
    arguments = [str(baseline), str(black), "--output-dir", str(tmp_path / "out")]
    assert visual.main([*arguments, "--noise", str(black)]) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "nothing_compared"


def test_a_noise_mask_over_most_of_the_image_fails_unless_allowed(tmp_path: Path) -> None:
    white = (255, 255, 255)
    baseline = _png(tmp_path / "baseline.png", {}, size=(10, 10))
    half = {(x, y): white for x in range(10) for y in range(6)}  # 60 of 100 pixels move
    moving = _png(tmp_path / "moving.png", half, size=(10, 10))
    options = {"noise_paths": [moving], **STRICT}
    report = visual.compare(baseline, baseline, tmp_path / "out", **options)
    assert report["ok"] is False and report["error"] == "noise_mask_too_large"
    assert report["metrics"]["noise_fraction"] == 0.6
    assert report["thresholds"]["max_noise_fraction"] == 0.5
    allowed = visual.compare(
        baseline, baseline, tmp_path / "out", max_noise_fraction=0.6, **options
    )
    assert allowed["ok"] is True and "error" not in allowed


def test_the_command_refuses_limits_that_make_no_sense(tmp_path: Path, capsys) -> None:
    baseline = _png(tmp_path / "baseline.png", {})
    arguments = [str(baseline), str(baseline), "--output-dir", str(tmp_path / "out")]
    for flag, value in (
        ("--max-noise-fraction", "1.5"),
        ("--pixel-threshold", "256"),
        ("--noise-pad", "-1"),
    ):
        with pytest.raises(SystemExit) as stopped:
            visual.main([*arguments, flag, value])
        assert stopped.value.code == 2 and flag in capsys.readouterr().err


def test_the_command_writes_a_report_and_exits_by_result(tmp_path: Path, capsys) -> None:
    baseline = _png(tmp_path / "baseline.png", {})
    changed = _png(tmp_path / "changed.png", {(3, 3): (255, 255, 255)})
    out = tmp_path / "out"
    assert visual.main([str(baseline), str(baseline), "--output-dir", str(out)]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed == json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert printed["ok"] is True
    strict = ["--output-dir", str(out), "--max-changed-fraction", "0"]
    assert visual.main([str(baseline), str(changed), *strict]) == 1
    assert json.loads((out / "report.json").read_text(encoding="utf-8"))["ok"] is False
    capsys.readouterr()
    assert visual.main([str(baseline), str(tmp_path / "missing.png"), *strict]) == 2
    assert "cannot read image" in capsys.readouterr().err
    (tmp_path / "text.png").write_text("not an image", encoding="utf-8")
    assert visual.main([str(baseline), str(tmp_path / "text.png"), *strict]) == 2


def test_output_under_artifacts_is_hidden_from_godot(tmp_path: Path) -> None:
    config = visual.kit_config.load(tmp_path)
    visual.keep_out_of_godot(tmp_path / "artifacts" / "visual" / "menu", config)
    assert (tmp_path / "artifacts" / ".gdignore").is_file()
    visual.keep_out_of_godot(tmp_path / "elsewhere" / "menu", config)
    assert not (tmp_path / "elsewhere").exists()
