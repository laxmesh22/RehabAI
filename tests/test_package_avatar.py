"""Fidelity checks vs rehab-ai-avatar-and-pain-check package (zip source of truth)."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOTION_JS = ROOT / 'web' / 'motion.js'
MOTION_TS = ROOT / 'rehab-ai-avatar-and-pain-check' / 'src' / 'avatar' / 'motion.ts'
FOLLOW_JS = ROOT / 'web' / 'follow_avatar.js'
FOLLOW_TS = ROOT / 'rehab-ai-avatar-and-pain-check' / 'src' / 'avatar' / 'FollowAvatar.ts'
VENDOR_THREE = ROOT / 'web' / 'vendor' / 'three.module.js'

PACKAGE_DEFAULTS = {
    'wand_er': 45,
    'er': 40,
    'flexion': 120,
    'abduction': 90,
    'wand_flexion': 110,
    'wall_walk': 100,
    'pendulum': 0,
}


def _parse_defaults(text: str) -> dict[str, int]:
    out = {}
    for match in re.finditer(
        r"(?:^|\s)(\w+)\s*:\s*\{\s*id:\s*['\"]?\w+['\"]?.*?defaultTarget:\s*(\d+)",
        text,
        re.S,
    ):
        out[match.group(1)] = int(match.group(2))
    return out


class PackageAvatarFidelityTests(unittest.TestCase):
    def test_package_folder_present(self):
        self.assertTrue(MOTION_TS.is_file(), 'Extract package folder rehab-ai-avatar-and-pain-check/')
        self.assertTrue(FOLLOW_TS.is_file())
        self.assertTrue(VENDOR_THREE.is_file())
        self.assertGreater(VENDOR_THREE.stat().st_size, 500_000)

    def test_motion_defaults_match_package(self):
        js = MOTION_JS.read_text(encoding='utf-8')
        ts = MOTION_TS.read_text(encoding='utf-8')
        js_defs = _parse_defaults(js)
        ts_defs = _parse_defaults(ts)
        self.assertEqual(ts_defs, PACKAGE_DEFAULTS)
        self.assertEqual(js_defs, PACKAGE_DEFAULTS)

    def test_library_avatar_demos_use_package_ids(self):
        from edge.exercises.library import EXERCISES, avatar_demo_for
        for eid, row in EXERCISES.items():
            demo = avatar_demo_for(eid)
            self.assertIn(demo, PACKAGE_DEFAULTS, eid)
            self.assertEqual(row['avatar_demo'], demo)
            # Canonical catalog rows match package defaultTarget; aliases may use a clinical target.
            if eid in ('shoulder_abduction', 'shoulder_flexion', 'wand_flexion', 'wall_climb',
                       'external_rotation', 'wand_er') or eid == demo:
                if demo != 'pendulum':
                    self.assertEqual(row['target_range'], PACKAGE_DEFAULTS[demo], eid)

    def test_follow_avatar_exports_create(self):
        text = FOLLOW_JS.read_text(encoding='utf-8')
        self.assertIn('export function createFollowAvatar', text)
        self.assertIn('forceResize', text)
        self.assertIn("import * as THREE from 'three'", text)


if __name__ == '__main__':
    unittest.main()
