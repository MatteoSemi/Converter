"""Produce inspectable, local conversion evidence (not a substitute for pytest).

Run: .venv/bin/python tests/evidence.py --output evidence
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
from hashlib import sha256
import json
from pathlib import Path
import platform
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageChops, ImageDraw

from generate_fixtures import generate
from pngvector.core import Options, convert
from pngvector.eps import inspect_eps


def digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def version(command):
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=10)
        return (result.stdout or result.stderr).strip().splitlines()[0]
    except (OSError, subprocess.TimeoutExpired, IndexError) as error:
        return str(error)


def comparison(result, output):
    original = Image.open(result.reference).convert("RGB")
    rendered = Image.open(result.preview).convert("RGB")
    diff = ImageChops.difference(original, rendered)
    # Nearest-neighbor enlargement exposes holes, dots and seams clearly.
    factor = 3
    images = [original, rendered, diff]
    sheet = Image.new("RGB", (original.width * factor * 3, original.height * factor + 26), "#dddddd")
    draw = ImageDraw.Draw(sheet)
    for index, (image, label) in enumerate(zip(images, ["Reference", "Final EPS / export preview", "Absolute difference"])):
        x = index * original.width * factor
        sheet.paste(image.resize((image.width * factor, image.height * factor), Image.Resampling.NEAREST), (x, 26))
        draw.text((x + 5, 7), label, fill="black")
    sheet.save(output)


def run(output):
    output.mkdir(parents=True, exist_ok=True)
    fixture_paths = generate(output / "fixtures")
    import PIL
    from importlib.metadata import version as package_version
    report = {
        "platform": platform.platform(),
        "python": sys.version,
        "versions": {name: package_version(name) for name in ["Pillow", "vtracer", "CairoSVG", "PySide6", "pytest"]},
        "ghostscript": version(["gs", "--version"]),
        "potrace": version(["potrace", "--version"]),
        "notes": [
            "Differences are measured on the final EPS render using matching canvas and background.",
            "A similarity number alone does not prove preservation of lettering, holes or fine details.",
            "Photograph input is a bundled public-domain US Navy Grace Hopper portrait thumbnail.",
        ],
        "conversions": [],
    }
    names = ["lettering counters punctuation", "thin corners dots", "adjacent colours nested holes", "binary transparency", "gradient", "photograph", "wide margins", "blank", "fully transparent", "Übergrößé 文件 with spaces", "icc sRGB"]
    for mode in ["smooth", "pixel"]:
        for name in names:
            source = fixture_paths[name]
            target = output / mode
            before = digest(source)
            start = time.monotonic()
            try:
                result = convert(source, target, Options(mode=mode))
                item = {"fixture": name, "mode": mode, "elapsed_seconds": round(time.monotonic() - start, 4), "input_sha256_before": before, "input_sha256_after": digest(source), "output": str(Path(result.output).relative_to(output)), "output_bytes": Path(result.output).stat().st_size, "inspection": inspect_eps(result.output), "metrics": result.metrics, "warnings": result.warnings}
                if name in ["lettering counters punctuation", "thin corners dots", "adjacent colours nested holes", "wide margins", "photograph"]:
                    comparison(result, target / f"{source.stem}-comparison.png")
                item["status"] = "passed"
            except Exception as error:
                item = {"fixture": name, "mode": mode, "status": "failed", "error": str(error), "type": type(error).__name__}
            report["conversions"].append(item)
            (output / "report.json").write_text(json.dumps(report, indent=2, default=str) + "\n")
            print(f"{mode}: {name}: {item['status']}", flush=True)
    source = fixture_paths["partial edges"]
    for policy in ["composite", "retain"]:
        before = digest(source)
        try:
            result = convert(source, output / policy, Options(mode="pixel", alpha_policy=policy, background="#1872c3"))
            report["conversions"].append({"fixture": "partial edges", "mode": "pixel", "alpha_policy": policy, "status": "passed", "output": str(Path(result.output).relative_to(output)), "metrics": result.metrics, "warnings": result.warnings, "input_sha256_before": before, "input_sha256_after": digest(source)})
        except Exception as error:
            report["conversions"].append({"fixture": "partial edges", "alpha_policy": policy, "status": "failed", "error": str(error)})
    (output / "report.json").write_text(json.dumps(report, indent=2, default=str) + "\n")
    failures = [item for item in report["conversions"] if item["status"] == "failed"]
    print(f"{len(report['conversions']) - len(failures)} passed; {len(failures)} failed")
    return bool(failures)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("evidence"))
    args = parser.parse_args()
    raise SystemExit(run(args.output))
