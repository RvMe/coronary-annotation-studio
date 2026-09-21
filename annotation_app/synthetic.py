"""Small offline coronary-shaped phantoms. No real patient data or anatomy claims."""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import SimpleITK as sitk
from .imaging import NativeVolume
from .package import file_record, validate_package


def generate(destination):
    destination = Path(destination).resolve()
    if destination.exists():
        raise ValueError("Demo destination must be a new directory")
    destination.mkdir(parents=True)
    z,y,x = np.indices((72,80,80), dtype=np.float32)
    hu = -120 + .5*x + .4*y + .8*z
    # Bright vessel-shaped tubes plus one small synthetic high-HU region.
    radius = (x-(38+5*np.sin(z/20)))**2+(y-40)**2
    hu += 360*np.exp(-radius/7)
    hu += 520*np.exp(-((x-42)**2+(y-41)**2+(z-30)**2)/5)
    native_image = sitk.GetImageFromArray(hu.astype(np.float32))
    native_image.SetSpacing((.7,.8,1.1))
    native_image.SetOrigin((-35.,-32.,-25.))
    native_image.SetDirection((0.,-1.,0.,1.,0.,0.,0.,0.,1.))
    native = NativeVolume(native_image)
    distances = np.r_[np.arange(0,40,.8),40.1]
    entries=[]
    for cid, pids, shared in (("single",["coronary-path-A"],False),("multiple",["LAD","LCX","RCA"],False),("shared-lm",["LAD","LCX"],True)):
        root=destination/cid
        root.mkdir()
        sitk.WriteImage(native.image,str(root/"native.nii.gz"))
        records={}
        for k,pid in enumerate(pids):
            # A bend plus divergent distal branches. Shared proximal centers are exact.
            t=distances
            branch=np.maximum(t-10,0)*.22*k if shared else np.full_like(t,k*3.)
            xyz=np.column_stack((38+5*np.sin((10+t/1.1)/20)+branch,40+branch*.6,10+t/1.1))
            centers=native.index_to_world(xyz)
            arc=np.r_[0.,np.cumsum(np.linalg.norm(np.diff(centers,axis=0),axis=1))]
            tangent=np.gradient(centers,axis=0);tangent/=np.linalg.norm(tangent,axis=1)[:,None]
            normal=np.tile([0.,1.,0.],(len(t),1));normal-=np.sum(normal*tangent,axis=1)[:,None]*tangent;normal/=np.linalg.norm(normal,axis=1)[:,None]
            binormal=np.cross(tangent,normal)
            u=(np.arange(32)-15.5)*.4;v=(np.arange(24)-11.5)*.4
            field=centers[:,None,None,:]+u[None,None,:,None]*normal[:,None,None,:]+v[None,:,None,None]*binormal[:,None,None,:]
            volume=native.sample(field)
            cpr=sitk.GetImageFromArray(volume);cpr.SetSpacing((.4,.4,.8))
            sitk.WriteImage(cpr,str(root/f"{pid}.nrrd"))
            # Single example exercises genuine nonlinear field transport; others frames.
            if cid=="single":
                np.savez_compressed(root/f"{pid}.npz",distance_mm=arc,native_lps=field)
                kind="coordinate_field"
            else:
                np.savez_compressed(root/f"{pid}.npz",distance_mm=arc,centers=centers,normals=normal,binormals=binormal,tangents=tangent)
                kind="frames"
            records[pid]={"image":f"{pid}.nrrd","display_name":pid,"mapping":{"type":kind,"file":f"{pid}.npz","axis_order":"s,v,u","inplane_spacing_mm":[.4,.4]}}
        lm={"status":"not_present"}
        if shared:
            lm={"status":"verified","owner":"LAD","reference_paths":["LAD","LCX"],"verified_end_mm":9.6,
                "relationship_id":"synthetic-shared-prefix","evidence":{"source":"Synthetic construction; identical proximal LPS centers"}}
        m={"schema_version":"cas-case-1.0","project_id":"synthetic-demo-v1","case_id":cid,"geometry_id":f"synthetic-{cid}-v1",
           "units":"mm","coordinate_system":"LPS","intensity_units":"HU","native":"native.nii.gz","paths":records,
           "annotation_scope":pids,"canonical_lm":lm,"metadata":{"synthetic":True,"description":"Mathematical phantom; no patient data"},
           "files":[file_record(p,root) for p in sorted(root.iterdir()) if p.is_file()]}
        (root/"case.json").write_text(json.dumps(m,indent=2),encoding="utf-8")
        entries.append({"case_id":cid,"case_manifest":f"{cid}/case.json"})
    (destination/"manifest.json").write_text(json.dumps({"schema_version":"cas-package-1.0","project_id":"synthetic-demo-v1",
        "name":"Synthetic coronary examples (not patient data)","cases":entries},indent=2),encoding="utf-8")
    return validate_package(destination)
