# PNG Vector — local PNG → editable vector EPS

A runnable Python desktop application and batch CLI. All image processing stays on your computer; no accounts, image uploads, paid APIs, fonts substituted for lettering, or background removal. EPS output is filled vector geometry, never an embedded PNG. Smooth reconstruction is **not lossless**. Always inspect the final EPS preview, particularly lettering, tiny marks, holes and colour boundaries.

Intel Big Sur users: use `scripts/setup-macports.sh` and `requirements-macos11.txt`; the main Qt pin requires macOS 12+. Mac users: start with [MAC_START_HERE.md](MAC_START_HERE.md) and the included macOS setup/launch scripts. Native macOS testing is still outstanding.

## Install and launch

Tested on **Debian 13 x86-64, Python 3.12**. Python 3.11–3.13 is supported by the declared dependencies; other Python versions, macOS and Windows have not been tested here.

On Debian/Ubuntu, install system dependencies first:

```sh
sudo apt-get update
sudo apt-get install -y python3-venv ghostscript potrace libcairo2 libxcb-cursor0 libxkbcommon-x11-0
cd Converter
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pngvector.doctor
.venv/bin/python -m pngvector
```

Or, after system packages are installed, run `sh scripts/bootstrap.sh`. `requirements.txt` pins all installed Python dependencies, including tests. Ghostscript 10.05.1, Potrace 1.16 and Cairo 1.18.4 were actually exercised. VTracer is pinned to 0.6.12; startup diagnostics print its installed function signature. Potrace's actual `--help` options were checked before use.

**This prepared cloud workspace:**

```sh
cd /workspace/Converter
export PATH=/workspace/.local/usr/bin:$PATH
.venv/bin/python -m pngvector.doctor
.venv/bin/python -m pngvector
```

A graphical session is needed to see the desktop window. The cloud test environment has no interactive display; GUI conversion was exercised with Qt’s offscreen platform; Xvfb could not bind display sockets in this sandbox. A local installation on your computer supplies the normal desktop window. Do not set `QT_QPA_PLATFORM=offscreen` for normal use.

For an untested macOS installation, Homebrew provides `ghostscript`, `potrace` and `cairo`; for untested Windows, install Ghostscript (`gswin64c`), Potrace and a Cairo runtime, add the executables/runtime libraries to PATH, create a Python venv, and use `.venv\Scripts\python`. System/runtime availability must be verified with `pngvector.doctor`. No standalone packaged executable is supplied.

## Use the desktop application

1. Add one or more PNGs with the file picker or drag files onto the window.
2. Select an output folder. Choose **Smooth trace** (default) or explicitly choose **Exact pixel grid**.
3. Keep **Preserve detail** defaults for the first conversion: full colour precision, zero smoothing, no component removal. These prioritize detail over compact files; zero smoothing retains unsimplified contour boundaries instead of rounding corners.
4. Convert the queue. Progress, cancellation, error messages and large-job confirmations are provided. A failed file does not prevent later batch files from being processed.
5. Inspect synchronized original/converted views, pan and zoom above 100%, blend the overlay and toggle the amplified difference view. The EPS preview is generated **from the final EPS with Ghostscript**, not from its intermediate SVG. Substantial differences produce a warning; a low difference score is never proof that important details survived.

Every conversion gets a new, uniquely named subdirectory such as `drawing-smooth/`, then `drawing-smooth-2/`. Existing files are never overwritten. Input files are opened read-only and SHA-256 checked before/after. Failed/cancelled conversions remove their own incomplete output directory.

Each result includes EPS, editable intermediate SVG, source comparison PNG, final-output preview PNG, difference PNG and JSON report with dimensions, source hash, options, vector audit, warnings and measurements. Pixel mode also includes a rectangle manifest. These PNGs are sidecar previews, **not embedded in the EPS**. See `docs/screenshot.png` for an actual running conversion window.

## The two modes

| Mode | Geometry | Intended use / limits |
|---|---|---|
| Smooth trace | Established VTracer colour tracing, or Potrace masks for exact black/white | Editable contours. Approximation, especially gradients, photographs, antialiasing and closely spaced features. More smoothing can round corners or erase detail. |
| Exact pixel grid | Filled vector rectangles, merging identical horizontal runs vertically | Preserves decoded 8-bit sRGB pixel coverage and colour numerically. Retains stair-stepped edges; **does not recover smooth original artwork**. Photographs can create enormous EPS files. |

Automatic engine selection uses Potrace only for exact black/white artwork with binary alpha. Antialiased grayscale and coloured images use VTracer; nothing is silently thresholded to black and white. Explicit Potrace selection rejects unsuitable inputs. Both black and white opaque areas are traced so white artwork stays present. Lettering always becomes geometry, not text/font references. Fully transparent pixels become unpainted areas.

VTracer uses cutout colour regions, maximum channel precision, zero speckle removal, layer difference zero and explicit path precision. There is no denoising, sharpening, font redraw or automatic colour-background removal. Tracer-specific approximation can still alter shapes; seams and small features require inspection. The controls are:

- **Detail preservation:** lower length/optimization tolerances at higher settings when smoothing is enabled. With zero smoothing VTracer keeps all contour vertices. Effects depend on tracing engine; this cannot reconstruct missing source detail.
- **Curve smoothing:** zero retains all boundary vertices in VTracer and sharp corners in Potrace; positive settings enable smoothing. Higher settings can change fine geometry.
- **Colour precision:** 8 bits/channel by default; lower values merge colours in VTracer and can lose fidelity.
- **Small-component removal:** zero disables destructive cleanup; nonzero engine-specific speckle settings may remove dots/punctuation or merge details (Potrace uses pixel area; VTracer uses its native filter setting). These values are not a common cross-engine area threshold. Disabled in exact-pixel mode.

Lower path counts and smaller files can reduce fidelity. Size estimates are content-dependent bounds, not promises. Jobs over the soft pixel limit need explicit confirmation; a hard memory guard rejects oversized inputs. Cancel stops tracing/export subprocesses and checks rectangle loops. Decoding and some comparison operations are not interruptible mid-call.

## Transparency and colour

**Binary transparency:** completely transparent areas, including enclosed holes, remain unpainted; no white background is added. The preview uses the selected comparison background, which does not add artwork to the EPS.

**Partial transparency:** EPS has no general alpha. The default refuses to proceed until you choose:

- **Composite onto a selected colour → EPS.** All alpha is explicitly composited in sRGB byte space against that colour. This changes the artwork and makes the whole original canvas opaque.
- **Retain alpha → SVG + PDF, no EPS.** This explicitly selected alternative uses pixel-vector rectangles with per-shape opacity, retaining alpha rather than silently thresholding it. It is labelled as pixel geometry even if smooth mode was selected. Its preview is rendered from the transparency-preserving SVG, not presented as an EPS preview.

Never assume a translucent PNG can yield a visually identical EPS on every background. There is no automatic white flattening or raster fallback.

Embedded RGB/grayscale ICC profiles are converted using Pillow/LittleCMS to **8-bit sRGB**, relative colorimetric intent, preserving alpha. Malformed/incompatible profiles are rejected. Untagged PNGs are assumed sRGB; gAMA/cHRM without ICC produce a warning because this version does not implement an independent chunk-based colour transform. Convert such sources explicitly to tagged sRGB when colour accuracy matters. EPS stores DeviceRGB numerical colour values; an EPS viewer/printer's colour policy can change appearance. This is not a CMYK/prepress colour-separation workflow.

Supported: static 1/2/4/8-bit PNG inputs, including palette transparency, decoded to 8-bit RGBA. Sub-8-bit indexed/grayscale values are expanded without throwing away their discrete values. **16-bit channels are explicitly rejected**, rather than silently reduced. APNG, un-applied EXIF orientation and unequal x/y DPI metadata are rejected with guidance. An explicit DPI override can resolve unequal DPI while preserving square pixel proportions.

## Canvas and physical size

The full original pixel canvas, including wide empty margins, is retained. No ink-based crop is applied. The EPS integer `BoundingBox` encloses the canvas and `HiResBoundingBox` is explicitly set to:

```
0 0 (PNG width × 72 / DPI) (PNG height × 72 / DPI) points
```

The PNG's equal x/y physical DPI is used when present; otherwise **96 DPI**. A 960 × 480 image at 96 DPI is 720 × 360 points (10 × 5 inches). A DPI override changes physical size, not pixel placement or proportions. PNG physical-resolution integers can round metadata slightly. Coordinate transforms preserve top-left PNG orientation in EPS's bottom-left coordinate system. Ghostscript previews use the original width/height and matching DPI with an explicit page, rather than trusting a viewer's rounded ink bounds.

## Batch CLI and tests

```sh
# Default conservative tracing; spaces and Unicode are supported.
.venv/bin/python -m pngvector.cli 'image with spaces.png' other.png -o converted
.venv/bin/python -m pngvector.cli drawing.png -o converted --mode pixel
.venv/bin/python -m pngvector.cli alpha.png -o converted --alpha composite --background '#203050'
.venv/bin/python -m pngvector.cli alpha.png -o converted --alpha retain
.venv/bin/python -m pngvector.cli large.png -o converted --estimate-only
# Use --allow-large only after reviewing the estimate.
.venv/bin/python -m pytest -q
# Generate fresh inspectable fixtures, final EPS renders, and JSON evidence:
.venv/bin/python tests/evidence.py --output evidence
```

The tests exercise actual EPS conversion/rendering, bounds, orientation, input hashes, holes, lettering, tiny components, adjacent colours, binary/partial alpha, gradients, a public-domain photograph, wide margins, blank/corrupted/large inputs, ICC, bit depth, spaces and Unicode. Exact mode is independently reconstructed from **actual EPS painting commands**, tracking colour state, then compared to source coverage and channel values without renderer antialiasing.

EPS raster detection executes Ghostscript with `image`, `colorimage`, and `imagemask` instrumented in `systemdict`, covering bound aliases and compressed exporter output. Negative bitmap EPS fixtures must be detected. This is an audit of **locally generated** EPS, not a security proof for arbitrary hostile PostScript. Ghostscript runs with `SAFER`; keep your system Ghostscript patched. Never open untrusted EPS with an outdated interpreter.

See `TEST_RESULTS.md` and `evidence/report.json` for actual results and limitations, rather than treating the screenshots or similarity measurements as evidence alone.
