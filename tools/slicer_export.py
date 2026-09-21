"""Run inside 3D Slicer. Export EXISTING straightened CPR, CT and transform.

This adapter performs no centerline extraction, CPR generation or diagnosis.
For SlicerSandbox use mapping_direction='from_parent': its transform from the
straightened parent coordinates maps back into the native volume's RAS space.
Volume/transform parents must be hardened explicitly before export.
"""
from pathlib import Path
import hashlib
import json
import re
import numpy as np


def _id(value):
    if not isinstance(value,str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}",value):
        raise ValueError("Identity must be 1–96 portable letters, digits, dots, underscores or hyphens")
    return value


def _record(path, root):
    return {"path":path.relative_to(root).as_posix(),"bytes":path.stat().st_size,"sha256":hashlib.sha256(path.read_bytes()).hexdigest()}


def export_package(native_node, paths, destination, *, project_id, case_id, geometry_id,
                   intensity_units, native_index_tolerance=0.1, synthetic=False):
    """paths=[{path_id, cpr_node, transform_node, mapping_direction}].

CPR K must be the along-path axis. No 2D projections. Mapping direction must be
explicit ('from_parent' for SlicerSandbox or 'to_parent' for a supplied forward
CPR-to-native transform). Input nodes must have no unhandled parent transforms.
Subvoxel comparison is measured against Slicer's actual transform and published
in the package report; this is a sampled numerical check, not clinical QA.
"""
    import slicer
    import vtk
    from scipy.ndimage import map_coordinates
    for value in (project_id,case_id,geometry_id):_id(value)
    if intensity_units!="HU":raise ValueError("Explicit intensity_units='HU' declaration required")
    if not 0<native_index_tolerance<=0.1:raise ValueError("Tolerance must be positive and no greater than 0.1 voxel")
    destination=Path(destination).resolve()
    if destination.exists():raise ValueError("Export requires a new destination")
    if not paths:raise ValueError("Provide at least one existing 3D CPR")
    native_array=slicer.util.arrayFromVolume(native_node)
    if native_array.ndim!=3 or min(native_array.shape)<2 or not np.isfinite(native_array).all():
        raise ValueError("Native CT must be finite scalar 3D")
    if native_node.GetParentTransformNode():raise ValueError("Harden native volume parent transforms before export")
    nras=vtk.vtkMatrix4x4();native_node.GetRASToIJKMatrix(nras)
    native_ras_to_index=np.array([[nras.GetElement(i,j) for j in range(4)] for i in range(4)])
    destination.mkdir(parents=True)
    root=destination/case_id;root.mkdir()
    if not slicer.util.saveNode(native_node,str(root/"native.nrrd")):raise IOError("Cannot save native CT")
    records={};reports=[]
    for entry in paths:
        pid=_id(entry["path_id"])
        if pid in records:raise ValueError("Duplicate path_id")
        cpr,transform=entry["cpr_node"],entry["transform_node"]
        if cpr.GetParentTransformNode() or transform.GetParentTransformNode():
            raise ValueError(f"{pid}: CPR and mapping transform must have no unhandled parents")
        direction=entry.get("mapping_direction")
        if direction not in ("from_parent","to_parent"):
            raise ValueError(f"{pid}: explicitly choose mapping_direction from_parent or to_parent")
        mapping=(transform.GetTransformFromParent() if direction=="from_parent"
                 else transform.GetTransformToParent())
        array=slicer.util.arrayFromVolume(cpr)
        if array.ndim!=3 or min(array.shape)<2 or not np.isfinite(array).all():
            raise ValueError(f"{pid}: 3D CPR required; MIP/screenshots/projections are unsupported")
        matrix=vtk.vtkMatrix4x4();cpr.GetIJKToRASMatrix(matrix)
        affine=np.array([[matrix.GetElement(i,j) for j in range(4)] for i in range(4)])
        def mapped(ijk):
            ras=np.asarray(ijk)@affine[:3,:3].T+affine[:3,3]
            return np.array([mapping.TransformPoint(tuple(p)) for p in ras.reshape(-1,3)]).reshape(ras.shape)
        n,h,w=array.shape
        field=np.empty((n,h,w,3),dtype=np.float64)
        y,x=np.indices((h,w),dtype=float)
        for k in range(n):
            ijk=np.stack((x,y,np.full_like(x,k)),axis=-1)
            field[k]=mapped(ijk)*np.array([-1.,-1.,1.])
        centers=mapped(np.column_stack((np.full(n,(w-1)/2),np.full(n,(h-1)/2),np.arange(n))))
        native_indices=centers@native_ras_to_index[:3,:3].T+native_ras_to_index[:3,3]
        if not np.isfinite(field).all() or not np.isfinite(native_indices).all():
            raise ValueError(f"{pid}: nonfinite native mapping")
        if np.any(native_indices<-.5) or np.any(native_indices>np.array(native_array.shape[::-1])-.5):
            raise ValueError(f"{pid}: path center outside native CT; check mapping direction and transform context")
        distance=np.r_[0.,np.cumsum(np.linalg.norm(np.diff(centers,axis=0),axis=1))]
        if np.any(np.diff(distance)<=1e-8):raise ValueError(f"{pid}: degenerate along-path mapping")
        # Deterministic random interior, every slice center, image corners and
        # all along-path half-samples. Endpoint and fractional-grid tests included.
        rng=np.random.default_rng(1729)
        probes=rng.uniform([0,0,0],[w-1,h-1,n-1],size=(12000,3))
        centers_ijk=np.column_stack((np.full(2*n-1,(w-1)/2),np.full(2*n-1,(h-1)/2),np.arange(0,n-.5,.5)))
        corners=np.array([[i,j,k] for i in (0,w-1) for j in (0,h-1) for k in (0,n-1)],float)
        probes=np.vstack((probes,centers_ijk,corners))
        actual=mapped(probes)
        interp=np.column_stack([map_coordinates(field[...,d],probes[:,::-1].T,order=1,mode="nearest",prefilter=False) for d in range(3)])*np.array([-1.,-1.,1.])
        error=np.linalg.norm((interp-actual)@native_ras_to_index[:3,:3].T,axis=1)
        maximum=float(np.max(error))
        report={"path_id":pid,"mapping_direction":direction,"sample_count":len(probes),
                "maximum_native_continuous_index_error_voxel":maximum,"limit_voxel":native_index_tolerance,
                "status":"PASS" if maximum<=native_index_tolerance else "FAIL","sampling":"12000 seeded random subvoxels, all centers/half-slices and volume corners"}
        reports.append(report)
        if maximum>native_index_tolerance:
            (root/"mapping-validation-failed.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
            raise ValueError(f"{pid}: mapping error {maximum:.6g} exceeds {native_index_tolerance} voxel. Export with finer CPR sampling; package not completed.")
        if not slicer.util.saveNode(cpr,str(root/f"{pid}.nrrd")):raise IOError(f"Cannot save {pid}")
        np.savez_compressed(root/f"{pid}.npz",distance_mm=distance,native_lps=field)
        records[pid]={"image":f"{pid}.nrrd","display_name":entry.get("display_name",pid),
            "mapping":{"type":"coordinate_field","file":f"{pid}.npz","axis_order":"s,v,u","inplane_spacing_mm":list(cpr.GetSpacing()[:2])}}
    report={"schema_version":"cas-slicer-validation-1.0","slicer_version":slicer.app.applicationVersion,
            "status":"PASS","paths":reports,"synthetic":bool(synthetic),"meaning":"Sampled geometry agreement only; no reader/clinical acceptance"}
    (root/"slicer-validation.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    m={"schema_version":"cas-case-1.0","project_id":project_id,"case_id":case_id,"geometry_id":geometry_id,
       "units":"mm","coordinate_system":"LPS","intensity_units":"HU","native":"native.nrrd","paths":records,
       "annotation_scope":list(records),"canonical_lm":{"status":"not_present"},
       "metadata":{"generator":"Coronary Annotation Studio Slicer exporter","slicer_version":slicer.app.applicationVersion,"synthetic":bool(synthetic)},
       "files":[_record(p,root) for p in sorted(root.iterdir()) if p.is_file()]}
    (root/"case.json").write_text(json.dumps(m,indent=2),encoding="utf-8")
    (destination/"manifest.json").write_text(json.dumps({"schema_version":"cas-package-1.0","project_id":project_id,
        "name":project_id,"cases":[{"case_id":case_id,"case_manifest":f"{case_id}/case.json"}]},indent=2),encoding="utf-8")
    return report
