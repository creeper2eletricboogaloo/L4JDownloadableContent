import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlparse
from urllib.request import Request, urlopen


OWNER = "creeper2eletricboogaloo"
REPO = "L4JDownloadableContent"
RAW_HOST = "raw.githubusercontent.com"
MODRINTH_CDN_HOST = "cdn.modrinth.com"
MODRINTH_API = "https://api.modrinth.com/v2"
USER_AGENT = "L4JDownloadableContent-link-updater/1.0"


@dataclass(frozen=True)
class UrlRef:
    file_path: Path
    rel_file: str
    path: str
    prop: str
    value: str
    category: str | None
    pack_id: str | None
    pack_path: str | None
    variant_id: str | None
    variant_min_version: str | None


@dataclass(frozen=True)
class Change:
    kind: str
    rel_file: str
    path: str
    old: str
    new: str


@dataclass(frozen=True)
class VariantAddition:
    rel_file: str
    path: str
    base_url: str
    variant_id: str
    min_version: str
    url: str
    order: int


@dataclass(frozen=True)
class VariantTrack:
    id: str
    min_version: str
    order: int


@dataclass(frozen=True)
class ConfigTrackAddition:
    variant_id: str
    min_version: str
    project_count: int


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_config(root: Path) -> dict:
    path = root / "scripts" / "update_links_config.json"
    return json.loads(path.read_text(encoding="utf-8"))


def index_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("index.json") if ".git" not in p.parts)


def file_category(root: Path, file_path: Path) -> str:
    rel = file_path.relative_to(root).as_posix()
    return rel.split("/", 1)[0]


def is_url(value: str) -> bool:
    return value.startswith("http://") or value.startswith("https://")


def is_variant_object(value: dict) -> bool:
    return isinstance(value.get("id"), str) and isinstance(value.get("minVersion"), str) and isinstance(value.get("downloadURI"), str)


def is_pack_object(value: dict) -> bool:
    if not isinstance(value.get("id"), str) or is_variant_object(value):
        return False
    keys = {"downloadURI", "imageUrl", "downloadVariants", "bundlePacks", "worldTemplateDownloadURI", "worldTemplateVariants"}
    return any(key in value for key in keys)


def collect_urls(root: Path, file_path: Path, data: object) -> list[UrlRef]:
    refs: list[UrlRef] = []
    rel_file = file_path.relative_to(root).as_posix()
    base_category = file_category(root, file_path)

    def walk(value: object, path: str, prop: str, pack: tuple[str | None, str | None, str | None] | None, variant: tuple[str | None, str | None] | None) -> None:
        if isinstance(value, str):
            if is_url(value):
                category, pack_id, pack_path = pack if pack else (None, None, None)
                variant_id, variant_min_version = variant if variant else (None, None)
                refs.append(UrlRef(file_path, rel_file, path, prop, value, category, pack_id, pack_path, variant_id, variant_min_version))
            return
        if isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]", prop, pack, variant)
            return
        if isinstance(value, dict):
            next_pack = pack
            next_variant = variant
            if is_variant_object(value):
                next_variant = (value.get("id"), value.get("minVersion"))
            elif is_pack_object(value):
                next_pack = (value.get("categoryId") or base_category, value.get("id"), path)
                next_variant = None
            for key, item in value.items():
                next_path = f"{path}.{key}" if path else key
                walk(item, next_path, key, next_pack, next_variant)

    walk(data, "", "", None, None)
    return refs


def parse_same_repo_raw(url: str) -> list[str] | None:
    parsed = urlparse(url)
    if parsed.netloc != RAW_HOST:
        return None
    parts = [unquote(part) for part in parsed.path.split("/") if part]
    if len(parts) < 4 or parts[0] != OWNER or parts[1] != REPO:
        return None
    if parts[2] == "main":
        return parts[3:]
    if len(parts) >= 6 and parts[2:5] == ["refs", "heads", "main"]:
        return parts[5:]
    return None


def raw_url_for(rel_parts: list[str], checksum: str) -> str:
    rel = "/".join(rel_parts)
    encoded = quote(rel, safe="/._-()")
    return f"https://{RAW_HOST}/{OWNER}/{REPO}/main/{encoded}?checksum={checksum}"


def md5_file(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_modrinth_download(url: str) -> dict | None:
    parsed = urlparse(url)
    if parsed.netloc != MODRINTH_CDN_HOST:
        return None
    parts = [unquote(part) for part in parsed.path.split("/") if part]
    if len(parts) < 5 or parts[0] != "data" or parts[2] != "versions":
        return None
    return {
        "project": parts[1],
        "version": parts[3],
        "filename": "/".join(parts[4:]),
    }


def pack_key(ref: UrlRef) -> str | None:
    if not ref.category or not ref.pack_id:
        return None
    return f"{ref.category}:{ref.pack_id}"


def configured_variant_tracks(config: dict) -> list[VariantTrack]:
    variants = config.get("modrinth", {}).get("variants", [])
    if isinstance(variants, dict):
        items = [{"id": variant_id, "minVersion": item.get("minVersion")} for variant_id, item in variants.items() if isinstance(item, dict)]
    elif isinstance(variants, list):
        items = variants
    else:
        items = []
    tracks: list[VariantTrack] = []
    seen: set[str] = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        variant_id = item.get("id")
        min_version = item.get("minVersion")
        if not isinstance(variant_id, str) or not isinstance(min_version, str) or not variant_id or not min_version:
            continue
        if variant_id in seen:
            continue
        seen.add(variant_id)
        tracks.append(VariantTrack(variant_id, min_version, index))
    return tracks


def version_sort_key(value: str) -> tuple:
    parts = re.split(r"[.\-+_]", value)
    key = []
    for part in parts:
        if part.isdigit():
            key.append((0, int(part)))
        else:
            key.append((1, part))
    return tuple(key)


def variant_id_for_game_version(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z]+", "_", value).strip("_").lower()


def discovery_settings(config: dict) -> tuple[bool, re.Pattern[str], int]:
    settings = config.get("modrinth", {}).get("autoDiscoverVariants", {})
    if not isinstance(settings, dict):
        settings = {}
    enabled = settings.get("enabled") is True
    pattern = str(settings.get("gameVersionPattern", r"^26\.[0-9]+(\.[0-9]+)?$"))
    minimum_projects = settings.get("minimumProjects", 3)
    if not isinstance(minimum_projects, int) or minimum_projects < 1:
        minimum_projects = 3
    return enabled, re.compile(pattern), minimum_projects


def load_json_files(root: Path) -> tuple[dict[Path, str], dict[Path, object], list[UrlRef]]:
    texts: dict[Path, str] = {}
    data_by_file: dict[Path, object] = {}
    refs: list[UrlRef] = []
    for file_path in index_files(root):
        text = file_path.read_text(encoding="utf-8")
        data = json.loads(text)
        texts[file_path] = text
        data_by_file[file_path] = data
        refs.extend(collect_urls(root, file_path, data))
    return texts, data_by_file, refs


def fetch_modrinth_versions(project: str, cache: dict[str, list[dict]], warnings: list[str]) -> list[dict]:
    if project in cache:
        return cache[project]
    request = Request(f"{MODRINTH_API}/project/{project}/version", headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urlopen(request, timeout=30) as response:
            versions = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError) as error:
        warnings.append(f"Modrinth lookup failed for {project}: {error}")
        versions = []
    versions.sort(key=lambda item: item.get("date_published", ""), reverse=True)
    cache[project] = versions
    return versions


def selected_file(version: dict, required_filename: str | None) -> dict | None:
    files = version.get("files") or []
    zip_files = [item for item in files if item.get("primary") is True and str(item.get("filename", "")).lower().endswith(".zip")]
    if required_filename is not None:
        zip_files = [item for item in zip_files if item.get("filename") == required_filename]
    return zip_files[0] if zip_files else None


def version_matches(version: dict, config: dict, required_game_version: str | None, require_base: bool, required_filename: str | None) -> bool:
    modrinth = config.get("modrinth", {})
    if version.get("version_type") not in set(modrinth.get("versionTypes", ["release"])):
        return False
    if modrinth.get("loader", "minecraft") not in set(version.get("loaders") or []):
        return False
    game_versions = version.get("game_versions") or []
    if required_game_version is not None and required_game_version not in game_versions:
        return False
    if require_base and any(re.match(r"^26\.", item) for item in game_versions):
        return False
    return selected_file(version, required_filename) is not None


def choose_modrinth_url(project: str, config: dict, cache: dict[str, list[dict]], warnings: list[str], required_game_version: str | None, require_base: bool, required_filename: str | None) -> str | None:
    for version in fetch_modrinth_versions(project, cache, warnings):
        if version_matches(version, config, required_game_version, require_base, required_filename):
            file_item = selected_file(version, required_filename)
            if file_item and file_item.get("url"):
                return file_item["url"]
    return None


def project_filename_rules(refs: list[UrlRef]) -> dict[str, bool]:
    projects: dict[str, set[str]] = {}
    filenames: dict[str, set[str]] = {}
    for ref in refs:
        if ref.prop != "downloadURI":
            continue
        info = parse_modrinth_download(ref.value)
        key = pack_key(ref)
        if not info or not key:
            continue
        projects.setdefault(info["project"], set()).add(key)
        filenames.setdefault(info["project"], set()).add(info["filename"])
    return {project: len(projects.get(project, set())) > 1 and len(filenames.get(project, set())) > 1 for project in set(projects) | set(filenames)}


def discover_variant_tracks(refs: list[UrlRef], config: dict, cache: dict[str, list[dict]], warnings: list[str], exact_filename: dict[str, bool], configured_tracks: list[VariantTrack]) -> tuple[list[VariantTrack], list[ConfigTrackAddition]]:
    enabled, pattern, minimum_projects = discovery_settings(config)
    if not enabled:
        return configured_tracks, []
    configured_versions = {track.min_version for track in configured_tracks}
    configured_ids = {track.id for track in configured_tracks}
    newest_configured = max((track.min_version for track in configured_tracks), key=version_sort_key, default="")
    candidates: dict[str, set[str]] = {}
    for ref in refs:
        if ref.prop != "downloadURI" or ref.variant_id:
            continue
        info = parse_modrinth_download(ref.value)
        if info is None:
            continue
        required_filename = info["filename"] if exact_filename.get(info["project"]) else None
        for version in fetch_modrinth_versions(info["project"], cache, warnings):
            if not version_matches(version, config, None, False, required_filename):
                continue
            for game_version in version.get("game_versions") or []:
                if not isinstance(game_version, str) or not pattern.match(game_version):
                    continue
                if newest_configured and version_sort_key(game_version) <= version_sort_key(newest_configured):
                    continue
                if game_version in configured_versions:
                    continue
                candidates.setdefault(game_version, set()).add(info["project"])
    additions: list[ConfigTrackAddition] = []
    tracks = list(configured_tracks)
    order = max((track.order for track in tracks), default=-1) + 1
    for min_version in sorted(candidates, key=version_sort_key):
        project_count = len(candidates[min_version])
        if project_count < minimum_projects:
            continue
        variant_id = variant_id_for_game_version(min_version)
        if variant_id in configured_ids:
            warnings.append(f"Discovered variant {min_version} maps to configured id {variant_id}")
            continue
        additions.append(ConfigTrackAddition(variant_id, min_version, project_count))
        tracks.append(VariantTrack(variant_id, min_version, order))
        configured_ids.add(variant_id)
        configured_versions.add(min_version)
        order += 1
    return tracks, additions


def build_changes(root: Path, refs: list[UrlRef], config: dict) -> tuple[list[Change], list[VariantAddition], list[ConfigTrackAddition], list[str]]:
    changes: list[Change] = []
    additions: list[VariantAddition] = []
    warnings: list[str] = []
    cache: dict[str, list[dict]] = {}
    exact_filename = project_filename_rules(refs)
    variant_tracks, config_additions = discover_variant_tracks(refs, config, cache, warnings, exact_filename, configured_variant_tracks(config))
    variant_by_id = {track.id: track for track in variant_tracks}
    existing_variants = {(ref.rel_file, ref.pack_path, ref.variant_id) for ref in refs if ref.prop == "downloadURI" and ref.variant_id and ".downloadVariants[" in ref.path}
    variant_urls: dict[tuple[str, str | None], set[str]] = {}
    for ref in refs:
        if ref.prop == "downloadURI" and ref.variant_id and ".downloadVariants[" in ref.path:
            variant_urls.setdefault((ref.rel_file, ref.pack_path), set()).add(ref.value)

    for ref in refs:
        rel_parts = parse_same_repo_raw(ref.value)
        if rel_parts is not None:
            local_path = root.joinpath(*rel_parts)
            if not local_path.exists():
                warnings.append(f"Missing local file for {ref.rel_file} {ref.path}: {'/'.join(rel_parts)}")
                continue
            new_url = raw_url_for(rel_parts, md5_file(local_path))
            if new_url != ref.value:
                changes.append(Change("local", ref.rel_file, ref.path, ref.value, new_url))
            continue
        if ref.prop != "downloadURI":
            continue
        info = parse_modrinth_download(ref.value)
        if info is None:
            continue
        key = pack_key(ref)
        if key is None:
            warnings.append(f"Missing pack identity for {ref.rel_file} {ref.path}")
            continue
        required_filename = info["filename"] if exact_filename.get(info["project"]) else None
        if ref.variant_id:
            variant_track = variant_by_id.get(ref.variant_id)
            if not variant_track:
                warnings.append(f"Unconfigured variant {ref.variant_id} for {key} at {ref.rel_file} {ref.path}")
                continue
            new_url = choose_modrinth_url(info["project"], config, cache, warnings, variant_track.min_version, False, required_filename)
            if new_url is None:
                warnings.append(f"No Modrinth match for {key} variant {ref.variant_id} at {ref.rel_file} {ref.path}")
                continue
            if new_url != ref.value:
                changes.append(Change("modrinth", ref.rel_file, ref.path, ref.value, new_url))
            continue
        new_url = choose_modrinth_url(info["project"], config, cache, warnings, None, True, required_filename)
        if new_url is None:
            warnings.append(f"No Modrinth base match for {key} at {ref.rel_file} {ref.path}")
        elif new_url in variant_urls.get((ref.rel_file, ref.pack_path), set()):
            warnings.append(f"Base Modrinth match duplicates an existing variant for {key} at {ref.rel_file} {ref.path}")
        elif new_url != ref.value:
            changes.append(Change("modrinth", ref.rel_file, ref.path, ref.value, new_url))
        anchor_url = new_url if new_url is not None and new_url != ref.value and new_url not in variant_urls.get((ref.rel_file, ref.pack_path), set()) else ref.value
        for track in variant_tracks:
            if (ref.rel_file, ref.pack_path, track.id) in existing_variants:
                continue
            candidate = choose_modrinth_url(info["project"], config, cache, warnings, track.min_version, False, required_filename)
            if candidate is not None:
                additions.append(VariantAddition(ref.rel_file, ref.pack_path or ref.path, anchor_url, track.id, track.min_version, candidate, track.order))
    return changes, sorted(set(additions), key=lambda item: (item.rel_file, item.path, item.order, item.variant_id)), config_additions, sorted(set(warnings))


def build_local_changes(root: Path, refs: list[UrlRef]) -> tuple[list[Change], list[str]]:
    changes: list[Change] = []
    warnings: list[str] = []
    for ref in refs:
        rel_parts = parse_same_repo_raw(ref.value)
        if rel_parts is None:
            continue
        local_path = root.joinpath(*rel_parts)
        if not local_path.exists():
            warnings.append(f"Missing local file for {ref.rel_file} {ref.path}: {'/'.join(rel_parts)}")
            continue
        new_url = raw_url_for(rel_parts, md5_file(local_path))
        if new_url != ref.value:
            changes.append(Change("local", ref.rel_file, ref.path, ref.value, new_url))
    return changes, sorted(set(warnings))


def replacement_token(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def skip_string(text: str, index: int) -> int:
    index += 1
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == '"':
            return index + 1
        index += 1
    return index


def matching_bracket(text: str, start: int) -> int | None:
    if start >= len(text) or text[start] not in "{[":
        return None
    pairs = {"{": "}", "[": "]"}
    stack = [text[start]]
    index = start + 1
    while index < len(text):
        char = text[index]
        if char == '"':
            index = skip_string(text, index)
            continue
        if char in "{[":
            stack.append(char)
            index += 1
            continue
        if char in "}]":
            if not stack or pairs[stack[-1]] != char:
                return None
            stack.pop()
            if not stack:
                return index
        index += 1
    return None


def containing_object_bounds(text: str, position: int) -> tuple[int, int] | None:
    stack: list[tuple[str, int]] = []
    index = 0
    while index < position:
        char = text[index]
        if char == '"':
            end = skip_string(text, index)
            if end > position:
                break
            index = end
            continue
        if char in "{[":
            stack.append((char, index))
        elif char in "}]" and stack:
            stack.pop()
        index += 1
    for char, start in reversed(stack):
        if char == "{":
            end = matching_bracket(text, start)
            if end is not None:
                return start, end
    return None


def find_property_value_span(text: str, start: int, end: int, name: str) -> tuple[int, int, int] | None:
    token = replacement_token(name)
    depth = 0
    index = start + 1
    while index < end:
        char = text[index]
        if char == '"':
            if depth == 0 and text.startswith(token, index):
                key_end = index + len(token)
                colon = key_end
                while colon < end and text[colon].isspace():
                    colon += 1
                if colon < end and text[colon] == ":":
                    value_start = colon + 1
                    while value_start < end and text[value_start].isspace():
                        value_start += 1
                    if value_start >= end:
                        return None
                    value_char = text[value_start]
                    if value_char in "[{":
                        value_end = matching_bracket(text, value_start)
                        if value_end is None:
                            return None
                        return value_start, value_end + 1, index
                    if value_char == '"':
                        return value_start, skip_string(text, value_start), index
                    value_end = value_start
                    while value_end < end and text[value_end] not in ",}":
                        value_end += 1
                    while value_end > value_start and text[value_end - 1].isspace():
                        value_end -= 1
                    return value_start, value_end, index
            index = skip_string(text, index)
            continue
        if char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
        index += 1
    return None


def path_tokens(path: str) -> list[str | int]:
    tokens: list[str | int] = []
    index = 0
    while index < len(path):
        if path[index] == ".":
            index += 1
            continue
        if path[index] == "[":
            end = path.find("]", index)
            if end == -1:
                return []
            value = path[index + 1:end]
            if not value.isdigit():
                return []
            tokens.append(int(value))
            index = end + 1
            continue
        end = index
        while end < len(path) and path[end] not in ".[":
            end += 1
        tokens.append(path[index:end])
        index = end
    return tokens


def find_array_item_span(text: str, start: int, end: int, item_index: int) -> tuple[int, int] | None:
    if start >= len(text) or text[start] != "[":
        return None
    index = start + 1
    current = 0
    while index < end:
        while index < end and text[index].isspace():
            index += 1
        if index < end and text[index] == ",":
            index += 1
            continue
        if index >= end or text[index] == "]":
            return None
        value_start = index
        if text[index] in "[{":
            close = matching_bracket(text, index)
            if close is None:
                return None
            value_end = close + 1
        elif text[index] == '"':
            value_end = skip_string(text, index)
        else:
            value_end = index
            while value_end < end and text[value_end] not in ",]":
                value_end += 1
            while value_end > value_start and text[value_end - 1].isspace():
                value_end -= 1
        if current == item_index:
            return value_start, value_end
        current += 1
        index = value_end
    return None


def find_value_span_by_path(text: str, path: str) -> tuple[int, int] | None:
    start = 0
    while start < len(text) and text[start].isspace():
        start += 1
    end = matching_bracket(text, start)
    if end is None:
        return None
    current_start = start
    current_end = end + 1
    for token in path_tokens(path):
        if isinstance(token, str):
            if current_start >= len(text) or text[current_start] != "{":
                return None
            span = find_property_value_span(text, current_start, current_end - 1, token)
            if span is None:
                return None
            current_start, current_end = span[0], span[1]
        else:
            if current_start >= len(text) or text[current_start] != "[":
                return None
            span = find_array_item_span(text, current_start, current_end - 1, token)
            if span is None:
                return None
            current_start, current_end = span
    return current_start, current_end


def find_pack_object_bounds(text: str, addition: VariantAddition) -> tuple[int, int] | None:
    span = find_value_span_by_path(text, addition.path)
    if span is None:
        return None
    if span[0] < len(text) and text[span[0]] == "{":
        return span[0], span[1] - 1
    return containing_object_bounds(text, span[0])


def line_indent(text: str, index: int) -> str:
    line_start = text.rfind("\n", 0, index) + 1
    match = re.match(r"\s*", text[line_start:index])
    return match.group(0) if match else ""


def merge_variants(existing: object, additions: list[VariantAddition]) -> list[dict]:
    variants = list(existing) if isinstance(existing, list) else []
    seen = {item.get("id") for item in variants if isinstance(item, dict)}
    for addition in sorted(additions, key=lambda item: (item.order, item.variant_id)):
        if addition.variant_id in seen:
            continue
        variants.append({
            "id": addition.variant_id,
            "minVersion": addition.min_version,
            "downloadURI": addition.url,
        })
        seen.add(addition.variant_id)
    return variants


def variants_array_value(variants: list[dict], indent: str) -> str:
    child = indent + "  "
    item_indent = child + "  "
    blocks = []
    for variant in variants:
        blocks.append(
            f"{child}{{\n"
            f'{item_indent}"id": {replacement_token(variant.get("id", ""))},\n'
            f'{item_indent}"minVersion": {replacement_token(variant.get("minVersion", ""))},\n'
            f'{item_indent}"downloadURI": {replacement_token(variant.get("downloadURI", ""))}\n'
            f"{child}}}"
        )
    return "[\n" + ",\n".join(blocks) + f"\n{indent}]"


def variants_property_block(variants: list[dict], indent: str, gap: str, trailing_comma: bool) -> str:
    suffix = "," if trailing_comma else ""
    return f'{indent}"downloadVariants"{gap}{variants_array_value(variants, indent)}{suffix}\n'


def apply_variant_additions(text: str, additions: list[VariantAddition]) -> tuple[str, str | None]:
    bounds = find_pack_object_bounds(text, additions[0])
    if bounds is None:
        return text, f"Could not find pack object in {additions[0].rel_file}: {additions[0].path}"
    try:
        pack = json.loads(text[bounds[0]:bounds[1] + 1])
    except json.JSONDecodeError:
        return text, f"Could not parse pack object in {additions[0].rel_file}: {additions[0].path}"
    if not isinstance(pack, dict) or pack.get("downloadURI") != additions[0].base_url:
        return text, f"Pack URL mismatch in {additions[0].rel_file}: {additions[0].path}"
    existing = pack.get("downloadVariants") if isinstance(pack, dict) else []
    variants = merge_variants(existing, additions)
    span = find_property_value_span(text, bounds[0], bounds[1], "downloadVariants")
    if span is not None:
        value_start, value_end, key_start = span
        indent = line_indent(text, key_start)
        return text[:value_start] + variants_array_value(variants, indent) + text[value_end:], None
    download_span = find_property_value_span(text, bounds[0], bounds[1], "downloadURI")
    if download_span is None:
        return text, f"Could not find downloadURI in {additions[0].rel_file}: {additions[0].path}"
    line_start = text.rfind("\n", 0, download_span[2]) + 1
    line_end = text.find("\n", download_span[2])
    if line_end == -1:
        line_end = len(text)
    line = text[line_start:line_end]
    match = re.match(r'(\s*)"downloadURI"(\s*:\s*)', line)
    if not match:
        return text, f"Could not determine indentation in {additions[0].rel_file}: {additions[0].path}"
    next_text = text[line_end + 1:bounds[1]].lstrip()
    block = variants_property_block(variants, match.group(1), match.group(2), bool(next_text and not next_text.startswith("}")))
    comma = "" if line.rstrip().endswith(",") else ","
    return text[:line_end] + comma + text[line_end:line_end + 1] + block + text[line_end + 1:], None


def write_config_tracks(root: Path, config: dict, additions: list[ConfigTrackAddition]) -> None:
    modrinth = config.setdefault("modrinth", {})
    variants = modrinth.get("variants", [])
    if isinstance(variants, dict):
        variants = [{"id": variant_id, "minVersion": item.get("minVersion")} for variant_id, item in variants.items() if isinstance(item, dict)]
    elif not isinstance(variants, list):
        variants = []
    existing_ids = {item.get("id") for item in variants if isinstance(item, dict)}
    existing_versions = {item.get("minVersion") for item in variants if isinstance(item, dict)}
    for addition in additions:
        if addition.variant_id in existing_ids or addition.min_version in existing_versions:
            continue
        variants.append({
            "id": addition.variant_id,
            "minVersion": addition.min_version,
        })
        existing_ids.add(addition.variant_id)
        existing_versions.add(addition.min_version)
    modrinth["variants"] = variants
    path = root / "scripts" / "update_links_config.json"
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def write_changes(root: Path, texts: dict[Path, str], changes: list[Change], additions: list[VariantAddition]) -> list[str]:
    errors: list[str] = []
    by_file: dict[str, dict[str, str]] = {}
    additions_by_file: dict[str, dict[str, list[VariantAddition]]] = {}
    file_paths: dict[str, Path] = {}
    for change in changes:
        mapping = by_file.setdefault(change.rel_file, {})
        if change.old in mapping and mapping[change.old] != change.new:
            errors.append(f"Conflicting replacement for {change.rel_file}: {change.old}")
            continue
        mapping[change.old] = change.new
    for addition in additions:
        additions_by_file.setdefault(addition.rel_file, {}).setdefault(addition.path, []).append(addition)
    for path in texts:
        file_paths[path.relative_to(root).as_posix()] = path
    next_texts: dict[Path, str] = {}
    affected = set(by_file) | set(additions_by_file)
    for rel_file in sorted(affected):
        path = file_paths[rel_file]
        text = texts[path]
        for old, new in by_file.get(rel_file, {}).items():
            old_token = replacement_token(old)
            new_token = replacement_token(new)
            if old_token not in text:
                errors.append(f"Could not find URL token in {rel_file}: {old}")
                continue
            text = text.replace(old_token, new_token)
        for grouped_additions in additions_by_file.get(rel_file, {}).values():
            text, error = apply_variant_additions(text, grouped_additions)
            if error:
                errors.append(error)
        next_texts[path] = text
    if errors:
        return errors
    for path, text in next_texts.items():
        path.write_text(text, encoding="utf-8")
    return errors


def print_report(changes: list[Change], additions: list[VariantAddition], config_additions: list[ConfigTrackAddition], warnings: list[str], wrote: bool) -> None:
    action = "Updated" if wrote else "Would update"
    total = len(changes) + len(additions) + len(config_additions)
    if total:
        counts: dict[str, int] = {}
        for change in changes:
            counts[change.kind] = counts.get(change.kind, 0) + 1
        if additions:
            counts["variant"] = len(additions)
        if config_additions:
            counts["config"] = len(config_additions)
        print(f"{action} {total} values")
        for kind in sorted(counts):
            print(f"{kind}: {counts[kind]}")
        for change in changes[:50]:
            print(f"{change.kind} {change.rel_file} {change.path}")
        shown = min(len(changes), 50)
        for config_addition in config_additions[: max(0, 50 - shown)]:
            print(f"config variant {config_addition.variant_id} minVersion {config_addition.min_version} projects {config_addition.project_count}")
        shown += min(len(config_additions), max(0, 50 - shown))
        for addition in additions[: max(0, 50 - shown)]:
            print(f"variant {addition.rel_file} {addition.path}")
        if total > 50:
            print(f"... {total - 50} more")
    else:
        print("No URL updates needed")
    if warnings:
        print(f"Warnings: {len(warnings)}")
        for warning in warnings[:80]:
            print(warning)
        if len(warnings) > 80:
            print(f"... {len(warnings) - 80} more")


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    parser.add_argument("--local-only", action="store_true", help="Only update same-repository raw GitHub checksum URLs.")
    args = parser.parse_args()
    root = repo_root()
    texts, _, refs = load_json_files(root)
    if args.local_only:
        changes, warnings = build_local_changes(root, refs)
        additions: list[VariantAddition] = []
        config_additions: list[ConfigTrackAddition] = []
    else:
        config = load_config(root)
        changes, additions, config_additions, warnings = build_changes(root, refs, config)
    if args.write and (changes or additions or config_additions):
        errors = write_changes(root, texts, changes, additions)
        if errors:
            for error in errors:
                print(error, file=sys.stderr)
            return 2
        if not args.local_only and config_additions:
            write_config_tracks(root, config, config_additions)
    print_report(changes, additions, config_additions, warnings, args.write and bool(changes or additions or config_additions))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
