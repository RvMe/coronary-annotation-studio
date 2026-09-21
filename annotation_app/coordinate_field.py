"""Dense CPR-to-native LPS mapping; no affine approximation of nonlinear warps."""
from __future__ import annotations
import math
import numpy as np
from scipy.ndimage import map_coordinates
from .imaging import CPRPath, GeometryError, _finite


class CoordinateFieldPath(CPRPath):
    def __init__(self, volume, distances, native_lps, spacing_uv, native=None, path_id="", segment_ids=None):
        self.volume = np.asarray(volume)
        self.distances = np.array(distances, dtype=float, copy=True)
        self.native_lps = np.array(native_lps, dtype=float, copy=True)
        self.spacing_uv = np.asarray(spacing_uv, dtype=float)
        if self.volume.ndim != 3 or min(self.volume.shape) < 2 or not np.isfinite(self.volume).all():
            raise GeometryError("coordinate_field: expected finite scalar CPR (N,H,W), each dimension >=2")
        if self.distances.shape != (len(volume),) or not np.isfinite(self.distances).all() or abs(self.distances[0]) > 1e-8 or np.any(np.diff(self.distances) <= 0):
            raise GeometryError("coordinate_field/distance_mm: must start at zero and strictly increase")
        if self.native_lps.shape != self.volume.shape + (3,) or not np.isfinite(self.native_lps).all():
            raise GeometryError("coordinate_field/native_lps: expected finite (N,H,W,3) LPS millimetres")
        if self.spacing_uv.shape != (2,) or not np.isfinite(self.spacing_uv).all() or np.any(self.spacing_uv <= 0):
            raise GeometryError("coordinate_field/inplane_spacing_mm: expected two positive values [u,v]")
        self.length_mm = float(self.distances[-1])
        self.spacing_mm = float(self.spacing_uv[0])
        self.native, self.path_id = native, path_id
        self.segment_ids = np.zeros(len(volume), dtype=np.int32) if segment_ids is None else np.asarray(segment_ids, dtype=np.int32)
        if self.segment_ids.shape != (len(volume),):
            raise GeometryError("coordinate_field/segment_id: count differs from CPR")
        self.centers = np.array([self.point(s) for s in self.distances])
        if np.any(np.linalg.norm(np.diff(self.centers, axis=0), axis=1) < 1e-8):
            raise GeometryError("coordinate_field: consecutive center positions coincide")
        for arr in (self.volume, self.distances, self.native_lps, self.centers, self.segment_ids):
            arr.flags.writeable = False

    def _coords(self, s, u=0., v=0.):
        s, u, v = np.broadcast_arrays(np.asarray(s, float), np.asarray(u, float), np.asarray(v, float))
        if not all(np.isfinite(a).all() for a in (s, u, v)):
            raise GeometryError("CPR coordinates must be finite")
        rows = np.interp(s, self.distances, np.arange(len(self.distances)))
        y = v / self.spacing_uv[1] + (self.volume.shape[1] - 1) / 2
        x = u / self.spacing_uv[0] + (self.volume.shape[2] - 1) / 2
        valid = (s >= 0) & (s <= self.length_mm) & (x >= 0) & (x <= self.volume.shape[2]-1) & (y >= 0) & (y <= self.volume.shape[1]-1)
        return np.array([rows.ravel(), y.ravel(), x.ravel()]), s.shape, valid

    def points(self, s, u=0., v=0.):
        coords, shape, valid = self._coords(s, u, v)
        if not np.all(valid):
            raise GeometryError("CPR position is outside the supplied coordinate field; extrapolation is undefined")
        return np.stack([map_coordinates(self.native_lps[..., k], coords, order=1, mode="nearest", prefilter=False) for k in range(3)], axis=-1).reshape(shape+(3,))

    def point(self, s_mm, u_mm=0., v_mm=0.):
        return self.points(s_mm, u_mm, v_mm)

    def display_point(self, s_mm, u_mm=0., v_mm=0., angle_deg=0.):
        angle = math.radians(_finite(angle_deg, "angle"))
        c, s = math.cos(angle), math.sin(angle)
        return self.point(s_mm, c*u_mm-s*v_mm, s*u_mm+c*v_mm)

    def _reslice(self, s, u, v):
        coords, shape, valid = self._coords(s, u, v)
        # The supplied CPR remains authoritative. Sampling its mapped field must
        # never manufacture a frame or extrapolate beyond a finite mapping.
        values = map_coordinates(self.volume, coords, order=1, mode="constant", cval=-1024, output=np.float32, prefilter=False).reshape(shape)
        return np.where(valid, values, -1024).astype(np.float32)

    def longitudinal(self, angle_deg=0., offset_mm=0.):
        angle = math.radians(_finite(angle_deg, "angle"))
        u = (np.arange(self.volume.shape[2])-(self.volume.shape[2]-1)/2)*self.spacing_uv[0]
        offset = _finite(offset_mm, "offset")
        return self._reslice(self.distances[:,None], (u*math.cos(angle)-offset*math.sin(angle))[None,:], (u*math.sin(angle)+offset*math.cos(angle))[None,:])

    def cross_section(self, s_mm, angle_deg=0.):
        h, w = self.volume.shape[1:]
        u, v = np.meshgrid((np.arange(w)-(w-1)/2)*self.spacing_uv[0], (np.arange(h)-(h-1)/2)*self.spacing_uv[1])
        angle = math.radians(_finite(angle_deg, "angle"))
        return self._reslice(self.clamp_s(s_mm), u*math.cos(angle)-v*math.sin(angle), u*math.sin(angle)+v*math.cos(angle))

    def frame(self, s_mm):
        raise GeometryError("A nonlinear coordinate field has no assumed orthonormal native frame; use point/display_point")
