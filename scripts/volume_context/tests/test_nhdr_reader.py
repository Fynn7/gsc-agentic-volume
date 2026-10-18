"""NHDR reader: encodings, byte order, geometry and the unsupported-organization paths."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from volume_context.errors import InputError
from volume_context.readers import read_source_header
from volume_context.tests import fixtures

ORTHOGONAL_DIRECTIONS = "(3,0,0) (0,4,0) (0,0,5)"
TILTED_ORTHOGONAL_DIRECTIONS = "(0,3,4) (-4,0,0) (0,-12,9)"
SHEARED_DIRECTIONS = "(1,0,0) (1,1,0) (0,0,1)"


class NhdrReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def read(self, path: Path):
        return read_source_header(path)

    def test_raw_uint8_header(self) -> None:
        header_path, payload_path = fixtures.write_nhdr(self.root / "raw8", fixtures.PHANTOM_U8)
        header = self.read(header_path)
        self.assertEqual(header.family, "nhdr")
        self.assertEqual(header.scalar_type, "uint8")
        self.assertEqual(header.endianness, "little")
        self.assertEqual(header.encoding, "raw")
        self.assertEqual(header.expected_decoded_byte_length, 24)
        self.assertEqual(header.payload_path, payload_path.resolve())
        # Layout is preserved, so the stored bytes still equal the fixture.
        self.assertTrue(np.array_equal(np.frombuffer(payload_path.read_bytes(), dtype=np.uint8), fixtures.PHANTOM_U8))

    def test_single_byte_type_without_endian_reports_not_applicable(self) -> None:
        header_path, _ = fixtures.write_nhdr(
            self.root / "raw8noend", fixtures.PHANTOM_U8, endian=None
        )
        self.assertEqual(self.read(header_path).endianness, "not-applicable")

    def test_gzip_uint16_little_endian(self) -> None:
        header_path, _ = fixtures.write_nhdr(
            self.root / "gz16",
            fixtures.PHANTOM_U16,
            nrrd_type="uint16",
            dtype="<u2",
            endian="little",
            encoding="gzip",
        )
        header = self.read(header_path)
        self.assertEqual(header.scalar_type, "uint16")
        self.assertEqual(header.endianness, "little")
        self.assertEqual(header.encoding, "gzip")

    def test_gzip_big_endian_uint16(self) -> None:
        directory = self.root / "gbe"
        header_path, payload_path = fixtures.write_nhdr(
            directory,
            fixtures.PHANTOM_U16,
            nrrd_type="uint16",
            dtype=">u2",
            endian="big",
        )
        header = self.read(header_path)
        self.assertEqual(header.endianness, "big")
        # Big-endian bytes must not be reinterpreted as little-endian.
        stored = np.frombuffer(payload_path.read_bytes(), dtype=">u2")
        self.assertTrue(np.array_equal(stored, fixtures.PHANTOM_U16))

    def test_multi_byte_without_endian_is_refused_as_ambiguous(self) -> None:
        header_path, _ = fixtures.write_nhdr(
            self.root / "noend",
            fixtures.PHANTOM_U16,
            nrrd_type="uint16",
            dtype="<u2",
            endian=None,
        )
        with self.assertRaises(InputError) as caught:
            self.read(header_path)
        self.assertEqual(caught.exception.code, "ambiguous_endianness")

    def test_float_types_are_accepted(self) -> None:
        for nrrd_type, dtype, scalar in (
            ("float", "<f4", "float32"),
            ("double", "<f8", "float64"),
        ):
            with self.subTest(nrrd_type=nrrd_type):
                directory = self.root / nrrd_type
                header_path, _ = fixtures.write_nhdr(
                    directory, fixtures.PHANTOM_U8.astype(dtype), nrrd_type=nrrd_type, dtype=dtype
                )
                header = self.read(header_path)
                self.assertEqual(header.scalar_type, scalar)

    def test_int8_and_int32_are_accepted(self) -> None:
        for nrrd_type, dtype, scalar in (("int8", "|i1", "int8"), ("int32", "<i4", "int32")):
            with self.subTest(nrrd_type=nrrd_type):
                directory = self.root / nrrd_type
                header_path, _ = fixtures.write_nhdr(
                    directory, fixtures.PHANTOM_U8.astype(dtype), nrrd_type=nrrd_type, dtype=dtype
                )
                self.assertEqual(self.read(header_path).scalar_type, scalar)

    def test_missing_nrrd_magic_is_refused(self) -> None:
        header_path, _ = fixtures.write_nhdr(self.root / "nomagic", fixtures.PHANTOM_U8, magic="NOTNRRD")
        with self.assertRaises(InputError) as caught:
            self.read(header_path)
        self.assertEqual(caught.exception.code, "missing_nrrd_magic")

    def test_inline_nrrd_data_is_refused(self) -> None:
        header_path, _ = fixtures.write_nhdr(
            self.root / "inline", fixtures.PHANTOM_U8, omit_data_file=True
        )
        with self.assertRaises(InputError) as caught:
            self.read(header_path)
        self.assertEqual(caught.exception.code, "unsupported_data_layout")

    def test_list_payload_is_refused(self) -> None:
        header_path, _ = fixtures.write_nhdr(
            self.root / "list",
            fixtures.PHANTOM_U8,
            extra_fields=("data file: a.raw b.raw",),
            omit_data_file=True,
        )
        with self.assertRaises(InputError) as caught:
            self.read(header_path)
        self.assertEqual(caught.exception.code, "unsupported_data_layout")

    def test_list_keyword_is_refused(self) -> None:
        header_path, _ = fixtures.write_nhdr(
            self.root / "listkw",
            fixtures.PHANTOM_U8,
            extra_fields=("data file: LIST",),
            omit_data_file=True,
        )
        with self.assertRaises(InputError) as caught:
            self.read(header_path)
        self.assertEqual(caught.exception.code, "unsupported_data_layout")

    def test_ascii_encoding_is_refused(self) -> None:
        header_path, _ = fixtures.write_nhdr(
            self.root / "ascii", fixtures.PHANTOM_U8, encoding="ascii"
        )
        with self.assertRaises(InputError) as caught:
            self.read(header_path)
        self.assertEqual(caught.exception.code, "unsupported_encoding")

    def test_unknown_encoding_is_refused(self) -> None:
        header_path, _ = fixtures.write_nhdr(
            self.root / "enc", fixtures.PHANTOM_U8, encoding="zstd"
        )
        with self.assertRaises(InputError) as caught:
            self.read(header_path)
        self.assertEqual(caught.exception.code, "unsupported_encoding")

    def test_byte_skip_is_refused(self) -> None:
        header_path, _ = fixtures.write_nhdr(
            self.root / "skip", fixtures.PHANTOM_U8, extra_fields=("byte skip: 4",)
        )
        with self.assertRaises(InputError) as caught:
            self.read(header_path)
        self.assertEqual(caught.exception.code, "unsupported_data_skip")

    def test_zero_byte_skip_is_accepted(self) -> None:
        header_path, _ = fixtures.write_nhdr(
            self.root / "skip0",
            fixtures.PHANTOM_U8,
            extra_fields=("byte skip: 0", "line skip: 0"),
        )
        self.assertEqual(self.read(header_path).scalar_type, "uint8")

    def test_multi_channel_kinds_are_refused(self) -> None:
        header_path, _ = fixtures.write_nhdr(
            self.root / "chan",
            fixtures.PHANTOM_U8,
            extra_fields=("kinds: RGB-color domain domain",),
        )
        with self.assertRaises(InputError) as caught:
            self.read(header_path)
        self.assertEqual(caught.exception.code, "unsupported_component_layout")

    def test_vector_space_is_refused(self) -> None:
        header_path, _ = fixtures.write_nhdr(
            self.root / "vec", fixtures.PHANTOM_U8, extra_fields=("space: left-posterior-superior",)
        )
        header = self.read(header_path)
        # A space *name* alone is not an error, but it is also not a unit.
        self.assertIsNone(header.unit_token)

    def test_nonempty_content_is_ignored(self) -> None:
        header_path, _ = fixtures.write_nhdr(
            self.root / "content", fixtures.PHANTOM_U8, extra_fields=("content: scan",)
        )
        self.assertEqual(self.read(header_path).scalar_type, "uint8")

    def test_four_dimensional_volume_is_refused(self) -> None:
        directory = self.root / "4d"
        header_path, payload_path = fixtures.write_nhdr(directory, fixtures.PHANTOM_U8)
        text = header_path.read_text(encoding="utf-8").replace("dimension: 3", "dimension: 4")
        header_path.write_text(text, encoding="utf-8")
        del payload_path
        with self.assertRaises(InputError) as caught:
            self.read(header_path)
        self.assertEqual(caught.exception.code, "unsupported_dimensions")

    def test_conflicting_duplicate_field_is_refused(self) -> None:
        header_path, _ = fixtures.write_nhdr(
            self.root / "dup", fixtures.PHANTOM_U8, extra_fields=("sizes: 4 3 2",)
        )
        with self.assertRaises(InputError) as caught:
            self.read(header_path)
        self.assertEqual(caught.exception.code, "duplicate_header_field")
        self.assertIn("conflicting", caught.exception.message)

    def test_repeated_identical_field_is_refused(self) -> None:
        header_path, _ = fixtures.write_nhdr(
            self.root / "repdup",
            fixtures.PHANTOM_U8,
            extra_fields=("encoding: raw",),
        )
        with self.assertRaises(InputError) as caught:
            self.read(header_path)
        self.assertEqual(caught.exception.code, "duplicate_header_field")
        self.assertIn("repeated", caught.exception.message)

    def test_missing_data_file_field_is_refused(self) -> None:
        header_path, _ = fixtures.write_nhdr(
            self.root / "nodatafile", fixtures.PHANTOM_U8, omit_data_file=True
        )
        with self.assertRaises(InputError) as caught:
            self.read(header_path)
        self.assertEqual(caught.exception.code, "unsupported_data_layout")

    def test_unsupported_type_is_refused(self) -> None:
        header_path, _ = fixtures.write_nhdr(
            self.root / "longdouble", fixtures.PHANTOM_U8, nrrd_type="long double"
        )
        with self.assertRaises(InputError) as caught:
            self.read(header_path)
        self.assertEqual(caught.exception.code, "unsupported_scalar_type")

    def test_sheared_space_basis_is_refused(self) -> None:
        header_path, _ = fixtures.write_nhdr(
            self.root / "shear",
            fixtures.PHANTOM_U8,
            space_directions=SHEARED_DIRECTIONS,
        )
        with self.assertRaises(InputError) as caught:
            self.read(header_path)
        self.assertEqual(caught.exception.code, "sheared_space_basis")

    def test_zero_direction_is_refused(self) -> None:
        header_path, _ = fixtures.write_nhdr(
            self.root / "zero",
            fixtures.PHANTOM_U8,
            space_directions="(0,0,0) (0,1,0) (0,0,1)",
        )
        with self.assertRaises(InputError) as caught:
            self.read(header_path)
        self.assertEqual(caught.exception.code, "degenerate_space_basis")

    def test_non_finite_direction_is_refused(self) -> None:
        header_path, _ = fixtures.write_nhdr(
            self.root / "nan",
            fixtures.PHANTOM_U8,
            space_directions="(1,0,0) (0,nan,0) (0,0,1)",
        )
        with self.assertRaises(InputError) as caught:
            self.read(header_path)
        self.assertEqual(caught.exception.code, "invalid_geometry")

    def test_non_orthogonal_anisotropic_basis_is_accepted_with_recorded_dot(self) -> None:
        header_path, _ = fixtures.write_nhdr(
            self.root / "ortho",
            fixtures.PHANTOM_U8,
            space_directions=ORTHOGONAL_DIRECTIONS,
            space_origin="(10,20,30)",
            space_units="mm",
        )
        header = self.read(header_path)
        self.assertEqual(header.geometry.spacing, (3.0, 4.0, 5.0))
        self.assertEqual(header.geometry.origin, (10.0, 20.0, 30.0))
        self.assertEqual(header.geometry.max_abs_normalized_dot, 0.0)
        self.assertEqual(header.unit_token, "mm")

    def test_tilted_orthogonal_basis_is_accepted(self) -> None:
        header_path, _ = fixtures.write_nhdr(
            self.root / "tilt",
            fixtures.PHANTOM_U8,
            space_directions=TILTED_ORTHOGONAL_DIRECTIONS,
        )
        header = self.read(header_path)
        self.assertEqual(header.geometry.spacing, (5.0, 4.0, 15.0))
        self.assertEqual(header.geometry.max_abs_normalized_dot, 0.0)

    def test_standard_quoted_axis_units(self) -> None:
        header_path, _ = fixtures.write_nhdr(
            self.root / "spacename",
            fixtures.PHANTOM_U8,
            space_directions=ORTHOGONAL_DIRECTIONS,
            space_units='"mm" "mm" "mm"',
        )
        header = self.read(header_path)
        self.assertEqual(header.unit_token, "mm")

    def test_missing_spacing_stays_unknown(self) -> None:
        header_path, _ = fixtures.write_nhdr(self.root / "nosp", fixtures.PHANTOM_U8)
        header = self.read(header_path)
        self.assertIsNone(header.geometry.spacing)
        self.assertIsNone(header.geometry.directions)
        self.assertIsNone(header.geometry.origin)
        defaults = {entry["field"]: entry for entry in header.defaults}
        self.assertIn("spacing", defaults)

    def test_payload_escape_is_refused(self) -> None:
        directory = self.root / "escape"
        directory.mkdir(parents=True)
        header = directory / "phantom.nhdr"
        header.write_text(
            "NRRD0004\ntype: uint8\ndimension: 3\nsizes: 2 3 4\nencoding: raw\n"
            "data file: ../elsewhere.raw\n",
            encoding="ascii",
        )
        with self.assertRaises(InputError) as caught:
            self.read(header)
        self.assertEqual(caught.exception.code, "payload_path_escapes")

    def test_payload_name_with_spaces_is_preserved(self) -> None:
        header_path, payload_path = fixtures.write_nhdr(
            self.root / "spaces", fixtures.PHANTOM_U8, payload_name="my phantom.raw"
        )
        header = self.read(header_path)
        self.assertEqual(header.payload_path.name, "my phantom.raw")
        self.assertEqual(header.payload_path, payload_path.resolve())


if __name__ == "__main__":
    unittest.main()
