import argparse
import io
import subprocess
import sys
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from PIL import Image


LOGO_WIDTH = 335
LOGO_TOP = 14
CANVAS_SIZE = (360, 360)
SKINPACK_MATCH_Y = 125
SKINPACK_MATCH_THRESHOLD = 1.0
SKIP_SKINPACKS = {
    "fromtheshadows_preview.png",
    "doctor_who_volume_1_preview.png",
    "doctor_who_volume_2_preview.png",
}


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def cover_image(image: Image.Image, size: tuple[int, int] = CANVAS_SIZE) -> Image.Image:
    image = image.convert("RGBA")
    scale = max(size[0] / image.width, size[1] / image.height)
    resized = image.resize((round(image.width * scale), round(image.height * scale)), Image.Resampling.LANCZOS)
    left = (resized.width - size[0]) // 2
    top = (resized.height - size[1]) // 2
    return resized.crop((left, top, left + size[0], top + size[1]))


def add_header(base: Image.Image, logo: Image.Image) -> Image.Image:
    canvas = cover_image(base)
    logo_height = round(logo.height * LOGO_WIDTH / logo.width)
    overlay = logo.resize((LOGO_WIDTH, logo_height), Image.Resampling.LANCZOS)
    canvas.alpha_composite(overlay, ((canvas.width - overlay.width) // 2, LOGO_TOP))
    return canvas.convert("RGB")


def save_preview(base_path: Path, output_path: Path, logo: Image.Image) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    add_header(Image.open(base_path), logo).save(output_path)


def paste_logo(base: Image.Image, logo: Image.Image, logo_width: int, top: int) -> Image.Image:
    canvas = cover_image(base)
    logo_height = round(logo.height * logo_width / logo.width)
    overlay = logo.resize((logo_width, logo_height), Image.Resampling.LANCZOS)
    canvas.alpha_composite(overlay, ((canvas.width - overlay.width) // 2, top))
    return canvas.convert("RGB")


def save_logo_preview(base_path: Path, logo_path: Path, output_path: Path, logo_width: int, top: int) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    paste_logo(Image.open(base_path), Image.open(logo_path).convert("RGBA"), logo_width, top).save(output_path)


def replace_zip_png(zip_path: Path, member_name: str, image_path: Path) -> None:
    buffer = io.BytesIO()
    Image.open(image_path).convert("RGB").save(buffer, format="PNG")
    replacement = buffer.getvalue()
    temp_path = zip_path.with_suffix(zip_path.suffix + ".tmp")
    with ZipFile(zip_path, "r") as zin, ZipFile(temp_path, "w", ZIP_DEFLATED) as zout:
        replaced = False
        for item in zin.infolist():
            if item.filename == member_name:
                zout.writestr(item, replacement)
                replaced = True
            else:
                zout.writestr(item, zin.read(item.filename))
        if not replaced:
            zout.writestr(member_name, replacement)
    temp_path.replace(zip_path)


def mse_lower_region(left: Image.Image, right: Image.Image) -> float:
    left = cover_image(left).convert("RGB").crop((0, SKINPACK_MATCH_Y, 360, 360))
    right = cover_image(right).convert("RGB").crop((0, SKINPACK_MATCH_Y, 360, 360))
    total = 0
    count = 0
    for a, b in zip(left.tobytes(), right.tobytes()):
        delta = a - b
        total += delta * delta
        count += 1
    return total / count


def regenerate_skinpacks(root: Path, logo: Image.Image) -> list[tuple[Path, Path]]:
    source_dir = root / "resources" / "lce skinpack icons"
    output_dir = root / "skinpacks" / "preview_images"
    sources = [(path, Image.open(path)) for path in sorted(source_dir.glob("*.png"))]
    written: list[tuple[Path, Path]] = []
    for output_path in sorted(output_dir.glob("*.png")):
        if output_path.name in SKIP_SKINPACKS:
            continue
        output_image = Image.open(output_path)
        matches = sorted((mse_lower_region(output_image, source_image), source_path) for source_path, source_image in sources)
        if not matches or matches[0][0] > SKINPACK_MATCH_THRESHOLD:
            continue
        source_path = matches[0][1]
        save_preview(source_path, output_path, logo)
        written.append((source_path, output_path))
    return written


def regenerate_texture_packs(root: Path, logo: Image.Image) -> list[tuple[Path, Path]]:
    source_dir = root / "resources" / "store icons"
    output_dir = root / "texture_packs" / "preview_images"
    written: list[tuple[Path, Path]] = []
    for output_path in sorted(output_dir.glob("*.png")):
        source_path = source_dir / output_path.name
        if not source_path.exists():
            continue
        save_preview(source_path, output_path, logo)
        written.append((source_path, output_path))
    return written


def regenerate_doctor_who(root: Path, logo: Image.Image) -> list[tuple[Path, Path]]:
    source_path = root / "resources" / "store icons" / "doctor_who_preview_clean.png"
    outputs = [
        root / "skinpacks" / "preview_images" / "doctor_who_volume_1_preview.png",
        root / "skinpacks" / "preview_images" / "doctor_who_volume_2_preview.png",
    ]
    if not source_path.exists():
        raise FileNotFoundError(f"Missing clean Doctor Who base: {source_path}")
    written: list[tuple[Path, Path]] = []
    for output_path in outputs:
        save_preview(source_path, output_path, logo)
        written.append((source_path, output_path))
    return written


def regenerate_from_the_shadows(root: Path) -> list[tuple[Path, Path]]:
    source_path = root / "resources" / "lce skinpack icons" / "fromtheshadowsskinpack.png"
    logo_path = root / "resources" / "preview logos" / "fromtheshadowslogo.png"
    output_path = root / "skinpacks" / "preview_images" / "fromtheshadows_preview.png"
    save_logo_preview(source_path, logo_path, output_path, 338, 8)
    return [(source_path, output_path)]


def regenerate_legacy4j_specials(root: Path) -> list[tuple[Path, Path]]:
    preview_dir = root / "Legacy4J" / "preview_images"
    pack_dir = root / "Legacy4J" / "packs"
    logo_dir = root / "resources" / "preview logos"
    background = root / "resources" / "blanklegacy4jbackground.png"
    compressed_base = root / "resources" / "store icons" / "compressed_legacy_music_clean.png"
    items = [
        (compressed_base, logo_dir / "originalmusiclogo.png", preview_dir / "compressed_legacy_music_preview.png", 340, 14, pack_dir / "compressed_legacy_music.zip"),
        (background, logo_dir / "legacybedslogo.png", preview_dir / "legacy_beds_preview.png", 300, 104, pack_dir / "legacybeds.zip"),
        (background, logo_dir / "pre113waterlogo.png", preview_dir / "pre_1_13_waters_preview.png", 300, 104, pack_dir / "pre_1_13_waters.zip"),
        (background, logo_dir / "legacytitleslogo.png", preview_dir / "legacy_titles_preview.png", 300, 104, None),
    ]
    written: list[tuple[Path, Path]] = []
    for source_path, logo_path, output_path, logo_width, top, zip_path in items:
        if not source_path.exists():
            raise FileNotFoundError(f"Missing clean base: {source_path}")
        save_logo_preview(source_path, logo_path, output_path, logo_width, top)
        if zip_path is not None:
            replace_zip_png(zip_path, "pack.png", output_path)
        written.append((source_path, output_path))
    return written


def regenerate_legacy4j_generic(root: Path, logo: Image.Image) -> list[tuple[Path, Path]]:
    clean_dir = root / "resources" / "store icons" / "legacy4j_clean"
    preview_dir = root / "Legacy4J" / "preview_images"
    items = [
        ("faithful_legacy_clean.png", "faithful_legacy_preview.png"),
        ("old_ui_clean.png", "old_ui_preview_image.png"),
        ("ore4j_clean.png", "ore4j_preview.png"),
        ("legacy_tutorial_worlds_clean.png", "legacy_tutorial_worlds_preview.png"),
        ("legacy_panorama_tu5_clean.png", "legacy_panorama_tu5_preview.png"),
        ("legacy_panorama_tu7_clean.png", "legacy_panorama_tu7_preview.png"),
        ("legacy_panorama_tu12_clean.png", "legacy_panorama_tu12_preview.png"),
        ("legacy_panorama_tu20_clean.png", "legacy_panorama_tu20_preview.png"),
        ("legacy_panorama_tu31_clean.png", "legacy_panorama_tu31_preview.png"),
        ("legacy_panorama_tu46_clean.png", "legacy_panorama_tu46_preview.png"),
    ]
    written: list[tuple[Path, Path]] = []
    for source_name, output_name in items:
        source_path = clean_dir / source_name
        output_path = preview_dir / output_name
        if not source_path.exists():
            raise FileNotFoundError(f"Missing clean Legacy4J base: {source_path}")
        save_preview(source_path, output_path, logo)
        written.append((source_path, output_path))
    return written


def regenerate_starter_and_hide_seek(root: Path, logo: Image.Image) -> list[tuple[Path, Path]]:
    source_path = Path.home() / "Downloads" / "pack.png"
    if not source_path.exists():
        return []
    outputs = [
        root / "STARTERPACKS" / "preview_images" / "starterpacks_bundle_preview.png",
        root / "community_skinpacks" / "preview_images" / "hermitcraft_skin_pack_preview.png",
        root / "community_skinpacks" / "preview_images" / "hide_and_seek_preview.png",
    ]
    written: list[tuple[Path, Path]] = []
    for output_path in outputs:
        save_preview(source_path, output_path, logo)
        written.append((source_path, output_path))
    return written


def regenerate_bundles(root: Path) -> list[tuple[Path, Path]]:
    bundles = {
        "builders bundle": root / "Bundle Packs" / "preview_images" / "builders_pack_preview.png",
        "mega skinpack bundle": root / "Bundle Packs" / "preview_images" / "skinpack_megabundle_preview_image.png",
        "merry bundle": root / "Bundle Packs" / "preview_images" / "merrybundle_preview_image.png",
        "spooky bundle": root / "Bundle Packs" / "preview_images" / "spookybundle_preview_image.png",
    }
    template_root = root / "resources" / "bundle preview image template"
    script = root / "scripts" / "make_bundle_preview.py"
    written: list[tuple[Path, Path]] = []
    for bundle_name, output_path in bundles.items():
        bundle_dir = template_root / bundle_name
        command = [sys.executable, str(script), "--bundle-dir", str(bundle_dir)]
        if bundle_name in {"merry bundle", "spooky bundle"}:
            command.extend(["--text-image-max-height", "36"])
        subprocess.run(command, check=True)
        source_path = bundle_dir / "preview.png"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        Image.open(source_path).convert("RGB").resize(CANVAS_SIZE, Image.Resampling.LANCZOS).save(output_path)
        written.append((source_path, output_path))
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description="Regenerate previews that use the generic Minecraft Legacy4J header.")
    parser.add_argument(
        "--category",
        action="append",
        choices=[
            "skinpacks",
            "texture-packs",
            "doctor-who",
            "from-the-shadows",
            "legacy4j-specials",
            "legacy4j-generic",
            "starterpacks",
            "bundles",
        ],
        help="Limit regeneration to one or more categories.",
    )
    args = parser.parse_args()

    root = repo_root()
    logo = Image.open(root / "resources" / "preview logos" / "header.png").convert("RGBA")
    categories = set(args.category or ["skinpacks", "texture-packs", "doctor-who", "from-the-shadows", "legacy4j-specials", "legacy4j-generic", "starterpacks", "bundles"])
    jobs = []
    if "skinpacks" in categories:
        jobs.extend(regenerate_skinpacks(root, logo))
    if "texture-packs" in categories:
        jobs.extend(regenerate_texture_packs(root, logo))
    if "doctor-who" in categories:
        jobs.extend(regenerate_doctor_who(root, logo))
    if "from-the-shadows" in categories:
        jobs.extend(regenerate_from_the_shadows(root))
    if "legacy4j-specials" in categories:
        jobs.extend(regenerate_legacy4j_specials(root))
    if "legacy4j-generic" in categories:
        jobs.extend(regenerate_legacy4j_generic(root, logo))
    if "starterpacks" in categories:
        jobs.extend(regenerate_starter_and_hide_seek(root, logo))
    if "bundles" in categories:
        jobs.extend(regenerate_bundles(root))

    for source_path, output_path in jobs:
        print(f"{source_path.relative_to(root) if source_path.is_relative_to(root) else source_path} -> {output_path.relative_to(root)}")
    print(f"Regenerated {len(jobs)} previews")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
