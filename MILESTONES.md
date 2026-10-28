# Implementation checkpoints

## 1 — Environment and design
- Original repository is empty; no existing application was replaced.
- Python 3.12 / Debian 13 x86-64 development environment.
- Pinned VTracer 0.6.12, Pillow 11.3.0, CairoSVG 2.8.2, PySide6 6.9.3 installed.
- Ghostscript 10.05.1 and Cairo 1.18.4 available.
- Pipeline, GUI, and reproducible end-to-end fixtures are under development in separate modules.
- Explicit partial alpha choice: composite to selected colour for EPS, or pixel-vector SVG/PDF with transparency and no misleading EPS.
- No source images uploaded, no remote inference or account service used. Network is only for dependency installation.

## 2 — Working conversion pipeline
- Real VTracer/Potrace contours → Cairo EPS, and separate merged-rectangle exact-pixel EPS.
- Explicit alpha choices, ICC conversion/rejection, DPI/full-canvas coordinates, no overwrite, source SHA-256 protection.
- Ghostscript final EPS preview and executable raster-paint audit; negative bitmap fixtures detected.
- Fixed fractional-point canvas alignment and isolated one-pixel contour collapse after inspecting real enlarged output.

## 3 — Desktop and reproducible validation complete
- PySide6 local desktop UI, single/batch worker conversions, drop/picker/output selector, cancellation, fidelity controls, synchronized preview and comparison.
- 58 automated tests pass; 24 conversions in evidence/report.json; independent large/cancel/PDF checks in evidence/independent-audit.json.
- Real offscreen application screenshot in docs/screenshot.png. No native desktop or other OS verification claimed.
- TEST_RESULTS.md records actual differences, including substantial gradient/photo tracing loss.
- Repeated scripts/bootstrap.sh successfully; install_script and start_skill saved in environment configuration draft. Publication remains a user action in environment settings.
- Files are saved locally; nothing was pushed to GitHub.

## 4 — Mac handoff preparation
- Added MAC_START_HERE.md, scripts/setup-mac.sh, and Launch PNG Vector.command.
- macOS scripts target Homebrew Python 3.12 and Cairo runtime paths; shell syntax verified with bash -n on Linux, not run on macOS.
- Prepared and integrity-checked dist/PNGVector-Mac-source.zip.
- Cloud publication still reports a generic UI failure; runtime tools expose no publication cause. Workspace file links are not downloadable attachments in this client. GitHub transfer requires the user's approval to push the implementation to the existing repository.

## 5 — Approved GitHub delivery
- User explicitly approved pushing the completed project to MatteoSemi/Converter.
- Preparing the source, test fixtures, evidence, and Mac setup scripts as the initial main-branch commit. Dependencies/virtual environments and generated distribution ZIPs remain excluded from Git.

## 6 — Intel macOS 11 Big Sur setup route
- Added MacPorts setup and a separate pinned Qt6.6.3.1 profile because the original Qt6.9.3 wheel requires macOS12+.
- Verified every pinned Python dependency resolves to a compatible CPython3.12 Intel Big Sur wheel; recorded filenames and hashes.
- Full58-test suite passes under the older Qt profile on Linux; native Mac testing remains outstanding.
