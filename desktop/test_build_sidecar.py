"""Platform-only checks for the desktop sidecar build naming convention."""

import unittest

from desktop.build_sidecar import COLLECT_ALL, data_separator, sidecar_filename, target_suffix


class SidecarNamingTests(unittest.TestCase):
    def test_darwin_arm64_matches_tauri_external_bin_convention(self) -> None:
        self.assertEqual(target_suffix("Darwin", "arm64"), "aarch64-apple-darwin")
        self.assertEqual(
            sidecar_filename("Darwin", "arm64"),
            "mindcore-backend-aarch64-apple-darwin",
        )

    def test_windows_x64_uses_exe_and_msvc_target(self) -> None:
        self.assertEqual(target_suffix("Windows", "AMD64"), "x86_64-pc-windows-msvc")
        self.assertEqual(
            sidecar_filename("Windows", "AMD64"),
            "mindcore-backend-x86_64-pc-windows-msvc.exe",
        )

    def test_pyinstaller_data_separator_is_platform_safe(self) -> None:
        self.assertEqual(data_separator("Windows"), ";")
        self.assertEqual(data_separator("Darwin"), ":")

    def test_sidecar_collects_iana_timezone_data(self) -> None:
        self.assertIn("tzdata", COLLECT_ALL)
