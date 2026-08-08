import argparse
import hashlib
import io
import re
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from PIL import Image


CANVAS_SIZE = (1024, 1024)
HEADER_WIDTH = 900
HEADER_TOP = 40
STAMP_WIDTH = 465
STAMP_LEFT = 520
STAMP_TOP = 165
MINIGAME_STAMP_TOP = 225
SOURCES = {
    "adventure_time": "resources/lce skinpack icons/adventure_time.png",
    "chinese_mythology": "resources/store icons/chinesemytho_preview.png",
    "city": "resources/store icons/city_preview.png",
    "egyptian_mythology": "resources/store icons/egyptianmytho_preview.png",
    "fallout": "resources/store icons/fallout_preview.png",
    "fantasy": "resources/store icons/fantasy_preview.png",
    "festive": "resources/store icons/festive_preview.png",
    "glide": "resources/store icons/glide_preview_clean.webp",
    "greek_mythology": "resources/store icons/greekmytho_preview.png",
    "halloween_2015": "resources/store icons/halloween_preview.png",
    "halo": "resources/lce skinpack icons/mashuphalo.png",
    "littlebigplanet": "resources/store icons/lbp_preview.png",
    "mass_effect": "resources/store icons/masseffect_preview.png",
    "norse_mythology": "resources/store icons/norsemytho_preview.png",
    "pirates_of_the_caribbean": "resources/store icons/potc_preview.png",
    "skyrim": "resources/lce skinpack icons/skyrim.png",
    "steampunk": "resources/store icons/steampunk_preview.png",
    "steven_universe": "resources/lce skinpack icons/stevenuniverse.png",
    "the_nightmare_before_christmas": "resources/lce skinpack icons/thenightmarebeforechristmas.png",
    "toy_story": "resources/lce skinpack icons/toystorymash-up.png",
    "tumble": "resources/store icons/tumble_preview_clean.png",
}
NO_HEADER = {"glide", "littlebigplanet", "toy_story", "tumble"}


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def cover(image: Image.Image) -> Image.Image:
    image = image.convert("RGBA")
    scale = max(CANVAS_SIZE[0] / image.width, CANVAS_SIZE[1] / image.height)
    resized = image.resize((round(image.width * scale), round(image.height * scale)), Image.Resampling.LANCZOS)
    left = (resized.width - CANVAS_SIZE[0]) // 2
    top = (resized.height - CANVAS_SIZE[1]) // 2
    return resized.crop((left, top, left + CANVAS_SIZE[0], top + CANVAS_SIZE[1]))


def resize_width(image: Image.Image, width: int) -> Image.Image:
    height = round(image.height * width / image.width)
    return image.resize((width, height), Image.Resampling.LANCZOS)


def make_preview(source: Path, output: Path, header: Image.Image, stamp: Image.Image, skip_header: bool) -> None:
    canvas = cover(Image.open(source))
    stamp_image = resize_width(stamp, STAMP_WIDTH)
    if not skip_header:
        header_image = resize_width(header, HEADER_WIDTH)
        canvas.alpha_composite(header_image, ((CANVAS_SIZE[0] - HEADER_WIDTH) // 2, HEADER_TOP))
    canvas.alpha_composite(stamp_image, (STAMP_LEFT, MINIGAME_STAMP_TOP if skip_header else STAMP_TOP))
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output)


def replace_pack_icon(zip_path: Path, preview: Path) -> None:
    buffer = io.BytesIO()
    Image.open(preview).save(buffer, format="PNG")
    replacement = buffer.getvalue()
    temp_path = zip_path.with_suffix(".zip.tmp")
    with ZipFile(zip_path, "r") as source, ZipFile(temp_path, "w", ZIP_DEFLATED) as target:
        replaced = False
        for item in source.infolist():
            if item.filename == "pack.png":
                target.writestr(item, replacement)
                replaced = True
            else:
                target.writestr(item, source.read(item.filename))
        if not replaced:
            target.writestr("pack.png", replacement)
    temp_path.replace(zip_path)


def md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def update_index(index_path: Path, results: dict[str, tuple[str, str]]) -> None:
    text = index_path.read_text(encoding="utf-8")
    for soundtrack_id, (zip_hash, image_hash) in results.items():
        zip_pattern = rf"(soundtracks/packs/{re.escape(soundtrack_id)}\.zip\?checksum=)[0-9a-f]+"
        image_pattern = rf"(soundtracks/preview_images/{re.escape(soundtrack_id)}_preview\.png\?checksum=)[0-9a-f]+"
        text = re.sub(zip_pattern, rf"\g<1>{zip_hash}", text)
        text = re.sub(image_pattern, rf"\g<1>{image_hash}", text)
    index_path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Regenerate soundtrack preview images and pack icons.")
    parser.add_argument("--id", action="append", choices=sorted(SOURCES), help="Generate only the selected soundtrack. May be repeated.")
    parser.add_argument("--force", action="store_true", help="Regenerate previews that already exist.")
    args = parser.parse_args()

    root = repo_root()
    header = Image.open(root / "resources" / "preview logos" / "header.png").convert("RGBA")
    stamp = Image.open(root / "resources" / "preview logos" / "soundtrack.png").convert("RGBA")
    selected = args.id or list(SOURCES)
    results = {}
    for soundtrack_id in selected:
        source = root / SOURCES[soundtrack_id]
        preview = root / "soundtracks" / "preview_images" / f"{soundtrack_id}_preview.png"
        zip_path = root / "soundtracks" / "packs" / f"{soundtrack_id}.zip"
        if preview.exists() and not args.force:
            continue
        if not source.exists():
            raise FileNotFoundError(source)
        if not zip_path.exists():
            raise FileNotFoundError(zip_path)
        make_preview(source, preview, header, stamp, soundtrack_id in NO_HEADER)
        replace_pack_icon(zip_path, preview)
        results[soundtrack_id] = (md5(zip_path), md5(preview))
        print(f"{soundtrack_id}: zip={results[soundtrack_id][0]} image={results[soundtrack_id][1]}")
    update_index(root / "soundtracks" / "index.json", results)
    print(f"Regenerated {len(results)} soundtrack previews")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
