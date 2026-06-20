import argparse
import fnmatch
from pathlib import Path

from PIL import Image


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_logo_path() -> Path:
    return repo_root() / "resources" / "bundle preview image template" / "header.png"


def resolve_path(value: str, base: Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return base / path


def iter_inputs(patterns: list[str], base: Path) -> list[Path]:
    paths: list[Path] = []
    for pattern in patterns:
        path = resolve_path(pattern, base)
        if any(char in pattern for char in "*?[]"):
            matches = sorted(base.glob(pattern))
        elif path.is_dir():
            matches = sorted(path.glob("*.png"))
        else:
            matches = [path]
        for match in matches:
            if match.is_file() and match.suffix.lower() in {".png", ".jpg", ".jpeg"} and match not in paths:
                paths.append(match)
    return paths


def is_excluded(path: Path, excludes: list[str], base: Path) -> bool:
    if not excludes:
        return False
    try:
        relative = path.relative_to(base).as_posix()
    except ValueError:
        relative = path.as_posix()
    name = path.name
    return any(fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(name, pattern) for pattern in excludes)


def scaled_logo(logo: Image.Image, width: int) -> Image.Image:
    height = round(logo.height * width / logo.width)
    return logo.resize((width, height), Image.Resampling.LANCZOS)


def add_logo(
    image_path: Path,
    output_path: Path,
    logo: Image.Image,
    logo_width_ratio: float,
    logo_width: int | None,
    top: int,
    opacity: float,
) -> None:
    image = Image.open(image_path).convert("RGBA")
    width = logo_width or round(image.width * logo_width_ratio)
    overlay = scaled_logo(logo, width)
    if opacity < 1:
        alpha = overlay.getchannel("A").point(lambda value: round(value * opacity))
        overlay.putalpha(alpha)
    x = (image.width - overlay.width) // 2
    image.alpha_composite(overlay, (x, top))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path)


def output_for(input_path: Path, input_base: Path, output_dir: Path | None, in_place: bool) -> Path:
    if in_place:
        return input_path
    if output_dir is None:
        return input_path.with_name(f"{input_path.stem}_logo{input_path.suffix}")
    try:
        relative = input_path.relative_to(input_base)
    except ValueError:
        relative = Path(input_path.name)
    return output_dir / relative


def main() -> int:
    parser = argparse.ArgumentParser(description="Add the Minecraft Legacy4J header logo to preview images.")
    parser.add_argument("inputs", nargs="+", help="Files, directories, or glob patterns. Directories process PNG files.")
    parser.add_argument("--logo", default=str(default_logo_path()), help="Transparent logo image to overlay.")
    parser.add_argument("--input-base", default=str(repo_root()), help="Base path for relative inputs and output layout.")
    parser.add_argument("--output-dir", help="Write results under this directory instead of beside inputs.")
    parser.add_argument("--in-place", action="store_true", help="Overwrite input files.")
    parser.add_argument("--exclude", action="append", default=[], help="Glob pattern to skip. May be repeated.")
    parser.add_argument("--logo-width-ratio", type=float, default=0.90, help="Logo width as a fraction of image width.")
    parser.add_argument("--logo-width", type=int, help="Fixed logo width in pixels.")
    parser.add_argument("--top", type=int, default=19, help="Logo top offset in pixels.")
    parser.add_argument("--opacity", type=float, default=1.0, help="Logo opacity from 0.0 to 1.0.")
    parser.add_argument("--dry-run", action="store_true", help="Print files that would be written.")
    args = parser.parse_args()

    input_base = resolve_path(args.input_base, Path.cwd()).resolve()
    logo_path = resolve_path(args.logo, input_base)
    output_dir = resolve_path(args.output_dir, input_base) if args.output_dir else None
    logo = Image.open(logo_path).convert("RGBA")
    paths = iter_inputs(args.inputs, input_base)
    if not paths:
        raise FileNotFoundError("No input preview images matched.")
    for path in paths:
        if is_excluded(path, args.exclude, input_base):
            continue
        out = output_for(path, input_base, output_dir, args.in_place)
        if args.dry_run:
            print(f"{path} -> {out}")
            continue
        add_logo(path, out, logo, args.logo_width_ratio, args.logo_width, args.top, args.opacity)
        print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
