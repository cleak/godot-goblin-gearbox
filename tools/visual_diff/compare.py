#!/usr/bin/env python3
"""Compare a screenshot with a baseline: metrics, a heatmap, a contact sheet, a JSON report.

    uv run python tools/visual_diff/compare.py baseline.png current.png \
        --output-dir artifacts/visual/menu --max-rms 2 --max-changed-fraction 0.01

Two metrics, both over RGB (alpha is dropped):

- rms: root mean square of the per-channel difference, 0..255. Catches a faint change
  spread over the whole image.
- changed_fraction: the share of pixels where some channel differs by more than
  `--pixel-threshold`. Catches a strong change in a small area.

The comparison passes when both are within their limits. Images of different sizes
never pass.

Animated parts (flames, particles, a cursor) differ between two captures of the same
scene. Give extra captures of the unchanged scene with `--noise`: every pixel that
differs between the baseline and any of them is left out of both metrics.
`--noise-pad N` widens that mask by N pixels, for an edge that moves by a pixel.
A mask that hides more than `--max-noise-fraction` of the image (default 0.5) fails
the comparison: with most of the picture left out, "no difference" says nothing.

Written to --output-dir: absolute_difference.png, heatmap.png (the difference,
stretched to full contrast), contact_sheet.png (baseline | current | heatmap) and
report.json (also printed; `error` names a failure that is not a metric). Under the
project's artifacts/ folder a .gdignore is added, so Godot does not import the images.

Exit 0 pass, 1 differ (or sizes differ, or the noise mask is too large), 2 an image is
missing or unreadable.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter, ImageOps, UnidentifiedImageError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ci"))
import gate_support  # noqa: E402
import kit_config  # noqa: E402

ARTIFACTS = "artifacts"


def _strongest_channel(difference: Image.Image) -> Image.Image:
    """Per pixel, the largest of the three channel differences (an L image)."""
    red, green, blue = difference.split()
    return ImageChops.lighter(ImageChops.lighter(red, green), blue)


def noise_mask(baseline: Image.Image, captures: list[Image.Image], pad: int = 0) -> Image.Image:
    """An L image, 255 where any capture differs from the baseline, grown by `pad` pixels."""
    mask = Image.new("L", baseline.size, 0)
    for capture in captures:
        moved = _strongest_channel(ImageChops.difference(baseline, capture))
        mask = ImageChops.lighter(mask, moved.point(lambda value: 255 if value else 0))
    if pad > 0:
        mask = mask.filter(ImageFilter.MaxFilter(2 * pad + 1))
    return mask


def compare(
    baseline_path: Path,
    current_path: Path,
    output_dir: Path,
    *,
    pixel_threshold: int,
    max_rms: float,
    max_changed_fraction: float,
    noise_paths: tuple[Path, ...] | list[Path] = (),
    noise_pad: int = 0,
    max_noise_fraction: float = 0.5,
) -> dict[str, object]:
    """Compare two images, write the review images into `output_dir`, return the report."""
    baseline = Image.open(baseline_path).convert("RGB")
    current = Image.open(current_path).convert("RGB")
    captures = [Image.open(path).convert("RGB") for path in noise_paths]
    sizes = {image.size for image in (current, *captures)}
    if sizes != {baseline.size}:
        return {
            "ok": False,
            "error": "size_mismatch",
            "baseline_size": list(baseline.size),
            "other_sizes": sorted(list(size) for size in sizes - {baseline.size}),
        }

    difference = ImageChops.difference(baseline, current)
    pixel_count = baseline.width * baseline.height
    masked_pixels = 0
    if captures:
        mask = noise_mask(baseline, captures, noise_pad)
        masked_pixels = mask.histogram()[255]
        difference.paste((0, 0, 0), mask=mask)
    compared_pixels = pixel_count - masked_pixels
    noise_fraction = masked_pixels / pixel_count
    # A mask over the whole image compares nothing, and nothing never differs.
    error = ""
    if compared_pixels == 0:
        error = "nothing_compared"
    elif noise_fraction > max_noise_fraction:
        error = "noise_mask_too_large"

    # A three-band histogram is three runs of 256 counts: value = index % 256.
    squared_sum = sum(
        (index % 256) ** 2 * count for index, count in enumerate(difference.histogram())
    )
    rms = math.sqrt(squared_sum / max(compared_pixels * 3, 1))
    strongest = _strongest_channel(difference)
    changed_pixels = sum(strongest.histogram()[pixel_threshold + 1 :])
    changed_fraction = changed_pixels / max(compared_pixels, 1)
    passed = not error and rms <= max_rms and changed_fraction <= max_changed_fraction

    output_dir.mkdir(parents=True, exist_ok=True)
    heatmap = ImageOps.autocontrast(strongest).convert("RGB")
    contact = Image.new("RGB", (baseline.width * 3, baseline.height))
    contact.paste(baseline, (0, 0))
    contact.paste(current, (baseline.width, 0))
    contact.paste(heatmap, (baseline.width * 2, 0))
    paths = {
        "absolute_difference": output_dir / "absolute_difference.png",
        "heatmap": output_dir / "heatmap.png",
        "contact_sheet": output_dir / "contact_sheet.png",
    }
    difference.save(paths["absolute_difference"])
    heatmap.save(paths["heatmap"])
    contact.save(paths["contact_sheet"])

    report: dict[str, object] = {
        "ok": passed,
        "baseline": baseline_path.as_posix(),
        "current": current_path.as_posix(),
        "resolution": [baseline.width, baseline.height],
        "metrics": {
            "rms": round(rms, 6),
            "changed_pixels": changed_pixels,
            "changed_fraction": round(changed_fraction, 8),
            "pixel_threshold": pixel_threshold,
            "noise_pixels": masked_pixels,
            "noise_fraction": round(noise_fraction, 8),
        },
        "thresholds": {
            "max_rms": max_rms,
            "max_changed_fraction": max_changed_fraction,
            "max_noise_fraction": max_noise_fraction,
        },
        "artifacts": {name: path.as_posix() for name, path in paths.items()},
    }
    if error:
        report["error"] = error
    return report


def keep_out_of_godot(output_dir: Path, config: kit_config.KitConfig | None = None) -> None:
    """Inside the project's artifacts/ folder, make sure Godot does not scan it."""
    artifacts = (config or kit_config.load()).root / ARTIFACTS
    target = output_dir.resolve()
    if target == artifacts or artifacts in target.parents:
        gate_support.ensure_gdignore(artifacts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare a screenshot with a baseline; exit 1 when they differ too much."
    )
    parser.add_argument("baseline", type=Path)
    parser.add_argument("current", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True, help="where the images go")
    parser.add_argument(
        "--pixel-threshold",
        type=int,
        default=4,
        help="a pixel is changed when a channel differs by more than this (0-255, default 4)",
    )
    parser.add_argument("--max-rms", type=float, default=2.0, help="default 2.0")
    parser.add_argument(
        "--max-changed-fraction", type=float, default=0.01, help="0-1, default 0.01"
    )
    parser.add_argument(
        "--noise",
        type=Path,
        nargs="+",
        default=[],
        metavar="CAPTURE",
        help="more captures of the unchanged scene; pixels that differ among them are ignored",
    )
    parser.add_argument("--noise-pad", type=int, default=0, help="grow the noise by N pixels")
    parser.add_argument(
        "--max-noise-fraction",
        type=float,
        default=0.5,
        help="fail when the noise hides more than this share of the image (0-1, default 0.5)",
    )
    args = parser.parse_args(argv)
    gate_support.plain_output()
    if not 0 <= args.pixel_threshold <= 255:
        parser.error("--pixel-threshold must be between 0 and 255")
    if args.noise_pad < 0:
        parser.error("--noise-pad must be 0 or more")
    if not 0 <= args.max_noise_fraction <= 1:
        parser.error("--max-noise-fraction must be between 0 and 1")

    try:
        keep_out_of_godot(args.output_dir)
    except kit_config.ConfigError as error:
        print(error, file=sys.stderr)
        return 2
    try:
        report = compare(
            args.baseline,
            args.current,
            args.output_dir,
            pixel_threshold=args.pixel_threshold,
            max_rms=args.max_rms,
            max_changed_fraction=args.max_changed_fraction,
            noise_paths=args.noise,
            noise_pad=args.noise_pad,
            max_noise_fraction=args.max_noise_fraction,
        )
    except (OSError, UnidentifiedImageError) as error:
        print(f"cannot read image: {error}", file=sys.stderr)
        return 2
    args.output_dir.mkdir(parents=True, exist_ok=True)
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    (args.output_dir / "report.json").write_text(text, encoding="utf-8", newline="\n")
    print(text, end="")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
