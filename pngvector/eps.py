"""EPS export verification and final-file rendering with Ghostscript.

All external invocations use argument arrays, no shell. Raster detection executes
the artwork with image painting operators instrumented, rather than looking for
words in its source (Cairo includes unused image procedures in its prologue).
This verifier is intended for trusted, locally generated PostScript, not as a
security sandbox for adversarial programs.
"""
from __future__ import annotations

import math
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time


class EPSVerificationError(RuntimeError):
    pass


def ghostscript() -> str:
    executable = shutil.which("gs") or shutil.which("gswin64c") or shutil.which("gswin32c")
    if not executable:
        raise EPSVerificationError("Ghostscript is missing. Install Ghostscript and make gs (or gswin64c) available on PATH.")
    return executable


def run_process(args, *, cancelled=None, timeout=300):
    """Bounded, cancellable process; logs cannot fill an unread PIPE."""
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen([str(a) for a in args], stdout=log, stderr=subprocess.STDOUT)
        started = time.monotonic()
        try:
            while process.poll() is None:
                if cancelled and cancelled():
                    raise InterruptedError("Conversion cancelled.")
                if time.monotonic() - started > timeout:
                    raise EPSVerificationError(f"Operation exceeded {timeout} seconds: {Path(str(args[0])).name}")
                time.sleep(0.05)
        except BaseException:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            raise
        log.seek(0)
        output = log.read(65536).decode("utf-8", errors="replace")
        return process.returncode, output


def inspect_eps(path, *, cancelled=None) -> dict:
    path = Path(path)
    with path.open("rb") as stream:
        header = stream.read(8192).decode("latin-1")
    valid_header = header.startswith("%!PS-Adobe-") and "EPSF-" in header.splitlines()[0]
    boxes = {}
    for key, label in (("bounding_box", "BoundingBox"), ("hires_bounding_box", "HiResBoundingBox")):
        match = re.search(r"^%%" + label + r":\s+([-+.\d]+)\s+([-+.\d]+)\s+([-+.\d]+)\s+([-+.\d]+)", header, re.M)
        boxes[key] = [float(v) for v in match.groups()] if match else None
    # Override in systemdict so subsequently bound procedures and direct
    # systemdict lookups are covered as well as ordinary name lookup.
    probe = """
true setglobal
systemdict /image { (PNGVECRASTER:image\\n) print flush } bind put
systemdict /imagemask { (PNGVECRASTER:imagemask\\n) print flush } bind put
systemdict /colorimage { (PNGVECRASTER:colorimage\\n) print flush } bind put
false setglobal
systemdict readonly pop
"""
    # A detected operator must consume its original operands. Stop interpretation
    # immediately instead: otherwise leftover operands cause misleading errors.
    probe = probe.replace("print flush }", "print flush 93 .quit }")
    code, output = run_process([ghostscript(), "-q", "-dSAFER", "-dWRITESYSTEMDICT", "-dBATCH", "-dNOPAUSE", "-sDEVICE=nullpage", "-c", probe, "-f", path], cancelled=cancelled)
    operators = re.findall(r"PNGVECRASTER:(\w+)", output)
    if code and not operators:
        raise EPSVerificationError(f"Ghostscript could not interpret EPS (exit {code}): {output[-2000:]}")
    return {"valid_header": valid_header, **boxes, "vector_only": not operators and code == 0, "raster_operators": operators, "interpreter_exit": code}


def set_canvas_bbox(path, width_points, height_points):
    """Override ink-based DSC boxes with the complete original canvas.

    Cairo's EPS backend reports ink extents and rounds its root canvas y-flip to
    an integer point. Preserve full canvas DSC boxes and align that y-flip with
    the actual fractional-point page. Rendering uses a matching explicit page.
    """
    path = Path(path)
    data = path.read_bytes()
    # Cairo rounds the EPS surface height to an integer point in its root
    # y-flip, even when geometry came from a fractional-point SVG page. Move the
    # entire page (including its ink clip) into the true canvas. Merely changing
    # DSC boxes would shift all artwork by up to one point on final rendering.
    flip = re.search(rb"(?m)^1 0 0 -1 0 ([-+.\d]+) cm", data)
    if flip:
        delta = height_points-float(flip.group(1))
        if abs(delta)>1e-10:
            setup=b"%%EndPageSetup\n"
            if setup not in data:
                raise EPSVerificationError("Cairo EPS page setup could not be aligned to its canvas.")
            prefix,page=data.split(setup,1)
            # Keep the correction inside Cairo's existing q/Q save/restore;
            # importing this EPS must not change the caller's transform.
            if not re.match(rb"q\s",page):
                raise EPSVerificationError("Cairo EPS graphics save could not be located for canvas alignment.")
            page=re.sub(rb"^q(?=\s)",f"q\n0 {delta:.12f} translate".encode(),page,count=1)
            data=prefix+setup+page
    box = f"%%BoundingBox: 0 0 {math.ceil(width_points)} {math.ceil(height_points)}".encode()
    hires = f"%%HiResBoundingBox: 0 0 {width_points:.10f} {height_points:.10f}".encode()
    data = re.sub(rb"(?m)^%%BoundingBox:[^\r\n]*", box, data)
    data = re.sub(rb"(?m)^%%HiResBoundingBox:[^\r\n]*", hires, data)
    if b"%%HiResBoundingBox:" not in data:
        data = data.replace(box, box + b"\n" + hires, 1)
    path.write_bytes(data)


def render_eps(path, output, width, height, dpi, background="#ffffff", *, cancelled=None):
    from PIL import Image, ImageColor
    output = Path(output)
    rgb = ImageColor.getrgb(background)
    if len(rgb) != 3:
        raise ValueError("Preview background must be an RGB colour.")
    # EPSCrop uses rounded integer BoundingBox on some GS releases. Explicit page
    # dimensions and FIXEDMEDIA keep sub-point canvas sizes and wide margins.
    width_points, height_points = width * 72 / dpi, height * 72 / dpi
    page = f"<</PageSize [{width_points:.12f} {height_points:.12f}]>> setpagedevice"
    # pngalpha gives a transparent canvas; composite after interpretation against
    # the same sRGB reference background rather than relying on device defaults.
    code, log = run_process([ghostscript(), "-q", "-dSAFER", "-dBATCH", "-dNOPAUSE", "-dFIXEDMEDIA", "-sDEVICE=pngalpha", "-sDefaultRGBProfile=srgb.icc", "-sOutputICCProfile=srgb.icc", "-dTextAlphaBits=4", "-dGraphicsAlphaBits=4", f"-r{dpi:.12f}", f"-g{width}x{height}", f"-sOutputFile={str(output).replace('%','%%')}", "-c", page, "-f", Path(path)], cancelled=cancelled)
    if code:
        raise EPSVerificationError(f"Ghostscript rendering failed (exit {code}): {log[-2000:]}")
    with Image.open(output) as image:
        if image.size != (width, height):
            raise EPSVerificationError(f"Rendered canvas is {image.size}, expected {(width, height)}.")
        rgba = image.convert("RGBA")
    bg = Image.new("RGBA", rgba.size, (*rgb, 255))
    Image.alpha_composite(bg, rgba).convert("RGB").save(output)
    return output
