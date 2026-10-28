"""Local PNG to editable vector EPS pipeline.

No source files are modified. Smooth tracing uses VTracer/Potrace; pixel mode
encodes exact decoded sRGB pixel coverage as merged vector rectangles.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
import hashlib
import io
import json
import math
import multiprocessing
import os
import re
import shutil
import tempfile
import time
import xml.etree.ElementTree as ET

from PIL import Image, ImageCms, ImageColor, ImageChops, ImageStat, UnidentifiedImageError

from .eps import inspect_eps, render_eps, set_canvas_bbox, run_process, EPSVerificationError


class ConversionError(RuntimeError):
    pass


class AlphaPolicyRequired(ConversionError):
    pass


class LargeImageError(ConversionError):
    pass


class Cancelled(ConversionError):
    pass


@dataclass
class Options:
    mode: str = "smooth"
    tracer: str = "auto"
    detail: int = 6
    smoothing: float = 0.0
    color_precision: int = 8
    min_component: int = 0
    alpha_policy: str = "ask"
    background: str = "#ffffff"
    dpi: float | None = None
    max_pixels: int = 4_000_000
    force_large: bool = False
    line_color: str = "all"
    line_threshold: int = 128
    line_alpha_threshold: int = 128


@dataclass
class Result:
    source: Path
    output: Path
    preview: Path
    reference: Path
    metrics: dict
    warnings: list[str]
    width: int
    height: int
    dpi: float
    path_count: int
    extras: list[Path] = field(default_factory=list)
    comparison_background: str = "#ffffff"


def validate_line_options(options):
    if options.line_color not in ("all", "black", "white"):
        raise ConversionError("Line colour must be all, black or white.")
    if type(options.line_threshold) is not int or not 0 <= options.line_threshold <= 255:
        raise ConversionError("Line brightness cutoff must be an integer from 0 to 255.")
    if type(options.line_alpha_threshold) is not int or not 1 <= options.line_alpha_threshold <= 255:
        raise ConversionError("Line opacity cutoff must be an integer from 1 to 255.")


def extract_lines(image, options):
    """Shared selection for exports and the before-tracing preview; no resampling."""
    validate_line_options(options)
    rgba = image.convert("RGBA")
    if options.line_color == "all":
        return rgba
    luminance = rgba.convert("RGB").convert("L")
    threshold = options.line_threshold
    mask = luminance.point(lambda value: 255 if (value < threshold if options.line_color == "black" else value >= threshold) else 0)
    # An opacity cutoff of at least 1 keeps fully transparent pixels unpainted.
    alpha = rgba.getchannel("A").point(lambda value: 255 if value >= options.line_alpha_threshold else 0)
    selected = Image.new("RGBA", rgba.size, options.line_color)
    selected.putalpha(ImageChops.multiply(mask, alpha))
    return selected


def load_selection_source(source):
    """Load a colour-managed source for an exact, bounded interactive preview."""
    width, height = _png_info(source)
    if width * height > 4_000_000:
        raise LargeImageError("Live selection preview is limited to 4 million pixels. You can still convert this image with the chosen cutoffs.")
    # Selection uses pixel coordinates; physical DPI is chosen for export.
    return _load(source, Options(dpi=96))[0]


def _check(cancelled):
    if cancelled and cancelled():
        raise Cancelled("Conversion cancelled; source file was left unchanged.")


def _png_info(source):
    source = Path(source)
    try:
        with source.open("rb") as handle:
            header = handle.read(33)
        if header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
            raise ConversionError("The selected file is not a valid PNG (signature/IHDR missing).")
        if len(header) < 33:
            raise ConversionError("PNG header is truncated.")
        if header[24] == 16:
            raise ConversionError("16-bit PNG channels are not supported. Convert explicitly to an 8-bit colour-managed PNG first; this application will not silently reduce bit depth.")
        if header[24] not in (1, 2, 4, 8):
            raise ConversionError(f"Unsupported PNG bit depth: {header[24]}.")
        width, height = int.from_bytes(header[16:20], "big"), int.from_bytes(header[20:24], "big")
        if not width or not height:
            raise ConversionError("PNG canvas must have nonzero dimensions.")
        return width, height
    except OSError as exc:
        raise ConversionError(f"Cannot read PNG: {exc}") from exc


def estimate(source, options=None):
    options = options or Options()
    width, height = _png_info(source)
    pixels = width * height
    # Worst-case rectangle/path counts, not a guarantee for photographs.
    estimated_bytes = pixels * (140 if options.mode == "pixel" else 300)
    partial = None
    if pixels <= options.max_pixels:
        try:
            with Image.open(source) as image:
                alpha = image.convert("RGBA").getchannel("A")
                histogram = alpha.histogram()
                partial = bool(sum(histogram[1:255]))
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            raise ConversionError(f"PNG cannot be decoded: {exc}") from exc
    return {"width": width, "height": height, "pixels": pixels, "estimated_bytes": estimated_bytes, "estimated_memory_bytes":pixels*1000, "large": pixels > options.max_pixels, "partial_alpha": partial}


def _load(source, options):
    warnings = []
    try:
        with Image.open(source) as image:
            if getattr(image, "n_frames", 1) != 1:
                raise ConversionError("Animated PNG is unsupported. Export the desired frame explicitly first.")
            image.load()
            if image.getexif().get(274, 1) != 1:
                raise ConversionError("PNG has an EXIF orientation transform. Apply the orientation explicitly before conversion so canvas placement is unambiguous.")
            info = dict(image.info)
            rgba = image.convert("RGBA")
            profile = info.get("icc_profile")
            if profile:
                try:
                    # PNG ICC profiles describe RGB or grayscale input. Preserve
                    # the original matching source mode for CMS conversion.
                    source_profile = ImageCms.ImageCmsProfile(io.BytesIO(profile))
                    space = source_profile.profile.xcolor_space.strip()
                    cms_source = image.convert("L" if space == "GRAY" else "RGB")
                    rgb = ImageCms.profileToProfile(cms_source, source_profile, ImageCms.createProfile("sRGB"), renderingIntent=ImageCms.Intent.RELATIVE_COLORIMETRIC, outputMode="RGB")
                    rgb.putalpha(rgba.getchannel("A"))
                    rgba = rgb
                    warnings.append("Embedded ICC profile converted to sRGB with LittleCMS, relative colorimetric intent; alpha was preserved.")
                except Exception as exc:
                    raise ConversionError(f"Embedded ICC profile could not be applied: {exc}. Repair or explicitly convert the profile before retrying.") from exc
            else:
                if "gamma" in info or "chromaticity" in info:
                    warnings.append("PNG has gAMA/cHRM metadata without an ICC profile. This version assumes sRGB pixel values; explicitly convert to an ICC-tagged sRGB PNG if these chunks describe another colour space.")
            dpi = options.dpi
            if dpi is None:
                embedded = info.get("dpi")
                if embedded:
                    x, y = embedded
                    if not math.isfinite(x) or not math.isfinite(y) or x <= 0 or y <= 0:
                        raise ConversionError("PNG has invalid DPI metadata; choose an explicit positive DPI override.")
                    if abs(x-y) > max(x,y)*0.001:
                        raise ConversionError("PNG has unequal horizontal/vertical DPI. Choose an explicit DPI to preserve pixel aspect ratio and canvas proportions.")
                    dpi = float(x)
                else:
                    dpi = 96.0
            if not math.isfinite(dpi) or dpi < 1 or dpi > 10000:
                raise ConversionError("DPI must be a finite value between 1 and 10000.")
            return rgba, dpi, warnings
    except ConversionError:
        raise
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise ConversionError(f"PNG cannot be decoded: {exc}") from exc


def pixel_rectangles(image, cancelled=None):
    """Merge equal horizontal runs vertically without changing coverage/colour."""
    rgba = image.convert("RGBA")
    pixels = rgba.load()
    active = {}
    rectangles = []
    for y in range(rgba.height):
        _check(cancelled)
        runs = {}
        x = 0
        while x < rgba.width:
            color = pixels[x, y]
            start = x
            x += 1
            while x < rgba.width and pixels[x, y] == color:
                x += 1
            if color[3] == 0:
                continue
            key = (start, x-start, color)
            previous = active.pop(key, None)
            runs[key] = (start, previous[1] if previous else y, x-start, previous[3]+1 if previous else 1, color)
        rectangles.extend(active.values())
        active = runs
    rectangles.extend(active.values())
    return rectangles


def _write_pixel_eps(path, rectangles, size, dpi, cancelled):
    width, height = size
    wp, hp = width*72/dpi, height*72/dpi
    with Path(path).open("w", encoding="ascii", newline="\n") as output:
        output.write(f"%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 {math.ceil(wp)} {math.ceil(hp)}\n%%HiResBoundingBox: 0 0 {wp:.10f} {hp:.10f}\n%%Creator: PNG Vector exact pixel rectangles\n%%LanguageLevel: 2\n%%Pages: 1\n%%EndComments\ngsave\n{72/dpi:.14g} {72/dpi:.14g} scale\n0 {height} translate 1 -1 scale\n")
        last = None
        for index, (x,y,w,h,color) in enumerate(rectangles):
            if index % 2048 == 0:
                _check(cancelled)
            if color[3] != 255:
                raise ConversionError("EPS cannot paint partially transparent pixels; choose compositing first.")
            if color != last:
                output.write(" ".join(f"{v/255:.14g}" for v in color[:3]) + " setrgbcolor\n")
                last = color
            output.write(f"{x} {y} {w} {h} rectfill\n")
        output.write("grestore\nshowpage\n%%EOF\n")


def _write_pixel_svg(path, rectangles, size, dpi, cancelled=None):
    width, height = size
    with Path(path).open("w", encoding="utf-8") as output:
        output.write(f'<svg xmlns="http://www.w3.org/2000/svg" width="{width*72/dpi:.12f}pt" height="{height*72/dpi:.12f}pt" viewBox="0 0 {width} {height}">\n')
        for index,(x,y,w,h,(r,g,b,a)) in enumerate(rectangles):
            if index % 2048 == 0:
                _check(cancelled)
            output.write(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="#{r:02x}{g:02x}{b:02x}" fill-opacity="{a/255:.14g}"/>\n')
        output.write('</svg>\n')


def _vtracer_worker(image_path, svg_path, kwargs, connection):
    try:
        import vtracer
        vtracer.convert_image_to_svg_py(str(image_path), str(svg_path), **kwargs)
        connection.send(None)
    except BaseException as exc:
        connection.send(str(exc))
    finally:
        connection.close()


def _cairo_worker(kind, source, target, kwargs, connection):
    try:
        import cairosvg
        getattr(cairosvg, "svg2"+kind)(url=str(source),write_to=str(target),**kwargs)
        connection.send(None)
    except BaseException as exc:
        connection.send(str(exc))
    finally:
        connection.close()


def _cairo_export(kind, source, target, cancelled=None, **kwargs):
    context=multiprocessing.get_context("spawn")
    receiver,sender=context.Pipe(duplex=False)
    process=context.Process(target=_cairo_worker,args=(kind,source,target,kwargs,sender))
    process.start()
    sender.close()
    start=time.monotonic()
    try:
        while process.is_alive():
            _check(cancelled)
            if time.monotonic()-start>600:
                raise ConversionError("Cairo export exceeded ten minutes; reduce source dimensions or colour precision explicitly.")
            process.join(0.1)
        error=receiver.recv() if receiver.poll() else f"export worker exited {process.exitcode}"
        if error:
            raise ConversionError(f"Cairo {kind.upper()} export failed: {error}")
    finally:
        if process.is_alive():
            process.terminate()
            process.join(3)
            if process.is_alive():
                process.kill()
                process.join()
        receiver.close()


def _trace_vtracer(image, folder, options, cancelled):
    image_path, svg_path = folder/"trace-input.png", folder/"traced.svg"
    image.save(image_path)
    # VTracer's polygon simplifier can collapse isolated one-pixel components
    # even with filter_speckle=0. Its supported `none` fitting mode retains the
    # actual traced contours; smoothing must be explicitly requested.
    kwargs = dict(colormode="color", hierarchical="cutout", mode="none" if options.smoothing == 0 else "spline", filter_speckle=options.min_component, color_precision=options.color_precision, layer_difference=0, corner_threshold=round(60+options.smoothing*100), length_threshold=max(0.5, 5-options.detail*0.45)+options.smoothing*4, max_iterations=10, splice_threshold=round(45+options.smoothing*90), path_precision=8)
    # A separate process makes even a CPU-bound Rust trace cancellable.
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_vtracer_worker, args=(image_path, svg_path, kwargs, sender))
    process.start()
    sender.close()
    start = time.monotonic()
    try:
        while process.is_alive():
            _check(cancelled)
            if time.monotonic()-start > 600:
                raise ConversionError("VTracer exceeded ten minutes. Cancelled; reduce dimensions or use pixel mode explicitly.")
            process.join(0.1)
        error = receiver.recv() if receiver.poll() else f"VTracer process exited with status {process.exitcode}"
        if error:
            raise ConversionError(f"VTracer tracing failed: {error}")
    finally:
        if process.is_alive():
            process.terminate()
            process.join(3)
            if process.is_alive():
                process.kill()
                process.join()
        receiver.close()
    return svg_path


def _potrace_binary(image):
    """Recognize exact black/white + binary alpha; no silent thresholding."""
    colors = image.getcolors(maxcolors=4)
    if colors is None:
        return False
    return all(a in (0,255) and (a == 0 or (r,g,b) in ((0,0,0),(255,255,255))) for count,(r,g,b,a) in colors)


def _trace_potrace(image, folder, options, cancelled):
    executable = os.environ.get("POTRACE_BINARY") or shutil.which("potrace")
    if not executable:
        candidate = Path("/workspace/.local/usr/bin/potrace")
        executable = str(candidate) if candidate.exists() else None
    if not executable:
        raise ConversionError("Potrace is missing. Install the Potrace command-line tool, or choose VTracer.")
    if not _potrace_binary(image):
        raise ConversionError("Potrace accepts only exact black/white artwork here. Choose VTracer for coloured/antialiased inputs; this application will not silently threshold them.")
    # Trace both black and white as separate masks. Transparent pixels stay clear,
    # and white lettering/holes remain white where genuinely present in source.
    width,height = image.size
    svg = ET.Element("svg", {"xmlns":"http://www.w3.org/2000/svg", "width":str(width), "height":str(height), "viewBox":f"0 0 {width} {height}"})
    for index, color in enumerate(((255,255,255),(0,0,0))):
        _check(cancelled)
        mask = Image.new("1", image.size, 1)
        data = [0 if a == 255 and (r,g,b)==color else 1 for r,g,b,a in image.getdata()]
        if 0 not in data:
            continue
        mask.putdata(data)
        pbm, traced = folder/f"mask-{index}.pbm", folder/f"mask-{index}.svg"
        mask.save(pbm)
        # Verified supported Potrace 1.16 options: -t area threshold, -a corner
        # smoothing, -O optimization tolerance; -n disables curve optimization.
        args=[executable,"--svg","--output",traced,"--turdsize",str(options.min_component),"--alphamax",str(options.smoothing*1.334),"--opttolerance",str(max(0.01,(10-options.detail)*0.02))]
        if options.smoothing == 0:
            args.append("--longcurve")
        args.append(pbm)
        code,log=run_process(args,cancelled=cancelled)
        if code:
            raise ConversionError(f"Potrace failed: {log[-2000:]}")
        root=ET.parse(traced).getroot()
        group=ET.SubElement(svg,"g",{"fill":"#%02x%02x%02x"%color})
        for child in root:
            if child.tag.rsplit("}",1)[-1] == "g":
                child.attrib.pop("fill",None)
                group.append(child)
    target=folder/"traced.svg"
    ET.ElementTree(svg).write(target,encoding="utf-8",xml_declaration=True)
    return target


def _prepare_svg(path, size, dpi):
    width,height=size
    root=ET.parse(path).getroot()
    for node in root.iter():
        tag=node.tag.rsplit("}",1)[-1]
        if tag in ("image","foreignObject","text","filter"):
            raise ConversionError(f"Tracer produced unsupported {tag} content; output was not exported.")
    root.set("viewBox",f"0 0 {width} {height}")
    root.set("width",f"{width*72/dpi:.12f}pt")
    root.set("height",f"{height*72/dpi:.12f}pt")
    ET.ElementTree(root).write(path,encoding="utf-8",xml_declaration=True)
    return sum(node.tag.rsplit("}",1)[-1] in ("path","rect","polygon") for node in root.iter())


def _metrics(reference, preview):
    diff=ImageChops.difference(reference.convert("RGB"),preview.convert("RGB"))
    stats=ImageStat.Stat(diff)
    histogram=diff.histogram()
    # A pixel differs if any RGB channel differs by >8/255 (AA tolerance).
    changed=sum(max(pixel)>8 for pixel in diff.getdata())/(diff.width*diff.height)
    mae=sum(stats.mean)/3
    return {"mae":mae,"max_error":max(v[1] for v in stats.extrema),"changed_fraction":changed,"substantial":mae>5 or changed>0.1,"note":"sRGB byte-space RGB differences against final rendering, >8/255 changed-pixel threshold; not perceptual or proof of detail preservation"},diff


def _hash(path):
    digest=hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda:source.read(1024*1024),b""):
            digest.update(chunk)
    return digest.hexdigest()


def _unique_folder(output_dir, stem):
    output_dir=Path(output_dir)
    output_dir.mkdir(parents=True,exist_ok=True)
    for index in range(10000):
        candidate=output_dir/(stem if index==0 else f"{stem}-{index+1}")
        try:
            candidate.mkdir()
            return candidate
        except FileExistsError:
            continue
    raise ConversionError("Too many outputs with this name. Select another output folder.")


def convert(source, output_dir, options=None, progress=None, cancelled=None):
    options=options or Options()
    progress=progress or (lambda fraction,message:None)
    source=Path(source).resolve()
    target_folder=None
    try:
        if options.mode not in ("smooth","pixel") or options.tracer not in ("auto","vtracer","potrace"):
            raise ConversionError("Unknown conversion mode or tracer.")
        if not 0<=options.detail<=10 or not 0<=options.smoothing<=1 or not 1<=options.color_precision<=8 or options.min_component<0:
            raise ConversionError("Detail must be 0–10, smoothing 0–1, colour precision 1–8 and component removal nonnegative.")
        if options.alpha_policy not in ("ask","composite","retain"):
            raise ConversionError("Unknown transparency policy.")
        validate_line_options(options)
        info=estimate(source,options)
        if info["large"] and not options.force_large:
            raise LargeImageError(f"Large image: {info['pixels']:,} pixels; estimated worst-case vector size {info['estimated_bytes']/1_000_000:.1f} MB. Confirm explicitly to continue, or reduce the source dimensions yourself.")
        if info["pixels"]>16_000_000:
            raise LargeImageError("Images over 16 million pixels exceed this application's memory safety limit. Reduce dimensions explicitly before conversion.")
        _check(cancelled)
        original_hash=_hash(source)
        progress(0.02,"Reading PNG and colour profile")
        rgba,dpi,warnings=_load(source,options)
        if options.line_color != "all":
            rgba = extract_lines(rgba, options)
            warnings.append(f"Intentional {options.line_color}-line extraction at brightness cutoff {options.line_threshold}; other pixels are transparent. Opacity cutoff is {options.line_alpha_threshold}/255. Metrics compare against the extracted artwork.")
        width,height=rgba.size
        alpha=rgba.getchannel("A")
        histogram=alpha.histogram()
        partial=bool(sum(histogram[1:255]))
        if partial and options.alpha_policy=="ask":
            raise AlphaPolicyRequired("This PNG contains partially transparent pixels. EPS cannot preserve them. Choose a background to composite against, or explicitly export SVG/PDF pixel vectors with alpha instead.")
        retain=partial and options.alpha_policy=="retain"
        background=ImageColor.getrgb(options.background)
        if len(background)!=3:
            raise ConversionError("Choose an opaque RGB background such as #ffffff.")
        if partial and options.alpha_policy=="composite":
            rgba=Image.alpha_composite(Image.new("RGBA",rgba.size,(*background,255)),rgba)
            warnings.append(f"Partial transparency was explicitly composited against {options.background}; EPS has no general alpha.")
        if retain:
            warnings.append("EPS was not created: partial alpha requires SVG/PDF. This explicit alternative uses pixel rectangles and preserves the pixel grid, not smooth reconstructed edges.")
        else:
            warnings.append("EPS uses DeviceRGB sRGB numeric values; EPS has no portable embedded sRGB profile. Viewer/printer colour settings can change appearance.")
        target_folder=_unique_folder(output_dir,source.stem+"-"+("alpha" if retain else options.mode))
        stem=source.stem
        reference_path=target_folder/(stem+".reference.png")
        preview_path=target_folder/(stem+".preview.png")
        reference=Image.alpha_composite(Image.new("RGBA",rgba.size,(*background,255)),rgba).convert("RGB")
        reference.save(reference_path)
        rgba.save(target_folder/(stem+".reference-alpha.png"))
        progress(0.1,"Building editable vector geometry")
        _check(cancelled)
        with tempfile.TemporaryDirectory(prefix="pngvector-") as temp:
            work=Path(temp)
            extras=[target_folder/(stem+".reference-alpha.png"), target_folder/(stem+".preview-alpha.png")]
            if options.mode=="pixel" or retain:
                if options.mode=="pixel":
                    warnings.append("Pixel mode preserves the decoded sRGB grid and ignores smooth-trace detail, smoothing, colour precision and component-removal controls. Stair-stepped edges remain; rectangle counts and files can be very large.")
                rectangles=pixel_rectangles(rgba,cancelled)
                path_count=len(rectangles)
                svg=target_folder/(stem+".svg")
                _write_pixel_svg(svg,rectangles,rgba.size,dpi,cancelled)
                extras.append(svg)
                if retain:
                    pdf=target_folder/(stem+".pdf")
                    _cairo_export("pdf",svg,pdf,cancelled)
                    output=svg
                    extras.append(pdf)
                    # This alpha-retaining branch has no EPS; rendered preview
                    # comes from retained SVG and is clearly labelled in report.
                    raster=work/"alpha-preview.png"
                    _cairo_export("png",svg,raster,cancelled,output_width=width,output_height=height)
                    shutil.copyfile(raster, target_folder/(stem+".preview-alpha.png"))
                    with Image.open(raster) as rendered:
                        preview=Image.alpha_composite(Image.new("RGBA",rgba.size,(*background,255)),rendered.convert("RGBA")).convert("RGB")
                    preview.save(preview_path)
                    inspection={"vector_only":True,"format":"SVG/PDF rectangles, no EPS", "preview_source":"SVG"}
                else:
                    output=target_folder/(stem+".eps")
                    _write_pixel_eps(output,rectangles,rgba.size,dpi,cancelled)
                    inspection=None
                rectangle_path=target_folder/(stem+".rectangles.json")
                with rectangle_path.open("w",encoding="utf-8") as manifest:
                    manifest.write(json.dumps({"width":width,"height":height,"colourspace":"sRGB 8-bit"})[:-1]+',"rectangles":[')
                    for index,rectangle in enumerate(rectangles):
                        if index % 2048 == 0:
                            _check(cancelled)
                        if index:
                            manifest.write(',')
                        manifest.write(json.dumps(rectangle))
                    manifest.write(']}')
                extras.append(rectangle_path)
            else:
                if histogram[0]==width*height:
                    svg=work/"traced.svg"
                    _write_pixel_svg(svg,[],rgba.size,dpi,cancelled)
                elif options.tracer=="potrace" or (options.tracer=="auto" and _potrace_binary(rgba)):
                    svg=_trace_potrace(rgba,work,options,cancelled)
                else:
                    svg=_trace_vtracer(rgba,work,options,cancelled)
                path_count=_prepare_svg(svg,rgba.size,dpi)
                saved_svg=target_folder/(stem+".svg")
                shutil.copyfile(svg,saved_svg)
                extras.append(saved_svg)
                output=target_folder/(stem+".eps")
                progress(0.65,"Exporting vector paths to EPS with Cairo")
                _cairo_export("eps",svg,output,cancelled)
                set_canvas_bbox(output,width*72/dpi,height*72/dpi)
                inspection=None
                warnings.append("Smooth tracing approximates raster artwork. Gradients/photos and antialiased lettering may produce many paths or visible differences; inspect enlarged previews. Lower path counts can reduce fidelity.")
                if options.smoothing == 0:
                    warnings.append("Preserve detail uses unsimplified traced contours (VTracer fitting=none, Potrace alphamax=0). Pixel stair steps can remain; the detail slider affects curve fitting only when smoothing is enabled.")
                else:
                    warnings.append("Curve smoothing was enabled explicitly. It can round corners, change counters and remove tiny geometric features even when small-component cleanup is zero; compare at high zoom.")
            _check(cancelled)
            if not retain:
                progress(0.75,"Checking EPS painting operators and rendering final EPS")
                inspection=inspect_eps(output,cancelled=cancelled)
                if not inspection["valid_header"] or not inspection["vector_only"]:
                    raise ConversionError(f"EPS verification failed: embedded raster painting or invalid header detected ({inspection}).")
                render_eps(output,preview_path,width,height,dpi,options.background,cancelled=cancelled, transparent_output=target_folder/(stem+".preview-alpha.png"))
        progress(0.9,"Comparing final rendering with source")
        with Image.open(preview_path) as preview:
            metrics,difference=_metrics(reference,preview)
        diff_path=target_folder/(stem+".difference.png")
        difference.save(diff_path)
        extras.append(diff_path)
        if metrics["substantial"]:
            warnings.append("Substantial rendered differences detected. Inspect counters, thin lines, colour boundaries and enlarged differences before using this artwork.")
        _check(cancelled)
        if _hash(source)!=original_hash:
            raise ConversionError("Input file changed during conversion; output rejected. Stop any program editing the source and retry.")
        report={"source":str(source),"source_sha256":original_hash,"source_unchanged":True,"output":output.name,"options":asdict(options),"width":width,"height":height,"dpi":dpi,"size_points":[width*72/dpi,height*72/dpi],"path_count":path_count,"metrics":metrics,"warnings":warnings,"inspection":inspection,"preview_source":"SVG alpha alternative" if retain else "final EPS via Ghostscript"}
        report_path=target_folder/(stem+".report.json")
        report_path.write_text(json.dumps(report,indent=2),encoding="utf-8")
        extras.append(report_path)
        progress(1.0,"Conversion verified")
        return Result(source,output,preview_path,reference_path,metrics,warnings,width,height,dpi,path_count,extras,comparison_background=options.background)
    except (AlphaPolicyRequired,LargeImageError,Cancelled):
        if target_folder:
            shutil.rmtree(target_folder,ignore_errors=True)
        raise
    except InterruptedError as exc:
        if target_folder:
            shutil.rmtree(target_folder,ignore_errors=True)
        raise Cancelled("Conversion cancelled; partial output removed.") from exc
    except Exception as exc:
        if target_folder:
            shutil.rmtree(target_folder,ignore_errors=True)
        if isinstance(exc,ConversionError):
            raise
        raise ConversionError(f"Conversion failed: {exc}") from exc
