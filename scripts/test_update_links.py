import json
import unittest
from pathlib import Path

from scripts import update_links


class VersionRailTests(unittest.TestCase):
    def test_multiversion_rails(self) -> None:
        config = update_links.load_config(update_links.repo_root())
        rails = update_links.configured_version_rails(config)
        actual = {rail.id: (rail.min_version, rail.match_version) for rail in rails}
        self.assertEqual([
            "1_20_1",
            "1_20_4",
            "1_21_1",
            "1_21_3",
            "1_21_4",
            "1_21_5",
        ], [rail.id for rail in rails[:6]])
        self.assertEqual(("1.20", "1.20.1"), actual["1_20_1"])
        self.assertEqual(("1.20.3", "1.20.4"), actual["1_20_4"])
        self.assertEqual(("1.21", "1.21.1"), actual["1_21_1"])
        self.assertEqual(("1.21.2", "1.21.3"), actual["1_21_3"])
        self.assertEqual(("1.21.4", "1.21.4"), actual["1_21_4"])
        self.assertEqual(("1.21.5", "1.21.5"), actual["1_21_5"])
        self.assertEqual(("1.21.11", "1.21.11"), actual["1_21_11"])

    def test_variants_are_sorted_by_lower_bound(self) -> None:
        existing = [
            {
                "id": "26_1_2",
                "minVersion": "26.1.2",
                "downloadURI": "https://example.com/current.zip",
            }
        ]
        additions = [
            update_links.VariantAddition("index.json", "packs[0]", "https://example.com/base.zip", "1_21_1", "1.21", "https://example.com/old.zip", 0)
        ]
        variants = update_links.merge_variants(existing, additions)
        self.assertEqual(["1_21_1", "26_1_2"], [variant["id"] for variant in variants])

    def test_match_version_is_independent_from_lower_bound(self) -> None:
        config = {
            "modrinth": {
                "loader": "minecraft",
                "versionTypes": ["release"],
            }
        }
        cache = {
            "project": [
                {
                    "version_type": "release",
                    "loaders": ["minecraft"],
                    "game_versions": ["1.20.4"],
                    "files": [
                        {
                            "primary": True,
                            "filename": "pack.zip",
                            "url": "https://example.com/pack.zip",
                        }
                    ],
                }
            ]
        }
        url = update_links.choose_modrinth_url("project", config, cache, [], "1.20.4", False, None, ())
        self.assertEqual("https://example.com/pack.zip", url)

    def test_auto_discovery_backfills_matching_rails(self) -> None:
        config = {
            "modrinth": {
                "loader": "minecraft",
                "versionTypes": ["release"],
                "rails": [
                    {
                        "id": "1_21_5",
                        "minVersion": "1.21.5",
                        "matchVersion": "1.21.5",
                    },
                    {
                        "id": "26_2",
                        "minVersion": "26.2",
                        "matchVersion": "26.2",
                    },
                ],
                "autoDiscoverRails": {
                    "enabled": True,
                    "gameVersionPattern": r"^26\.[0-9]+(\.[0-9]+)?$",
                    "minimumProjects": 1,
                    "minimumVersion": "26.1.2",
                },
            }
        }
        ref = update_links.UrlRef(
            Path("index.json"),
            "index.json",
            "packs[0].downloadURI",
            "downloadURI",
            "https://cdn.modrinth.com/data/project/versions/old/pack.zip",
            "category",
            "pack",
            "packs[0]",
            None,
            None,
        )
        cache = {
            "project": [
                {
                    "version_type": "release",
                    "loaders": ["minecraft"],
                    "game_versions": ["26.1.3"],
                    "files": [
                        {
                            "primary": True,
                            "filename": "pack.zip",
                            "url": "https://example.com/pack.zip",
                        }
                    ],
                }
            ]
        }
        configured = update_links.configured_version_rails(config)
        rails, additions = update_links.discover_version_rails([ref], config, cache, [], {}, configured, ())
        self.assertIn("26_1_3", {rail.id for rail in rails})
        self.assertEqual("26.1.3", additions[0].match_version)

    def test_variant_rails_are_ordered(self) -> None:
        root = update_links.repo_root()
        config = update_links.load_config(root)
        rails = {rail.id: rail for rail in update_links.configured_version_rails(config)}
        for path in update_links.index_files(root):
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assert_ordered(data, path, rails)

    def assert_ordered(self, value: object, path: Path, rails: dict[str, update_links.VersionRail]) -> None:
        if isinstance(value, list):
            for item in value:
                self.assert_ordered(item, path, rails)
            return
        if not isinstance(value, dict):
            return
        for property_name in ("downloadVariants", "worldTemplateVariants"):
            variants = value.get(property_name)
            if not isinstance(variants, list):
                continue
            versions = []
            for item in variants:
                if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not isinstance(item.get("minVersion"), str):
                    continue
                rail_id = item["id"]
                self.assertIn(rail_id, rails, f"{path}: {rail_id}")
                self.assertEqual(rails[rail_id].min_version, item["minVersion"], f"{path}: {rail_id}")
                versions.append(item["minVersion"])
            self.assertEqual(sorted(versions, key=update_links.version_sort_key), versions, f"{path}: {property_name}")
        for item in value.values():
            self.assert_ordered(item, path, rails)


if __name__ == "__main__":
    unittest.main()
