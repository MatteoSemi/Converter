# Actual validation — 28 October 2026

Tested: Debian GNU/Linux 13 x86-64, Python 3.12.14. Qt GUI tested with its offscreen platform, including real worker conversions and modal choices. Native desktop display/input integration, Windows and macOS were **not** verified. Xvfb could not bind a display socket under this execution sandbox.

## Automated run

```sh
PATH=/workspace/.local/usr/bin:$PATH QT_QPA_PLATFORM=offscreen \
  .venv/bin/python -m pytest tests -q --tb=short --junitxml=evidence/pytest.xml
```

**58 passed, 0 failed, 0 skipped, 21.86 seconds.** Six are desktop GUI tests. After a final label-only clarification of engine-specific cleanup units, the six GUI tests were rerun successfully. [JUnit results](evidence/pytest.xml).

Checked end-to-end: EPS headers and explicit full-canvas boxes, Ghostscript interpretation and matching-size rendering, correct orientation/placement, input SHA-256 hashes, counters/punctuation/one-pixel components, adjacent colours/nested holes, binary alpha revealing a nonwhite background, default partial-alpha refusal, explicit composite and retained-alpha exports, gradients/photo, wide margins, blank/fully transparent/corrupted/large images, colour profiles and unsupported 16-bit input, Unicode/spaces/percent filenames, no overwrite, and active cancellation cleanup. The GUI tests exercise real conversions, batch failure recovery, synchronized zoom/overlay/difference, large-job cancellation, and both alpha modal choices, including delayed alpha discovery on a large job.

The EPS raster audit executes instrumented Ghostscript, covering normal and dictionary `image`, `imagemask`, `colorimage`, bound aliases and actual Cairo-generated embedded-bitmap EPS. The negative cases are correctly rejected as raster. Exact-pixel tests independently interpret emitted EPS colour-state and rectangle commands, verify the global coordinate transform, and compare full pixel coverage and channel values independently of antialiasing. Retained SVG rectangles are also independently checked for RGBA values; `pdfimages -list` reports no raster images in the retained-alpha PDF.

## Inspectable conversions

```sh
PATH=/workspace/.local/usr/bin:$PATH \
  .venv/bin/python tests/evidence.py --output evidence
```

**24 successful conversions:** 22 ordinary EPS conversions across two modes, one explicitly composited EPS, and one explicitly selected SVG/PDF alternative. All 24 input hashes matched before/after. The 23 EPS outputs passed the executable vector-only audit. See [machine-readable report](evidence/report.json) and the per-conversion JSON reports, EPS/SVG files and comparison PNGs in `evidence/`.

Selected default traced-contour measurements, from **final EPS renders**:

| Fixture | Mean absolute RGB error /255 | Pixels with any channel error >8/255 |
|---|---:|---:|
| Black/white lettering (Potrace) | 0.7603 | 0.95% |
| Thin lines, corners, isolated dots | 0 | 0% |
| Adjacent colours and nested holes | 0 | 0% |
| Wide canvas margins | 0 | 0% |
| Gradient | 9.8477 | 83.51% |
| Photograph | 17.8326 | 55.82% |

The gradient and photograph are **substantially different** in traced mode and are flagged. They demonstrate a real limitation rather than a claim of lossless smooth vectorization. Default tracing uses unsimplified boundaries to retain small details, but VTracer's colour clustering still changes these continuous-tone sources. Exact pixel mode preserves their decoded sRGB pixel grid instead: all 11 ordinary pixel-mode fixture renders had MAE 0, and independent rectangle-coverage tests pass. These small fixtures do not establish identical results for every PNG or every EPS viewer.

## Independent large/cancellation/import checks

[Independent audit](evidence/independent-audit.json):

- Confirmed 4096 × 3072 (12,582,912-pixel) solid image converted in exact mode to one merged vector rectangle, rendered at its original dimensions, MAE 0. This tests a large low-complexity image; it does **not** establish practical resource use for a 12-megapixel photograph.
- In-flight exact conversion cancelled in 0.529 seconds after entering geometry construction; no incomplete outputs remained.
- An EPS imported into another PostScript program left the caller's graphics transform unchanged.
- Retained-alpha PDF lists zero raster images.
- Actual CLI tests: Unicode filename exact export, positive smoothing colour tracing, alpha-retaining export, corrupt input exit code 1, and SIGINT during a large conversion exit code 130 with unchanged source hash and no partial EPS.
- `scripts/bootstrap.sh` was executed against the prepared environment successfully; `pngvector.doctor` verified every required runtime. Installation into an entirely fresh operating system was not independently repeated.

Two fidelity defects discovered during visual inspection were repaired and regression-tested: fractional-point canvas-height alignment in Cairo EPS, and VTracer polygon simplification erasing one-pixel components. The Preserve detail default now uses VTracer's unsimplified contour fitting (`mode=none`); smoothing remains optional and explicitly warns about small-feature loss.

## Remaining limits

No mathematical smooth-vector reconstruction guarantee; no native partial alpha in EPS; no portable embedded ICC colour policy in EPS; 16-bit/APNG/unapplied orientation and ambiguous DPI inputs require explicit preparation. Large detailed images can be expensive and are subject to estimates, confirmation and a 16-million-pixel hard limit. Some decode/comparison operations cannot stop mid-call. Smooth-mode colours and geometry can differ even at full colour precision. Use final-output previews and the exact pixel alternative where those tradeoffs matter.

## Intel Big Sur compatibility follow-up

The original Qt6.9.3 wheel requires macOS12+, so it cannot install on Big Sur. Added an explicit macOS11 dependency profile using Qt6.6.3.1 and a MacPorts system-package setup script. The official wheel metadata for Qt6.6.3.1 targets macOS11 universal2 (including Intel); VTracer/Pillow/cffi also supply compatible Intel wheels.

All pinned dependencies in `requirements-macos11.txt` were successfully resolved and downloaded for **CPython3.12 / macOS11.0 / x86_64**, with binary wheels only. Filenames/hashes and method: `evidence/bigsur-dependency-check.json`. In a separate **Linux** venv using that dependency profile, the full suite passed **58 tests in24.78s**; results: `evidence/pytest-qt66-linux.xml`. Shell syntax checks passed for the setup/launch scripts. These are compatibility checks, not proof of installation or native GUI operation on macOS. MacPorts installation and its system packages still require validation on the user's Mac.
