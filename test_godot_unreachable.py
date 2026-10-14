"""Every rule in the scanner has a file in example/ that breaks without it.

Run: python3 -m unittest -v
"""

import contextlib
import io
import os
import unittest

import godot_unreachable as gu

EXAMPLE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "example")


def run(**kw):
    r = gu.analyse(EXAMPLE, kw.get("roots", []), gu.DEFAULT_BENCH, ["*.md"])
    r["dead_paths"] = {d["path"] for d in r["dead"]}
    return r


class Dead(unittest.TestCase):
    def setUp(self):
        self.r = run()

    def test_exactly_these_are_unreachable(self):
        self.assertEqual(self.r["dead_paths"], {
            "res://art/unused_background.png",
            "res://scenes/old_menu.tscn",
            "res://scripts/old_menu.gd",
            "res://spine/hero_v1.webp",
            "res://ui/Left on.webp",
        })

    def test_island_is_dead_although_each_file_is_cited(self):
        # The case a reference count gets wrong.
        self.assertIn(["res://scenes/old_menu.tscn", "res://scripts/old_menu.gd"], self.r["islands"])
        cited = {d["path"]: d["cited_by"] for d in self.r["dead"]}
        self.assertGreater(cited["res://scenes/old_menu.tscn"], 0)
        self.assertGreater(cited["res://scripts/old_menu.gd"], 0)


class Alive(unittest.TestCase):
    def setUp(self):
        self.r = run()

    def assertAlive(self, path):
        self.assertNotIn(path, self.r["dead_paths"])

    def test_main_scene_by_uid(self):
        self.assertAlive("res://scenes/main.tscn")

    def test_scene_referenced_only_by_uid(self):
        self.assertAlive("res://scenes/player.tscn")

    def test_autoload_with_star(self):
        self.assertAlive("res://scripts/events.gd")

    def test_class_name_is_an_edge(self):
        self.assertAlive("res://scripts/inventory.gd")

    def test_path_with_spaces(self):
        self.assertAlive("res://ui/Right on.webp")

    def test_spine_atlas_page_is_relative(self):
        self.assertAlive("res://spine/hero.webp")

    def test_font_fallback_lives_in_the_import_file(self):
        self.assertAlive("res://fonts/body_arabic.ttf")

    def test_translation_csv_through_generated_files(self):
        self.assertAlive("res://translations/text.csv")

    def test_folder_built_at_runtime_is_kept(self):
        self.assertAlive("res://sfx/click.ogg")
        self.assertIn("res://sfx/", self.r["dynamic"])

    def test_project_icon(self):
        self.assertAlive("res://icon.svg")


class Separated(unittest.TestCase):
    def setUp(self):
        self.r = run()

    def test_addon_folder_matched_without_case(self):
        self.assertEqual(self.r["unused_addons"], ["res://Addons/fancy_lines/"])
        self.assertFalse(any("Addons" in p for p in self.r["dead_paths"]))

    def test_benches_are_listed_apart(self):
        self.assertIn("res://tests/test_inventory.tscn", self.r["bench"])
        self.assertNotIn("res://tests/test_inventory.tscn", self.r["dead_paths"])

    def test_file_only_a_bench_uses(self):
        self.assertEqual(self.r["bench_only"], ["res://scripts/debug_hitboxes.gd"])

    def test_extra_root_revives(self):
        r = run(roots=["res://scenes/old_menu.tscn"])
        self.assertNotIn("res://scripts/old_menu.gd", r["dead_paths"])


class DynamicPrefix(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(gu.dynamic_prefix("res://sfx/%s.ogg", ""), "res://sfx/")
        self.assertEqual(gu.dynamic_prefix("res://lines/{0}.png", ""), "res://lines/")
        self.assertEqual(gu.dynamic_prefix("res://ui/", ""), "res://ui/")
        self.assertEqual(gu.dynamic_prefix("res://ui/btn_", ' + n'), "res://ui/")
        self.assertIsNone(gu.dynamic_prefix("res://ui/btn.png", ")"))


class Cli(unittest.TestCase):
    def test_fail_flag_is_a_gate(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(gu.main([EXAMPLE, "--json"]), 0)
            self.assertEqual(gu.main([EXAMPLE, "--json", "--fail"]), 1)


if __name__ == "__main__":
    unittest.main()
