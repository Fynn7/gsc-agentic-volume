"""QVis reader: format handling, axis order, geometry and every refusal path."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from volume_context.errors import InputError
from volume_context.readers import read_source_header
from volume_context.readers.common import max_abs_normalized_dot
from volume_context.tests import fixtures


class QvisReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def read(self, path: Path):
        return read_source_header(path)

    def test_uint8_header_declares_uint8(self) -> None:
        header_path, payload_path = fixtures.write_qvis(self.root / "u8", fixtures.PHANTOM_U8)
        header = self.read(header_path)
        self.assertEqual(header.family, "qvis")
        self.assertEqual(header.scalar_type, "uint8")
        self.assertEqual(header.endianness, "little")
        self.assertEqual(header.encoding, "raw")
        self.assertEqual(header.dimensions, (fixtures.WIDTH, fixtures.HEIGHT, fixtures.DEPTH))
        self.assertEqual(header.voxel_count, 24)
        self.assertEqual(header.expected_decoded_byte_length, 24)
        self.assertEqual(header.payload_path, payload_path.resolve())

    def test_uint16_header_declares_uint16(self) -> None:
        header_path, _ = fixtures.write_qvis(self.root / "u16", fixtures.PHANTOM_U16, scalar_type="uint16")
        header = self.read(header_path)
        self.assertEqual(header.scalar_type, "uint16")
        self.assertEqual(header.expected_decoded_byte_length, 48)

    def test_payload_bytes_match_the_declared_layout_x_fastest(self) -> None:
        header_path, payload_path = fixtures.write_qvis(self.root / "layout", fixtures.PHANTOM_U8)
        header = self.read(header_path)
        data = np.frombuffer(payload_path.read_bytes(), dtype=np.uint8)
        # Reading the raw bytes must reproduce the fixture exactly; a reader that
        # reshaped or transposed would not.
        self.assertTrue(np.array_equal(data, fixtures.PHANTOM_U8))
        self.assertEqual(int(data[fixtures.voxel_index(0, 0, 0)]), 1)
        self.assertEqual(int(data[fixtures.voxel_index(1, 2, 3)]), 24)
        self.assertEqual(header.dimensions, (2, 3, 4))

    def test_unsupported_format_is_refused_not_treated_as_16_bit(self) -> None:
        # The renderer falls back to 16 bits for any unknown Format. This reader
        # must not inherit that fallback.
        header_path, _ = fixtures.write_qvis(
            self.root / "fmt", fixtures.PHANTOM_U8, format_token="double"
        )
        with self.assertRaises(InputError) as caught:
            self.read(header_path)
        self.assertEqual(caught.exception.code, "unsupported_qvis_format")

    def test_missing_format_is_refused(self) -> None:
        header_path, _ = fixtures.write_qvis(self.root / "nofmt", fixtures.PHANTOM_U8, omit_format=True)
        with self.assertRaises(InputError) as caught:
            self.read(header_path)
        self.assertEqual(caught.exception.code, "missing_header_field")

    def test_big_endian_is_refused(self) -> None:
        header_path, _ = fixtures.write_qvis(self.root / "be", fixtures.PHANTOM_U8, endian_token="big")
        with self.assertRaises(InputError) as caught:
            self.read(header_path)
        self.assertEqual(caught.exception.code, "unsupported_qvis_endian")

    def test_absent_endianness_is_a_recorded_reader_default(self) -> None:
        header_path, _ = fixtures.write_qvis(self.root / "noend", fixtures.PHANTOM_U8, endian_token=None)
        header = self.read(header_path)
        defaults = {entry["field"]: entry for entry in header.defaults}
        self.assertIn("Endianess", defaults)
        self.assertEqual(defaults["Endianess"]["appliedValue"], "little")
        self.assertEqual(defaults["Endianess"]["reason"], "renderer-limitation")

    def test_lz4_is_refused(self) -> None:
        header_path, _ = fixtures.write_qvis(
            self.root / "lz4", fixtures.PHANTOM_U8, compression="lz4"
        )
        with self.assertRaises(InputError) as caught:
            self.read(header_path)
        self.assertEqual(caught.exception.code, "unsupported_qvis_compression")

    def test_absent_compression_is_a_recorded_reader_default(self) -> None:
        header_path, _ = fixtures.write_qvis(self.root / "nocomp", fixtures.PHANTOM_U8)
        header = self.read(header_path)
        defaults = {entry["field"]: entry for entry in header.defaults}
        self.assertEqual(defaults["Compression"]["appliedValue"], "none")

    def test_absent_slice_thickness_becomes_a_rendering_default(self) -> None:
        header_path, _ = fixtures.write_qvis(self.root / "nosp", fixtures.PHANTOM_U8, spacing=None)
        header = self.read(header_path)
        self.assertIsNone(header.geometry.spacing)
        self.assertIsNone(header.geometry.spacing_declared_by)
        defaults = {entry["field"]: entry for entry in header.defaults}
        self.assertEqual(defaults["SliceThickness"]["reason"], "renderer-limitation")

    def test_slice_thickness_becomes_a_diagonal_basis_without_world_orientation(self) -> None:
        header_path, _ = fixtures.write_qvis(
            self.root / "sp", fixtures.PHANTOM_U8, spacing=(0.5, 0.75, 2.0)
        )
        header = self.read(header_path)
        self.assertEqual(header.geometry.spacing, (0.5, 0.75, 2.0))
        self.assertEqual(header.geometry.directions_declared_by, "qvis-slice-thickness")
        self.assertIsNone(header.geometry.origin)
        self.assertIsNone(header.unit_token)

    def test_non_positive_slice_thickness_is_refused(self) -> None:
        header_path, _ = fixtures.write_qvis(
            self.root / "badsp", fixtures.PHANTOM_U8, spacing=(0.0, 1.0, 1.0)
        )
        with self.assertRaises(InputError) as caught:
            self.read(header_path)
        self.assertEqual(caught.exception.code, "invalid_geometry")

    def test_repeated_header_field_is_refused(self) -> None:
        header_path, _ = fixtures.write_qvis(
            self.root / "dup",
            fixtures.PHANTOM_U8,
            extra_lines=("Resolution: 2 3 4",),
        )
        with self.assertRaises(InputError) as caught:
            self.read(header_path)
        self.assertEqual(caught.exception.code, "duplicate_header_field")

    def test_non_numeric_resolution_is_refused(self) -> None:
        directory = self.root / "badres"
        directory.mkdir(parents=True)
        payload = directory / "phantom.raw"
        payload.write_bytes(fixtures.PHANTOM_U8.tobytes())
        header = directory / "phantom.dat"
        header.write_text(
            "ObjectFileName: phantom.raw\nResolution: 2 3 x\nFormat: uchar\nEndianess: little\n",
            encoding="ascii",
        )
        with self.assertRaises(InputError) as caught:
            self.read(header)
        self.assertEqual(caught.exception.code, "invalid_dimensions")

    def test_zero_dimension_is_refused(self) -> None:
        header_path, _ = fixtures.write_qvis(self.root / "zerodim", fixtures.PHANTOM_U8)
        header_path.write_text(
            header_path.read_text(encoding="ascii").replace(
                "Resolution: 2 3 4", "Resolution: 0 3 4"
            ),
            encoding="ascii",
        )
        with self.assertRaises(InputError) as caught:
            self.read(header_path)
        self.assertEqual(caught.exception.code, "invalid_dimensions")

    def test_two_dimensional_resolution_is_refused(self) -> None:
        header_path, _ = fixtures.write_qvis(self.root / "twod", fixtures.PHANTOM_U8)
        header_path.write_text(
            header_path.read_text(encoding="ascii").replace(
                "Resolution: 2 3 4", "Resolution: 2 3"
            ),
            encoding="ascii",
        )
        with self.assertRaises(InputError) as caught:
            self.read(header_path)
        self.assertEqual(caught.exception.code, "invalid_dimensions")

    def test_missing_payload_is_refused(self) -> None:
        header_path, payload_path = fixtures.write_qvis(self.root / "nopay", fixtures.PHANTOM_U8)
        payload_path.unlink()
        with self.assertRaises(InputError) as caught:
            self.read(header_path)
        self.assertEqual(caught.exception.code, "payload_missing")

    def test_parent_traversal_payload_is_refused(self) -> None:
        directory = self.root / "escape"
        directory.mkdir(parents=True)
        fixtures.write_raw_only(self.root, fixtures.PHANTOM_U8)
        header = directory / "phantom.dat"
        header.write_text(
            "ObjectFileName: ../orphan.raw\nResolution: 2 3 4\nFormat: uchar\nEndianess: little\n",
            encoding="ascii",
        )
        with self.assertRaises(InputError) as caught:
            self.read(header)
        self.assertEqual(caught.exception.code, "payload_path_escapes")

    def test_absolute_payload_reference_is_refused(self) -> None:
        directory = self.root / "abs"
        directory.mkdir(parents=True)
        header = directory / "phantom.dat"
        header.write_text(
            f"ObjectFileName: {fixtures.write_raw_only(self.root, fixtures.PHANTOM_U8)}\n"
            "Resolution: 2 3 4\nFormat: uchar\nEndianess: little\n",
            encoding="ascii",
        )
        with self.assertRaises(InputError) as caught:
            self.read(header)
        self.assertEqual(caught.exception.code, "payload_path_not_relative")

    def test_symlinked_payload_out_of_the_header_directory_is_refused(self) -> None:
        outer = self.root / "symlink"
        inner = outer / "inner"
        inner.mkdir(parents=True)
        outside = fixtures.write_raw_only(outer / "elsewhere", fixtures.PHANTOM_U8)
        payload = inner / "phantom.raw"
        try:
            payload.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are unavailable on this host")
        header = inner / "phantom.dat"
        header.write_text(
            "ObjectFileName: phantom.raw\nResolution: 2 3 4\nFormat: uchar\nEndianess: little\n",
            encoding="ascii",
        )
        with self.assertRaises(InputError) as caught:
            self.read(header)
        self.assertEqual(caught.exception.code, "payload_path_escapes")

    def test_payload_name_with_spaces_and_unicode_is_preserved(self) -> None:
        directory = self.root / "unicode"
        payload_name = "Volumen Test ÄÖÜ.raw"
        header_path, payload_path = fixtures.write_qvis(
            directory, fixtures.PHANTOM_U8, payload_name=payload_name
        )
        header = self.read(header_path)
        self.assertEqual(header.payload_path.name, payload_name)
        self.assertEqual(header.payload_path, payload_path.resolve())

    def test_orthogonal_helper_matches_hand_computation(self) -> None:
        axes = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
        self.assertEqual(max_abs_normalized_dot(axes), 0.0)
        # A rotated, anisotropic basis with exact integer dot products of zero.
        tilted = ((0.0, 3.0, 4.0), (-4.0, 0.0, 0.0), (0.0, -12.0, 9.0))
        self.assertEqual(max_abs_normalized_dot(tilted), 0.0)
        sheared = ((1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 0.0, 1.0))
        self.assertAlmostEqual(max_abs_normalized_dot(sheared), 1.0 / np.sqrt(2.0), places=12)

    def test_tu_wien_binary_dat_is_distinguished_from_qvis(self) -> None:
        path = self.root / "tu.dat"
        path.write_bytes(b"\x02\x00\x03\x00\x04\x00" + b"\x00" * 48)
        header = self.read(path)
        self.assertEqual(header.family, "tu-wien-dat")
        self.assertEqual(header.dimensions, (2, 3, 4))
        self.assertEqual(header.payload_offset, 6)


if __name__ == "__main__":
    unittest.main()
