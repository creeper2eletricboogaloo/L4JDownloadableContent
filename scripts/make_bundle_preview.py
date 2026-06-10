import argparse
import re
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw, ImageFont


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_template_dir() -> Path:
    return repo_root() / "resources" / "bundle preview image template"


def resolve_path(value: str | None, base: Path) -> Path | None:
    if value is None:
        return None
    path = Path(value)
    if path.is_absolute():
        return path
    return base / path


def resolve_template_path(value: str, template_dir: Path, bundle_dir: Path | None) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    if bundle_dir:
        local_path = bundle_dir / path
        if local_path.exists():
            return local_path
    return template_dir / path


def parse_box(value: str) -> tuple[int, int, int, int]:
    parts = [int(part.strip()) for part in value.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("Expected x,y,width,height")
    return parts[0], parts[1], parts[2], parts[3]


def parse_color(value: str) -> tuple[int, int, int, int]:
    return ImageColor.getcolor(value, "RGBA")


def cover_image(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    image = image.convert("RGBA")
    scale = max(size[0] / image.width, size[1] / image.height)
    resized = image.resize((round(image.width * scale), round(image.height * scale)), Image.Resampling.LANCZOS)
    left = (resized.width - size[0]) // 2
    top = (resized.height - size[1]) // 2
    return resized.crop((left, top, left + size[0], top + size[1]))


def contain_image(image: Image.Image, max_width: int, max_height: int) -> Image.Image:
    image = image.convert("RGBA")
    scale = min(max_width / image.width, max_height / image.height)
    return image.resize((round(image.width * scale), round(image.height * scale)), Image.Resampling.LANCZOS)


def paste_header(canvas: Image.Image, path: Path, width: int, y: int) -> None:
    header = Image.open(path).convert("RGBA")
    height = round(header.height * (width / header.width))
    header = header.resize((width, height), Image.Resampling.LANCZOS)
    x = (canvas.width - header.width) // 2
    canvas.alpha_composite(header, (x, y))


def paste_photos(canvas: Image.Image, paths: list[Path], box: tuple[int, int, int, int]) -> None:
    x, y, width, height = box
    slot_width = width // 3
    for index, path in enumerate(paths):
        photo = cover_image(Image.open(path), (slot_width, height))
        canvas.alpha_composite(photo, (x + slot_width * index, y))


def extend_canvas_for_rows(canvas: Image.Image, box: tuple[int, int, int, int], extra_rows: int) -> Image.Image:
    if extra_rows <= 0:
        return canvas
    _, y, _, height = box
    row_bottom = y + height
    new_canvas = Image.new("RGBA", (canvas.width, canvas.height + height * extra_rows), (0, 0, 0, 0))
    new_canvas.alpha_composite(canvas.crop((0, 0, canvas.width, row_bottom)), (0, 0))
    bottom = canvas.crop((0, row_bottom, canvas.width, canvas.height))
    new_canvas.alpha_composite(bottom, (0, row_bottom + height * extra_rows))
    return new_canvas


def load_font(path: Path | None, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if path and path.exists():
        return ImageFont.truetype(str(path), size)
    for fallback in [
        Path("C:/Windows/Fonts/bahnschrift.ttf"),
        Path("C:/Windows/Fonts/impact.ttf"),
        Path("C:/Windows/Fonts/arialbd.ttf"),
    ]:
        if fallback.exists():
            return ImageFont.truetype(str(fallback), size)
    return ImageFont.load_default()


def fit_text_font(text: str, font_path: Path | None, max_width: int, start_size: int, min_size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    size = start_size
    while size >= min_size:
        font = load_font(font_path, size)
        bbox = ImageDraw.Draw(Image.new("RGBA", (1, 1))).textbbox((0, 0), text, font=font, stroke_width=2)
        if bbox[2] - bbox[0] <= max_width:
            return font
        size -= 1
    return load_font(font_path, min_size)


def paste_text(canvas: Image.Image, text: str, font_path: Path | None, size: int, y: int, fill: tuple[int, int, int, int], stroke: tuple[int, int, int, int], stroke_width: int, max_width: int) -> None:
    font = fit_text_font(text, font_path, max_width, size, 14)
    draw = ImageDraw.Draw(canvas)
    bbox = draw.textbbox((0, 0), text, font=font, stroke_width=stroke_width)
    x = (canvas.width - (bbox[2] - bbox[0])) // 2
    draw.text((x - bbox[0], y - bbox[1]), text, font=font, fill=fill, stroke_width=stroke_width, stroke_fill=stroke)


def paste_text_image(canvas: Image.Image, path: Path, y: int, max_width: int, max_height: int) -> None:
    text_image = contain_image(Image.open(path), max_width, max_height)
    x = (canvas.width - text_image.width) // 2
    canvas.alpha_composite(text_image, (x, y))


def default_output(text: str | None) -> Path:
    stem = "bundle_preview"
    if text:
        stem = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or stem
    return default_template_dir() / f"{stem}.png"


def require_file(path: Path, label: str) -> None:
    if not path.exists():
        raise FileNotFoundError(f"Missing {label}: {path}")


def second_row_names(bundle_dir: Path) -> list[str] | None:
    for names in [
        ["left1.png", "middle1.png", "right1.png"],
        ["left2.png", "middle2.png", "right2.png"],
    ]:
        if any((bundle_dir / name).exists() for name in names):
            return names
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="cropped_bundle_clean.png")
    parser.add_argument("--header", default="header.png")
    parser.add_argument("--bundle-dir")
    parser.add_argument("--output-name", default="preview.png")
    parser.add_argument("--no-header", action="store_true")
    parser.add_argument("--header-width", type=int, default=590)
    parser.add_argument("--header-y", type=int, default=22)
    parser.add_argument("--photos", nargs=3)
    parser.add_argument("--photos2", nargs=3)
    parser.add_argument("--photo-strip", type=parse_box, default=parse_box("0,158,900,214"))
    parser.add_argument("--text")
    parser.add_argument("--text-image")
    parser.add_argument("--text-y", type=int)
    parser.add_argument("--text-size", type=int, default=34)
    parser.add_argument("--text-font")
    parser.add_argument("--text-fill", type=parse_color, default=parse_color("#ffffff"))
    parser.add_argument("--text-stroke", type=parse_color, default=parse_color("#151515"))
    parser.add_argument("--text-stroke-width", type=int, default=2)
    parser.add_argument("--text-max-width", type=int, default=760)
    parser.add_argument("--text-image-max-height", type=int, default=70)
    parser.add_argument("--output")
    args = parser.parse_args()

    template_dir = default_template_dir()
    bundle_dir = resolve_path(args.bundle_dir, repo_root()) if args.bundle_dir else None
    input_base = bundle_dir or repo_root()
    if bundle_dir and args.photos is None:
        args.photos = ["left.png", "middle.png", "right.png"]
    if bundle_dir and args.photos2 is None:
        args.photos2 = second_row_names(bundle_dir)
    if bundle_dir and args.text_image is None and args.text is None and (bundle_dir / "text.png").exists():
        args.text_image = "text.png"
    base_path = resolve_template_path(args.base, template_dir, bundle_dir)
    if args.output:
        output_path = resolve_path(args.output, input_base)
    elif bundle_dir:
        output_path = bundle_dir / args.output_name
    else:
        output_path = default_output(args.text)
    require_file(base_path, "base image")
    canvas = Image.open(base_path).convert("RGBA")
    extra_rows = 1 if args.photos2 else 0
    if args.text_y is None:
        args.text_y = 390 + args.photo_strip[3] * extra_rows
    canvas = extend_canvas_for_rows(canvas, args.photo_strip, extra_rows)

    if args.photos:
        photo_paths = [resolve_path(path, input_base) for path in args.photos]
        for index, path in enumerate(photo_paths, 1):
            require_file(path, f"photo {index}")
        paste_photos(canvas, photo_paths, args.photo_strip)

    if args.photos2:
        photo_paths = [resolve_path(path, input_base) for path in args.photos2]
        for index, path in enumerate(photo_paths, 1):
            require_file(path, f"second row photo {index}")
        x, y, width, height = args.photo_strip
        paste_photos(canvas, photo_paths, (x, y + height, width, height))

    if not args.no_header:
        header_path = resolve_template_path(args.header, template_dir, bundle_dir)
        require_file(header_path, "header image")
        paste_header(canvas, header_path, args.header_width, args.header_y)

    if args.text_image:
        text_image_path = resolve_path(args.text_image, input_base)
        require_file(text_image_path, "text image")
        paste_text_image(canvas, text_image_path, args.text_y, args.text_max_width, args.text_image_max_height)
    elif args.text:
        paste_text(canvas, args.text, resolve_path(args.text_font, input_base), args.text_size, args.text_y, args.text_fill, args.text_stroke, args.text_stroke_width, args.text_max_width)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(output_path, quality=95)
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
