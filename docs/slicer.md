# Export existing CPRs from 3D Slicer

[简体中文](zh/data-format.md)

Install Slicer separately and open your existing scalar native CT, existing
three-dimensional straightened CPR, and its corresponding spatial transform.
The adapter does not create a centerline, CPR or projected image.

The first release expects the CPR's K axis to follow the path and all volumes
and the transform to have no unhandled parent transforms. Harden parent
transforms explicitly when appropriate and verify your input geometry first.
A 2D MIP/projection cannot supply the missing spatial mapping.

In Slicer's Python console, import the adapter from your source checkout:

```python
import sys
sys.path.insert(0, "/path/to/coronary-annotation-studio/tools")
from slicer_export import export_package

report = export_package(
    native_node=slicer.util.getNode("Native CT"),
    paths=[{
        "path_id": "coronary-A",
        "cpr_node": slicer.util.getNode("Straightened CPR"),
        "transform_node": slicer.util.getNode("Straightening transform"),
        "mapping_direction": "from_parent"
    }],
    destination="/new/local/package-directory",
    project_id="my-project-v1", case_id="case-001", geometry_id="geometry-v1",
    intensity_units="HU"
)
print(report)
```

`from_parent` matches SlicerSandbox CurvedPlanarReformat's straightening transform:
that direction maps the straightened coordinates back into native RAS. For an
independently supplied forward CPR-to-native transform, select `to_parent`
explicitly. The adapter samples Slicer's actual transform, then converts RAS to
LPS by negating X and Y. It never substitutes a scalar image affine for a
nonlinear warp. See the upstream
[CurvedPlanarReformat implementation](https://github.com/PerkLab/SlicerSandbox/blob/master/CurvedPlanarReformat/CurvedPlanarReformat.py)
and [Slicer transform documentation](https://slicer.readthedocs.io/en/latest/developer_guide/script_repository/transforms.html).

Export performs deterministic subvoxel checks against that transform. Maximum
Euclidean error in native continuous-index coordinates must be at most **0.1
voxel**. The report records the actual maximum, sample count and method. If the
sampled field is too coarse, export fails without completing a package; supply
a finer existing CPR grid. This is a sampled numerical agreement test, not a
proof of clinical or anatomical correctness.

Then run the desktop validator and open the package. Check native CT linkage
visually at both endpoints, a curved portion and a few off-center positions.
Shared LM is not inferred by the adapter; explicit relations require independent
identity evidence and the loader's geometry check.
