"""Synthetic geometry and explicit legacy-package fixtures only."""

import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import SimpleITK as sitk

from annotation_app.imaging import (
    CPRPath, GeometryError, NativeVolume, load_case, safe_relative_path, window_uint8,
)


def synthetic():
    z, y, x = np.indices((40, 48, 48), dtype=np.float32)
    array = 2 * x + 3 * y + 5 * z - 400
    image = sitk.GetImageFromArray(array)
    image.SetOrigin((-130.5, 47.25, -23.125))
    image.SetSpacing((.7, .8, 1.0))
    image.SetDirection((0., -1., 0., 1., 0., 0., 0., 0., 1.))
    native = NativeVolume(image)
    distances = np.r_[np.arange(0., 10.01, .5), 10.2]
    centers = native.index_to_world(np.column_stack((np.full(len(distances), 24),
                                                   np.full(len(distances), 24), 8 + distances)))
    count = len(distances)
    normal = np.tile([0., 1., 0.], (count, 1))
    binormal = np.tile([-1., 0., 0.], (count, 1))
    tangent = np.tile([0., 0., 1.], (count, 1))
    u = (np.arange(12) - 5.5) * .25
    v = (np.arange(10) - 4.5) * .25
    vv, uu = np.meshgrid(v, u, indexing="ij")
    points = centers[:, None, None, :] + uu[None, :, :, None] * normal[:, None, None, :] + vv[None, :, :, None] * binormal[:, None, None, :]
    volume = native.sample(points)
    return native, CPRPath(volume, distances, centers, normal, binormal, tangent,
                           10.2, .25, np.full(count, 2), native, "LAD")


def write_tiny_case(root):
    native, path = synthetic()
    native_dir = root / "native"
    native_dir.mkdir()
    sitk.WriteImage(native.image, str(native_dir / "volume.nii.gz"))
    files = ["native/volume.nii.gz"]
    records = {}
    for vessel in ("LAD", "LCX", "RCA"):
        folder = root / "cpr" / vessel
        folder.mkdir(parents=True)
        local = sitk.GetImageFromArray(path.volume)
        local.SetSpacing((.25, .25, .5))
        sitk.WriteImage(local, str(folder / "image.nrrd"))
        np.savez_compressed(folder / "frames.npz", centers=path.centers,
                            tangents=path.tangents, normals=path.normals,
                            binormals=path.binormals, distance_mm=path.distances,
                            segment_id=path.segment_ids)
        (folder / "qa.json").write_text(json.dumps({"length_mm": 10.2}), encoding="utf8")
        with (folder / "path.csv").open("w", encoding="utf8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(("sample_index", "distance_mm", "x_mm", "y_mm", "z_mm", "segment_id"))
            for row, s in enumerate(path.distances):
                writer.writerow((row, s, *path.centers[row], path.segment_ids[row]))
        records[vessel] = {key: f"cpr/{vessel}/{name}" for key, name in
                           (("image", "image.nrrd"), ("frames", "frames.npz"),
                            ("qa", "qa.json"), ("path", "path.csv"))}
        files.extend(records[vessel].values())
    (root / "geometry").mkdir()
    (root / "geometry" / "canonical_lm.json").write_text(json.dumps({
        "schema_version": "imagecasx-canonical-lm-1.0", "status": "not_present",
        "owner": "LAD", "verified_end_mm": 0,
    }), encoding="utf8")
    files.append("geometry/canonical_lm.json")
    manifest = {"schema_version": "imagecasx-case-1.0", "case_id": "synthetic",
                "official_split": "train", "geometry_id": "synthetic-v1",
                "native": "native/volume.nii.gz", "paths": records,
                "canonical_lm": "geometry/canonical_lm.json", "files": []}
    for filename in files:
        asset = root / filename
        manifest["files"].append({"path": filename, "bytes": asset.stat().st_size,
                                  "sha256": hashlib.sha256(asset.read_bytes()).hexdigest()})
    (root / "case.json").write_text(json.dumps(manifest), encoding="utf8")
    return root / "case.json", manifest


class ImagingTests(unittest.TestCase):
    def setUp(self):
        self.native, self.path = synthetic()

    def test_native_affine_roundtrip_matches_sitk_lps(self):
        index = np.array([13.125, 19.75, 7.375])
        world = self.native.index_to_world(index)
        np.testing.assert_allclose(world, self.native.image.TransformContinuousIndexToPhysicalPoint(tuple(index)))
        np.testing.assert_allclose(self.native.world_to_index(world), index, atol=1e-12)
        self.assertGreater(world[1], 0)  # Explicitly not an accidental LPS -> RAS flip.
        batched = np.stack([world, world + [0, 0, 3]])
        np.testing.assert_allclose(self.native.index_to_world(self.native.world_to_index(batched)), batched)

    def test_native_sampling_keeps_fractional_hu(self):
        index = np.array([10.125, 15.25, 7.375])
        value = self.native.sample(self.native.index_to_world(index))
        self.assertAlmostEqual(float(value), float(np.dot(index, [2, 3, 5]) - 400), places=4)
        self.assertEqual(self.native.array_zyx.dtype, np.float32)
        self.assertFalse(self.native.array_zyx.flags.writeable)

    def test_native_axial_shape_readonly_and_clamping(self):
        self.assertEqual(self.native.axial(3).shape, (48, 48))
        np.testing.assert_array_equal(self.native.axial(-30), self.native.array_zyx[0])
        np.testing.assert_array_equal(self.native.axial(1000), self.native.array_zyx[-1])
        self.assertFalse(self.native.axial(3).flags.writeable)

    def test_patient_orientation_labels_follow_affine(self):
        self.assertEqual(self.native.orientation_labels(), {"left": "A", "right": "P", "top": "L", "bottom": "R"})
        image = sitk.Image(self.native.image)
        image.SetDirection((1., 0, 0, 0, -1., 0, 0, 0, 1.))
        self.assertEqual(NativeVolume(image).orientation_labels(), {"left": "R", "right": "L", "top": "P", "bottom": "A"})

    def test_window_preserves_source(self):
        source = np.array([[-200., -100., 0., 100., 200.]])
        before = source.copy()
        result = window_uint8(source, 200, 0)
        np.testing.assert_array_equal(result, [[0, 0, 127, 255, 255]])
        np.testing.assert_array_equal(source, before)
        self.assertTrue(result.flags.c_contiguous)
        self.assertEqual(result.dtype, np.uint8)
        self.assertEqual(window_uint8(np.array([np.nan, np.inf, -np.inf]), 100, 0).tolist(), [0, 255, 0])

    def test_half_pixel_center_and_longitudinal(self):
        # Even grids have the center at 5.5 / 4.5, not the pixel [5,6].
        longitudinal = self.path.longitudinal()
        expected = (self.path.volume[:, 4, :] + self.path.volume[:, 5, :]) / 2
        np.testing.assert_allclose(longitudinal, expected, atol=3e-5)
        self.assertEqual(longitudinal.shape, (22, 12))

    def test_nonzero_rotation_offset_matches_physical_anchors(self):
        angle, offset, row, column = 37., .63, 7, 9
        s = self.path.distances[row]
        u = (column - 5.5) * .25
        point = self.path.display_point(s, u, offset, angle)
        image_value = self.path.longitudinal(angle, offset)[row, column]
        self.assertAlmostEqual(float(image_value), float(self.native.sample(point)), places=5)
        frame = self.path.frame(s)
        radians = np.deg2rad(angle)
        expected = frame.center + u * (np.cos(radians) * frame.normal + np.sin(radians) * frame.binormal) + offset * (-np.sin(radians) * frame.normal + np.cos(radians) * frame.binormal)
        np.testing.assert_allclose(point, expected)

    def test_cross_section_arbitrary_s_matches_point(self):
        s, angle, row, column = 3.135, -61., 7, 2
        section = self.path.cross_section(s, angle)
        point = self.path.display_point(s, (column - 5.5) * .25, (row - 4.5) * .25, angle)
        self.assertEqual(section.shape, (10, 12))
        self.assertAlmostEqual(float(section[row, column]), float(self.native.sample(point)), places=5)

    def test_point_uses_local_mm_not_local_nrrd_affine(self):
        frame = self.path.frame(3.3)
        np.testing.assert_allclose(self.path.point(3.3, 1.2, -.8), frame.center + 1.2 * frame.normal - .8 * frame.binormal)
        self.assertGreater(np.linalg.norm(self.path.point(3.3)), 100)

    def test_actual_endpoint_not_uniform_last_row(self):
        self.assertAlmostEqual(self.path.row_at(10.1), 20.5)
        self.assertAlmostEqual(self.path.s_at_row(20.5), 10.1)
        np.testing.assert_allclose(self.path.point(999), self.path.centers[-1])
        np.testing.assert_allclose(self.path.point(-99), self.path.centers[0])

    def test_upstream_small_endpoint_overshoot_clamped_without_changing_source(self):
        original = self.path.distances.copy()
        original[-1] = 10.25
        clamped = CPRPath(self.path.volume, original, self.path.centers, self.path.normals,
                          self.path.binormals, self.path.tangents, 10.2, .25,
                          self.path.segment_ids, self.native)
        self.assertEqual(clamped.distances[-1], 10.2)
        self.assertEqual(original[-1], 10.25)
        original[-1] = 11.
        with self.assertRaises(GeometryError):
            CPRPath(self.path.volume, original, self.path.centers, self.path.normals,
                    self.path.binormals, self.path.tangents, 10.2, .25, self.path.segment_ids)

    def test_frame_interpolation_orthonormal(self):
        for s in (0., .125, 8.421, 10.15, 10.2):
            frame = self.path.frame(s)
            basis = np.stack([frame.tangent, frame.normal, frame.binormal], axis=1)
            np.testing.assert_allclose(basis.T @ basis, np.eye(3), atol=1e-12)
            self.assertAlmostEqual(np.linalg.det(basis), 1.)

    def test_invalid_frame_rejected(self):
        normals = self.path.normals.copy()
        normals[3] *= 2
        with self.assertRaises(GeometryError):
            CPRPath(self.path.volume, self.path.distances, self.path.centers, normals,
                    self.path.binormals, self.path.tangents, 10.2, .25, self.path.segment_ids)

    def test_curved_interpolated_frame_matches_native_sampling(self):
        centers = self.path.centers.copy()
        centers[:, 0] += np.sin(self.path.distances * .23) * 2
        tangents = np.gradient(centers, axis=0)
        tangents /= np.linalg.norm(tangents, axis=1)[:, None]
        normals = np.tile([0., 1., 0.], (len(centers), 1))
        binormals = np.cross(tangents, normals)
        curved = CPRPath(self.path.volume, self.path.distances, centers, normals,
                         binormals, tangents, 10.2, .25, self.path.segment_ids, self.native)
        s, angle = 4.123, 28.
        frame = curved.frame(s)
        basis = np.stack([frame.tangent, frame.normal, frame.binormal], axis=1)
        np.testing.assert_allclose(basis.T @ basis, np.eye(3), atol=1e-12)
        cross = curved.cross_section(s, angle)
        expected = self.native.sample(curved.display_point(s, (8 - 5.5) * .25, (2 - 4.5) * .25, angle))
        self.assertAlmostEqual(float(cross[2, 8]), float(expected), places=5)

    def test_nonunique_inverse_requires_local_context(self):
        centers = np.array([[0., 0, 0], [1, 0, 0], [2, 0, 0],
                            [1, 1, 0], [1, 0, 0], [1, -1, 0]])
        distances = np.r_[0., np.cumsum(np.linalg.norm(np.diff(centers, axis=0), axis=1))]
        count = len(centers)
        path = CPRPath(np.zeros((count, 2, 2), dtype=np.float32), distances, centers,
                       np.tile([1., 0, 0], (count, 1)), np.tile([0., 1, 0], (count, 1)),
                       np.tile([0., 0, 1], (count, 1)), distances[-1], .25, np.full(count, 2))
        self.assertIsNone(path.nearest_s([1, 0, 0]))
        self.assertAlmostEqual(path.nearest_s([1, 0, 0], hint_s_mm=1, search_radius_mm=.3), 1)

    def test_local_inverse_limits_path_tube_and_search_window(self):
        point = self.path.point(4.13, .7, -.6)
        self.assertAlmostEqual(self.path.nearest_s(point), 4.13)
        self.assertIsNone(self.path.nearest_s(point, .5))
        self.assertIsNone(self.path.nearest_s(point, max_distance_mm=1, hint_s_mm=9, search_radius_mm=.3))
        self.assertAlmostEqual(self.path.nearest_s(point, hint_s_mm=4, search_radius_mm=.3), 4.13)
        self.assertIsNone(self.path.nearest_s([1e6, 1e6, 1e6]))

    def test_nonfinite_values_rejected(self):
        for call in (lambda: self.path.point(np.nan), lambda: self.native.world_to_index([1, 2, np.inf]),
                     lambda: self.path.longitudinal(np.inf), lambda: window_uint8([0], np.nan, 0)):
            with self.assertRaises(GeometryError):
                call()

    def test_portable_path_rejects_escapes_and_windows_drives(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # Safe paths are canonical, including macOS /var -> /private/var.
            self.assertEqual(safe_relative_path(root, "cpr\\LAD\\image.nrrd"), (root / "cpr" / "LAD" / "image.nrrd").resolve())
            for bad in ("../secret", "a/../../secret", "/root/file", "C:\\private\\file", "C:relative", "\\\\server\\file", "data:stream", "", "."):
                with self.subTest(path=bad), self.assertRaises(ValueError):
                    safe_relative_path(root, bad)
