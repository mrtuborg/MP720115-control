"""Offline structural tests for the fast cinematic front-panel show."""

import importlib.util
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "examples"))

for name in ("visual_screensaver", "cursor_demo"):
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "examples", name + ".py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)

spec = importlib.util.spec_from_file_location(
    "cinematic_show", os.path.join(ROOT, "examples", "cinematic_show.py"))
show = importlib.util.module_from_spec(spec)
sys.modules["cinematic_show"] = show
spec.loader.exec_module(show)


class WriteOnlyFake:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        if name.startswith("get_") or name in ("idn", "capture_screen_bmp"):
            raise AssertionError(f"per-frame readback forbidden: {name}")
        if name.startswith("set_"):
            def setter(*args):
                self.calls.append((name, args))
            return setter
        raise AttributeError(name)


class PlanTests(unittest.TestCase):
    def test_plan_has_fast_lamp_chase_and_visual_modes(self):
        scenes = show.plan(1e-3, cycle=1, seed=720115)
        self.assertEqual(len(scenes), 25)
        names = {scene.name for scene in scenes}
        for required in (
            "blackout", "ch1-square-positive", "ch2-noise-fft", "ch3-solo", "ch4-solo",
            "all-channels", "math-add", "xy-lissajous", "fft-square-blackman",
            "filter-low-pass", "time-cursors", "amplitude-cursors",
        ):
            self.assertIn(required, names)

    def test_every_chase_trigger_source_is_enabled(self):
        for cycle in range(1, 8):
            for scene in show.chase_scenes(cycle, 720115):
                state = dict(scene.settings)
                enabled = state["channels"]
                source = int(state["trigger.source"][-1])
                self.assertIn(source, enabled)
                self.assertGreaterEqual(len(enabled), 1)

    def test_plan_is_deterministic_and_scale_is_bounded(self):
        a = [scene.as_dict() for scene in show.plan(1e-3, 2, 42)]
        b = [scene.as_dict() for scene in show.plan(1e-3, 2, 42)]
        self.assertEqual(a, b)
        self.assertEqual(show._bounded_scale(0), 2e-9)
        self.assertEqual(show._bounded_scale(1000), 100.0)

    def test_cursor_coordinates_are_in_documented_ranges(self):
        for scene in show.fixed_scenes(1e-3):
            state = dict(scene.settings)
            if state.get("cursor.type") not in ("TIME", "AMPLITUDE"):
                continue
            for key in ("cursor.a", "cursor.b"):
                show.MP720115.validate_cursor_position(state["cursor.type"], state[key])


class ApplyTests(unittest.TestCase):
    def test_apply_scene_uses_setters_only(self):
        fake = WriteOnlyFake()
        show.apply_scene(fake, show.fixed_scenes(1e-3)[1])
        self.assertTrue(fake.calls)
        self.assertEqual(
            [args for name, args in fake.calls if name == "set_channel_display"],
            [(1, True), (2, False), (3, False), (4, False)],
        )
        self.assertFalse(any(name in ("run", "stop") for name, _args in fake.calls))

    def test_blackout_turns_all_channel_lamps_off(self):
        fake = WriteOnlyFake()
        show.apply_scene(fake, show.fixed_scenes(1e-3)[0])
        calls = [args for name, args in fake.calls if name == "set_channel_display"]
        self.assertEqual(calls, [(1, False), (2, False), (3, False), (4, False)])


if __name__ == "__main__":
    unittest.main()
