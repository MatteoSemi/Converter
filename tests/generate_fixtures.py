"""Deterministic fixtures; no downloads, fonts, or external accounts required."""
from pathlib import Path
import shutil

from PIL import Image, ImageDraw, ImageCms


def generate(directory: Path) -> dict[str, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    result = {}

    def save(name, image, **kwargs):
        path = directory / f"{name}.png"
        image.save(path, **kwargs)
        result[name] = path

    # Block letter outlines produce deterministic counters without font rendering.
    im = Image.new("RGB", (216, 82), "white")
    d = ImageDraw.Draw(im)
    for x, holes in [(15, [(21, 23, 35, 58)]), (54, [(60, 23, 75, 35), (60, 44, 75, 58)]), (96, [(102, 23, 118, 35), (102, 44, 118, 58)])]:
        d.rectangle((x, 16, x + 27, 65), fill="black")
        for hole in holes:
            d.rectangle(hole, fill="white")
    d.polygon([(139, 65), (151, 16), (162, 16), (175, 65), (167, 65), (163, 49), (149, 49), (145, 65)], fill="black")
    d.polygon([(151, 41), (156, 25), (161, 41)], fill="white")
    d.rectangle((186, 22, 190, 49), fill="black")
    d.rectangle((186, 58, 190, 62), fill="black")
    d.point((202, 62), fill="black")
    save("lettering counters punctuation", im)

    im = Image.new("RGBA", (131, 79), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.line((7, 9, 118, 9), fill=(0, 0, 0, 255), width=1)
    d.line((9, 15, 9, 65), fill=(240, 30, 20, 255), width=1)
    d.polygon([(22, 65), (45, 19), (60, 63)], fill=(30, 80, 220, 255))
    d.point((111, 64), fill=(0, 0, 0, 255))
    d.rectangle((115, 60, 116, 61), fill=(0, 170, 40, 255))
    d.rectangle((77, 24, 82, 55), fill=(220, 30, 160, 255))
    d.rectangle((84, 24, 89, 55), fill=(220, 30, 160, 255))
    save("thin corners dots", im)

    im = Image.new("RGBA", (160, 112), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rectangle((12, 14, 79, 97), fill=(245, 50, 30, 255))
    d.rectangle((80, 14, 147, 97), fill=(20, 110, 235, 255))
    d.rectangle((35, 35, 124, 77), fill=(0, 0, 0, 0))
    d.rectangle((56, 47, 103, 65), fill=(25, 180, 70, 255))
    d.rectangle((70, 53, 90, 59), fill=(0, 0, 0, 0))
    save("adjacent colours nested holes", im)

    im = Image.new("RGBA", (100, 70), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((15, 10, 85, 60), fill=(220, 30, 100, 255))
    d.ellipse((32, 23, 68, 47), fill=(0, 0, 0, 0))
    save("binary transparency", im)

    im = Image.new("RGBA", (88, 62), (210, 20, 90, 0))
    pix = im.load()
    for y in range(8, 54):
        for x in range(8, 80):
            pix[x, y] = (210, 20, 90, min(255, (x - 7) * 4))
    save("partial edges", im)

    im = Image.new("RGB", (96, 48))
    pix = im.load()
    for y in range(im.height):
        for x in range(im.width):
            pix[x, y] = (round(x * 255 / 95), round(y * 255 / 47), round((x + y) * 255 / 142))
    save("gradient", im)

    # The checked-in thumbnail comes from Matplotlib's public-domain US Navy photo.
    photo = Path(__file__).parent / "assets" / "grace_hopper.png"
    if not photo.exists():
        candidates = [Path("/usr/share/matplotlib/mpl-data/sample_data/grace_hopper.jpg"), Path("/opt/codex/runtimes/codex-primary-runtime/dependencies/python/lib/python3.12/site-packages/matplotlib/mpl-data/sample_data/grace_hopper.jpg")]
        source = next((p for p in candidates if p.exists()), None)
        if source is None:
            raise FileNotFoundError("The bundled tests/assets/grace_hopper.png photograph is missing")
        photo.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(source) as image:
            image.convert("RGB").resize((80, 96), Image.Resampling.LANCZOS).save(photo)
    save("photograph", Image.open(photo).convert("RGB"))

    im = Image.new("RGB", (263, 91), "white")
    d = ImageDraw.Draw(im)
    d.rectangle((83, 19, 119, 51), fill=(250, 0, 0))
    d.rectangle((120, 19, 153, 34), fill=(0, 0, 250))
    d.rectangle((120, 35, 139, 51), fill=(0, 190, 0))
    save("wide margins", im, dpi=(144, 144))
    save("blank", Image.new("RGB", (67, 43), "white"))
    save("fully transparent", Image.new("RGBA", (67, 43), (254, 1, 129, 0)))
    save("large", Image.new("RGB", (4096, 3072), (25, 80, 140)))
    save("Übergrößé 文件 with spaces", Image.new("RGB", (21, 13), (44, 55, 66)))
    save("icc sRGB", Image.new("RGB", (24, 16), (20, 100, 200)), icc_profile=ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes())
    save("16 bit gray", Image.new("I;16", (20, 10), 40000))
    result["corrupted"] = directory / "corrupted.png"
    result["corrupted"].write_bytes(b"\x89PNG\r\n\x1a\nnot a PNG file")
    return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    for key, value in generate(args.directory).items():
        print(f"{key}: {value}")
