"""Tests for vendored three.js assets and MIME type serving (T15).

Verifies:
- Vendor files exist under docs/dashboard-platform/shell/vendor/three/
- VERSION.txt is present with version info
- Static file serving returns .js with application/javascript MIME type
- The three.module.min.js is a valid ES module (exports detected)
- OrbitControls.js exports OrbitControls
- The path-panel.js includes 3D view support (toggle, canvas, functions)
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[3]
VENDOR_THREE = PROJECT_ROOT / 'docs' / 'dashboard-platform' / 'shell' / 'vendor' / 'three'
SHELL_DIR = PROJECT_ROOT / 'docs' / 'dashboard-platform' / 'shell'


class TestVendorFiles(unittest.TestCase):
    def test_vendor_directory_exists(self):
        self.assertTrue(VENDOR_THREE.exists(), 'vendor/three/ missing at ' + str(VENDOR_THREE))
        self.assertTrue(VENDOR_THREE.is_dir())

    def test_three_module_min_exists(self):
        f = VENDOR_THREE / 'three.module.min.js'
        self.assertTrue(f.exists(), 'three.module.min.js not found')
        self.assertGreater(f.stat().st_size, 10000, 'three.module.min.js too small')

    def test_orbit_controls_exists(self):
        f = VENDOR_THREE / 'OrbitControls.js'
        self.assertTrue(f.exists(), 'OrbitControls.js not found')
        self.assertGreater(f.stat().st_size, 1000, 'OrbitControls.js too small')

    def test_version_file_exists(self):
        f = VENDOR_THREE / 'VERSION.txt'
        self.assertTrue(f.exists(), 'VERSION.txt not found')
        content = f.read_text()
        self.assertIn('0.160', content, 'VERSION.txt must mention v0.160.x')


class TestMimeTypes(unittest.TestCase):
    def test_js_mime_type_mapping(self):
        from ground_station.service.api import _MIME_TYPES
        self.assertIn('.js', _MIME_TYPES)
        self.assertEqual(_MIME_TYPES['.js'], 'application/javascript')

    def test_three_js_served_with_js_mime(self):
        from ground_station.service.api import _mime_type
        three_path = str(VENDOR_THREE / 'three.module.min.js')
        mime = _mime_type(three_path)
        self.assertEqual(mime, 'application/javascript')

        oc_path = str(VENDOR_THREE / 'OrbitControls.js')
        mime_oc = _mime_type(oc_path)
        self.assertEqual(mime_oc, 'application/javascript')

    def test_three_module_valid_esm(self):
        src = (VENDOR_THREE / 'three.module.min.js').read_text()
        self.assertIn('export', src)
        self.assertIn('Scene', src)
        self.assertIn('Camera', src)
        self.assertIn('WebGLRenderer', src)

    def test_orbit_controls_exports(self):
        src = (VENDOR_THREE / 'OrbitControls.js').read_text()
        self.assertIn('export { OrbitControls }', src)
        self.assertIn('three.module.min.js', src)


class TestPathPanel3D(unittest.TestCase):
    def setUp(self):
        self.panel_path = SHELL_DIR / 'plugins' / 'path-panel.js'
        self.content = self.panel_path.read_text()

    def test_3d_toggle_exists(self):
        self.assertIn('pp-view-2d', self.content)
        self.assertIn('pp-view-3d', self.content)

    def test_3d_canvas_element(self):
        self.assertIn('pp-3d-canvas', self.content)
        self.assertIn('pp-3d-wrap', self.content)

    def test_3d_functions_exist(self):
        self.assertIn('initThreeJS', self.content)
        self.assertIn('initThreeScene', self.content)
        self.assertIn('render3D', self.content)
        self.assertIn('dispose3D', self.content)

    def test_orbit_controls_integration(self):
        self.assertIn('OrbitControls', self.content)

    def test_view_presets(self):
        self.assertIn('presetView', self.content)
        for preset in ('top', 'side', 'front', 'iso'):
            self.assertIn(repr(preset), self.content)

    def test_follow_drone_toggle(self):
        self.assertIn('pp-3d-follow-toggle', self.content)
        self.assertIn('_threeFollowDrone', self.content)

    def test_clear_trail_button(self):
        self.assertIn('pp-3d-clear', self.content)
        self.assertIn('clear3D', self.content)

    def test_session_load_button(self):
        self.assertIn('pp-load-session', self.content)
        self.assertIn('loadSessionData', self.content)

    def test_tracking_error_display(self):
        self.assertIn('pp-3d-error', self.content)
        self.assertIn('_trackingError', self.content)

    def test_desired_path_rendering(self):
        self.assertIn('_desiredPath', self.content)
        self.assertIn('LineDashedMaterial', self.content)

    def test_buffer_geometry_ring_buffer(self):
        self.assertIn('_MAX_3D_POINTS', self.content)
        self.assertIn('BufferGeometry', self.content)
        self.assertIn('setDrawRange', self.content)

    def test_dispose_on_destroy(self):
        self.assertIn('dispose3D()', self.content)

    def test_existing_2d_preserved(self):
        self.assertIn('pp-canvas', self.content)
        self.assertIn('drawTrail', self.content)
        self.assertIn('drawGrid', self.content)


class TestPathPanelSyntax(unittest.TestCase):
    def test_panel_is_valid_js(self):
        import subprocess
        panel_path = str(SHELL_DIR / 'plugins' / 'path-panel.js')
        proc = subprocess.run(
            ['node', '--check', panel_path],
            capture_output=True, text=True, timeout=30
        )
        self.assertEqual(
            proc.returncode, 0,
            'path-panel.js has syntax errors: ' + proc.stdout + proc.stderr,
        )


if __name__ == '__main__':
    unittest.main()
