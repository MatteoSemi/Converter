"""End-to-end tests: final EPS, Ghostscript and independent pixel coverage."""
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET

from PIL import Image, ImageChops
import pytest

from pngvector.core import Options, convert, ConversionError, AlphaPolicyRequired
from pngvector.eps import inspect_eps, render_eps


REGULAR_FIXTURES = [
    "lettering counters punctuation", "thin corners dots",
    "adjacent colours nested holes", "binary transparency", "gradient",
    "photograph", "wide margins", "blank", "fully transparent",
    "Übergrößé 文件 with spaces", "icc sRGB",
]


def digest(path):
    return sha256(Path(path).read_bytes()).hexdigest()


@pytest.mark.parametrize("mode", ["smooth", "pixel"])
@pytest.mark.parametrize("name", REGULAR_FIXTURES)
def test_end_to_end(fixtures, tmp_path, mode, name):
    source = fixtures[name]
    before = digest(source)
    progress = []
    result = convert(source, tmp_path, Options(mode=mode), progress=lambda fraction, message: progress.append((fraction, message)))
    assert digest(source) == before
    output = Path(result.output)
    assert output.suffix.lower() == ".eps"
    assert output.read_bytes().startswith(b"%!PS-Adobe-")
    assert b"EPSF-" in output.read_bytes()[:80]
    inspection = inspect_eps(output)
    assert inspection["valid_header"]
    assert inspection["vector_only"], inspection
    assert not inspection["raster_operators"], inspection
    image = Image.open(source)
    dpi = image.info.get("dpi", (96, 96))[0]
    bb = inspection["hires_bounding_box"]
    assert bb[0:2] == [0, 0]
    assert math.isclose(bb[2], image.width * 72 / dpi, abs_tol=0.02)
    assert math.isclose(bb[3], image.height * 72 / dpi, abs_tol=0.02)
    with Image.open(result.preview) as preview:
        assert preview.size == image.size
        assert preview.mode in ("RGB", "RGBA")
    assert progress
    assert result.metrics


def test_canvas_orientation_and_margins(fixtures, tmp_path):
    result = convert(fixtures["wide margins"], tmp_path, Options(mode="pixel"))
    with Image.open(result.preview) as render:
        render = render.convert("RGB")
        assert render.size == (263, 91)
        for position, color in [((95, 29), (250, 0, 0)), ((131, 26), (0, 0, 250)), ((130, 43), (0, 190, 0)), ((10, 10), (255, 255, 255)), ((250, 80), (255, 255, 255))]:
            actual = render.getpixel(position)
            assert max(abs(a - b) for a, b in zip(actual, color)) <= 2


@pytest.mark.parametrize("mode", ["smooth", "pixel"])
def test_subpoint_canvas_alignment_and_isolated_dot(fixtures, tmp_path, mode):
    # 79px at 96dpi is 59.25pt. Cairo's rounded EPS page must not shift artwork.
    result = convert(fixtures["thin corners dots"], tmp_path, Options(mode=mode))
    with Image.open(result.preview) as render:
        render = render.convert("RGB")
        assert max(render.getpixel((50, 9))) < 30, "One-pixel line moved or disappeared"
        assert min(render.getpixel((50, 8))) > 240, "Canvas rounding shifted the line upward"
        assert max(render.getpixel((111, 64))) < 60, "Isolated one-pixel punctuation/dot was lost"


def test_holes_stay_unpainted(fixtures, tmp_path):
    source = fixtures["adjacent colours nested holes"]
    result = convert(source, tmp_path, Options(mode="smooth", tracer="vtracer"))
    # EPS contains no transparency; unpainted areas reveal the renderer background.
    with Image.open(result.preview) as render:
        render = render.convert("RGB")
        for point in [(0, 0), (40, 40), (80, 56), (151, 100)]:
            assert min(render.getpixel(point)) > 245, (point, render.getpixel(point))
        assert render.getpixel((63, 55))[1] > 120
        assert render.getpixel((20, 20))[0] > 200
        assert render.getpixel((140, 20))[2] > 180
    coloured = tmp_path / "coloured-background.png"
    render_eps(result.output, coloured, result.width, result.height, result.dpi, "#a93dc5")
    with Image.open(coloured) as render:
        for point in [(0, 0), (40, 40), (80, 56), (151, 100)]:
            assert render.convert("RGB").getpixel(point) == (169, 61, 197)


@pytest.mark.parametrize("mode", ["smooth", "pixel"])
def test_fully_transparent_paints_no_background(fixtures, tmp_path, mode):
    result = convert(fixtures["fully transparent"], tmp_path, Options(mode=mode))
    coloured = tmp_path / "fully-transparent-on-colour.png"
    render_eps(result.output, coloured, result.width, result.height, result.dpi, "#a93dc5")
    with Image.open(coloured) as render:
        assert render.convert("RGB").getcolors() == [(67 * 43, (169, 61, 197))]


def test_no_silent_overwrite(fixtures, tmp_path):
    first = convert(fixtures["wide margins"], tmp_path, Options(mode="pixel"))
    old = digest(first.output)
    second = convert(fixtures["wide margins"], tmp_path, Options(mode="pixel"))
    assert Path(first.output) != Path(second.output)
    assert digest(first.output) == old


@pytest.mark.parametrize("mode", ["smooth", "pixel"])
def test_partial_alpha_requires_choice(fixtures, tmp_path, mode):
    with pytest.raises(AlphaPolicyRequired):
        convert(fixtures["partial edges"], tmp_path, Options(mode=mode))
    assert not list(tmp_path.rglob("*.eps"))


def test_partial_alpha_composite(fixtures, tmp_path):
    source = fixtures["partial edges"]
    before = digest(source)
    result = convert(source, tmp_path, Options(mode="pixel", alpha_policy="composite", background="#1872c3"))
    assert inspect_eps(result.output)["vector_only"]
    with Image.open(result.reference) as reference:
        assert reference.convert("RGB").getpixel((0, 0)) == (24, 114, 195)
    assert digest(source) == before


def test_partial_alpha_retained_export(fixtures, tmp_path):
    result = convert(fixtures["partial edges"], tmp_path, Options(alpha_policy="retain"))
    assert not list(tmp_path.rglob("*.eps")), "General alpha cannot be silently exported as EPS"
    assert list(tmp_path.rglob("*.svg"))
    assert list(tmp_path.rglob("*.pdf"))
    svg = next(tmp_path.rglob("*.svg")).read_text()
    assert "opacity" in svg
    assert "<image" not in svg
    assert "data:image" not in svg
    # Validate retained alpha numerically, independently of Cairo's preview.
    source = Image.open(fixtures["partial edges"]).convert("RGBA")
    reconstructed = Image.new("RGBA", source.size, (0, 0, 0, 0))
    pixels = reconstructed.load()
    for rectangle in ET.fromstring(svg).iter("{http://www.w3.org/2000/svg}rect"):
        x, y, w, h = [int(rectangle.get(key)) for key in ("x", "y", "width", "height")]
        fill = rectangle.get("fill").lstrip("#")
        color = tuple(int(fill[index:index + 2], 16) for index in (0, 2, 4))
        alpha = round(float(rectangle.get("fill-opacity", "1")) * 255)
        for py in range(y, y + h):
            for px in range(x, x + w):
                pixels[px, py] = (*color, alpha)
    for y in range(source.height):
        for x in range(source.width):
            original = source.getpixel((x, y))
            if original[3]:
                assert pixels[x, y] == original
            else:
                assert pixels[x, y][3] == 0
    pdfimages = shutil.which("pdfimages")
    if pdfimages:
        inspection = subprocess.run([pdfimages, "-list", str(next(tmp_path.rglob("*.pdf")))],
                                    capture_output=True, text=True, check=True)
        # Poppler emits two header lines followed by one record per image.
        assert not inspection.stdout.splitlines()[2:], "Retained-alpha PDF contains bitmap images"


@pytest.mark.parametrize("name", ["corrupted", "16 bit gray"])
def test_rejects_unsupported_inputs(fixtures, tmp_path, name):
    before = digest(fixtures[name])
    with pytest.raises(ConversionError):
        convert(fixtures[name], tmp_path, Options())
    assert digest(fixtures[name]) == before
    assert not list(tmp_path.rglob("*.eps"))


def test_large_requires_confirmation(fixtures, tmp_path):
    with pytest.raises(ConversionError):
        convert(fixtures["large"], tmp_path, Options(mode="pixel", max_pixels=1_000_000))
    assert not list(tmp_path.rglob("*.eps"))


def test_cancellation(fixtures, tmp_path):
    with pytest.raises(ConversionError):
        convert(fixtures["large"], tmp_path, Options(mode="pixel", force_large=True), cancelled=lambda: True)
    assert not list(tmp_path.rglob("*.eps"))


def test_cancel_during_rectangle_build_cleans_up(fixtures, tmp_path):
    source = fixtures["photograph"]
    before = digest(source)
    checks = 0
    progress = []

    def cancelled():
        nonlocal checks
        checks += 1
        return checks >= 20

    with pytest.raises(ConversionError, match="[Cc]ancel"):
        convert(source, tmp_path, Options(mode="pixel"), cancelled=cancelled,
                progress=lambda fraction, message: progress.append(fraction))
    assert checks == 20
    assert 0.1 in progress, "Cancellation must occur after geometry building started"
    assert digest(source) == before
    assert not list(tmp_path.iterdir()), "Cancelled job left partial output behind"


@pytest.mark.parametrize("tracer,name", [("vtracer", "adjacent colours nested holes"), ("potrace", "lettering counters punctuation")])
def test_positive_smoothing_option(fixtures, tmp_path, tracer, name):
    result = convert(fixtures[name], tmp_path, Options(tracer=tracer, smoothing=0.5, detail=9))
    assert inspect_eps(result.output)["vector_only"]
    assert result.path_count > 0


def test_explicit_destructive_options_produce_verified_vectors(fixtures, tmp_path):
    result = convert(fixtures["thin corners dots"], tmp_path,
                     Options(tracer="vtracer", smoothing=0.5, detail=3, color_precision=5, min_component=3))
    assert inspect_eps(result.output)["vector_only"]
    assert result.warnings


def test_malformed_icc_is_actionable(tmp_path):
    source = tmp_path / "invalid-profile.png"
    Image.new("RGB", (18, 12), (10, 20, 30)).save(source, icc_profile=b"invalid ICC profile")
    before = digest(source)
    with pytest.raises(ConversionError, match="ICC profile"):
        convert(source, tmp_path / "output", Options())
    assert digest(source) == before
    assert not list((tmp_path / "output").rglob("*.eps"))


def test_exact_eps_coverage_and_colour(fixtures, tmp_path):
    """Read actual emitted EPS rectangle painting commands, not an intermediate SVG.

    Paint coverage into our own integer lattice. Verify every opaque source pixel
    is painted exactly once and every transparent pixel zero times, then compare
    channel values after reversing the EPS colour decimal representation.
    """
    for name in ["adjacent colours nested holes", "thin corners dots", "gradient", "photograph"]:
        source = Image.open(fixtures[name]).convert("RGBA")
        result = convert(fixtures[name], tmp_path, Options(mode="pixel"))
        text = Path(result.output).read_text("ascii")
        scale = re.search(r"gsave\s+([-+.\d]+)\s+([-+.\d]+)\s+scale", text)
        assert scale and all(math.isclose(float(v), 72 / result.dpi, abs_tol=1e-12) for v in scale.groups())
        assert re.search(rf"0\s+{source.height}\s+translate\s+1\s+-1\s+scale", text)
        decimal = r"([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)"
        number3 = r"\s+".join([decimal] * 3)
        number4 = r"\s+".join([decimal] * 4)
        event = re.compile(r"(?:" + number3 + r"\s+setrgbcolor)|(?:" + number4 + r"\s+rectfill)")
        matches = list(event.finditer(text))
        assert matches, "No independently inspectable rectangle paint commands found"
        reconstructed = Image.new("RGBA", source.size, (0, 0, 0, 0))
        pixels = reconstructed.load()
        coverage = bytearray(source.width * source.height)
        r = g = b = 0.0  # PostScript initial RGB paint colour is black.
        rectangle_count = 0
        for match in matches:
            values = match.groups()
            if values[0] is not None:
                r, g, b = map(float, values[:3])
                continue
            x, y, w, h = map(float, values[3:])
            rectangle_count += 1
            assert all(v.is_integer() for v in (x, y, w, h))
            x, y, w, h = map(int, (x, y, w, h))
            assert 0 <= x < source.width and 0 <= y < source.height
            assert w > 0 and h > 0 and x + w <= source.width and y + h <= source.height
            color = tuple(round(channel * 255) for channel in (r, g, b)) + (255,)
            for py in range(y, y + h):
                for px in range(x, x + w):
                    coverage[py * source.width + px] += 1
                    pixels[px, py] = color
        assert rectangle_count > 0
        for y in range(source.height):
            for x in range(source.width):
                original = source.getpixel((x, y))
                assert coverage[y * source.width + x] == (1 if original[3] else 0)
                if original[3]:
                    assert pixels[x, y] == original


@pytest.mark.parametrize("operator,body", [
    ("image", "1 1 8 [1 0 0 -1 0 1] {<80>} image"),
    ("imagemask", "1 1 true [1 0 0 -1 0 1] {<80>} imagemask"),
    ("colorimage", "1 1 8 [1 0 0 -1 0 1] {<ff0000>} false 3 colorimage"),
    ("image", "<< /ImageType 1 /Width 1 /Height 1 /BitsPerComponent 8 /ImageMatrix [1 0 0 -1 0 1] /Decode [0 1] /DataSource <80> >> image"),
    ("image", "systemdict /image get /hiddenRaster exch def 1 1 8 [1 0 0 -1 0 1] {<80>} hiddenRaster"),
    ("image", "/boundRaster { image } bind def 1 1 8 [1 0 0 -1 0 1] {<80>} boundRaster"),
])
def test_raster_negative_fixtures(tmp_path, operator, body):
    path = tmp_path / "negative.eps"
    path.write_text("%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 10 10\n%%HiResBoundingBox: 0 0 10 10\n/DeviceRGB setcolorspace\n" + body + "\nshowpage\n%%EOF\n")
    result = inspect_eps(path)
    assert not result["vector_only"], result
    assert result["raster_operators"], result


def test_bitmap_generated_by_established_backend_detected(tmp_path):
    # A valid Cairo SVG-with-bitmap -> EPS negative fixture proves that an EPS
    # signature, compressed content and a real exporter cannot fool the audit.
    import base64
    import io
    import cairosvg
    png = io.BytesIO()
    Image.new("RGB", (8, 8), (250, 20, 30)).save(png, format="PNG")
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="16" height="16"><image width="16" height="16" xlink:href="data:image/png;base64,{base64.b64encode(png.getvalue()).decode()}"/></svg>'
    path = tmp_path / "negative-cairo.eps"
    cairosvg.svg2eps(bytestring=svg.encode(), write_to=str(path))
    assert not inspect_eps(path)["vector_only"]


def test_potrace_explicit_when_available(fixtures, tmp_path):
    if not (shutil.which("potrace") or Path("/workspace/.local/usr/bin/potrace").exists()):
        pytest.skip("Potrace external executable is not installed")
    result = convert(fixtures["lettering counters punctuation"], tmp_path, Options(tracer="potrace"))
    assert inspect_eps(result.output)["vector_only"]
    with Image.open(result.preview) as render:
        render = render.convert("RGB")
        for point in [(27, 40), (66, 28), (66, 50), (108, 28), (108, 50)]:
            assert min(render.getpixel(point)) > 240


def test_potrace_does_not_discard_colour(fixtures, tmp_path):
    with pytest.raises(ConversionError):
        convert(fixtures["gradient"], tmp_path, Options(tracer="potrace"))


@pytest.mark.parametrize('mode', ['pixel', 'smooth'])
@pytest.mark.parametrize('colour', ['black', 'white'])
def test_line_extraction_exports_only_selected_colour(mode, colour, tmp_path):
    source = tmp_path / 'lines.png'
    image = Image.new('RGBA', (40, 20), 'white')
    for x in range(20):
        for y in range(20):
            image.putpixel((x, y), (30, 30, 30, 255))
    image.putpixel((30, 10), (255, 255, 255, 0))
    image.save(source)
    original = digest(source)
    result = convert(source, tmp_path / 'out', Options(mode=mode, line_color=colour))
    with Image.open(result.preview.with_name('lines.preview-alpha.png')) as preview:
        assert preview.getpixel((5, 10))[3] == (255 if colour == 'black' else 0)
        assert preview.getpixel((35, 10))[3] == (255 if colour == 'white' else 0)
        rgb = (0, 0, 0) if colour == 'black' else (255, 255, 255)
        assert preview.getpixel((5 if colour == 'black' else 35, 10))[:3] == rgb
    assert inspect_eps(result.output)['vector_only']
    assert digest(source) == original
