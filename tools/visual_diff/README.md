# Visual diff

`compare.py` compares a screenshot with a baseline of the same size.

```powershell
uv run python tools/visual_diff/compare.py baselines/menu.png artifacts/shots/menu.png `
  --output-dir artifacts/visual/menu --max-rms 2 --max-changed-fraction 0.01
```

| Output | What |
|---|---|
| exit code | 0 pass; 1 differ, sizes differ, noise mask too large or nothing left to compare; 2 an image is missing or unreadable |
| `report.json` (also printed) | `ok`; `metrics` (`rms`, `changed_pixels`, `changed_fraction`, `noise_fraction`); `thresholds`; `artifacts` (the image paths); `resolution`; `error` when the failure is not a metric |
| `contact_sheet.png` | baseline, current, heatmap side by side |
| `heatmap.png` | the difference at full contrast |
| `absolute_difference.png` | the raw difference |

| Flag | Default | Meaning |
|---|---|---|
| `--max-rms` | 2.0 | limit on the RMS channel difference (0-255) |
| `--max-changed-fraction` | 0.01 | limit on the share of changed pixels |
| `--pixel-threshold` | 4 | a pixel is changed when a channel differs by more than this |
| `--noise A.png B.png` | none | more captures of the unchanged scene; pixels that differ among them are ignored |
| `--noise-pad N` | 0 | grow that noise mask by N pixels |
| `--max-noise-fraction` | 0.5 | fail when the noise mask hides more of the image than this |

## A baseline you can trust

A pixel diff only means something when the two captures were made the same way. Pin:
Godot version, renderer, window size, random seed, camera, and the frame you capture
on (wait a fixed number of frames, not a time). Capture on the same GPU and driver, or
loosen the limits. Capture the scene twice before you approve a baseline: if the two
differ, pass the second as `--noise`, or the diff will fail on its own.

A passing diff says the picture did not change, not that it is right. Pair it with a
test that asserts what the scene contains.
