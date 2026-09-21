"""Run inside Slicer --testing --python-script; creates only mathematical data.

CAS_SLICER_OUTPUT must name a new local directory. Native CT, existing 3D CPR
and a nonlinear MRML transform are constructed before the exporter is called.
The exporter itself does not generate CPR or process patient data.
"""
import importlib.util
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import traceback
import numpy as np
import slicer
import vtk
from scipy.ndimage import map_coordinates

out=Path(os.environ['CAS_SLICER_OUTPUT']).resolve();out.mkdir(parents=True,exist_ok=False)
report={'status':'FAIL','synthetic_only':True,'slicer_version':slicer.app.applicationVersion,'machine':platform.machine(),'checks':[]}

def check(name,value):
    report['checks'].append({'name':name,'passed':bool(value)})
    if not value:raise AssertionError(name)

try:
    spec=importlib.util.spec_from_file_location('cas_slicer_export',Path(os.environ.get('CAS_SLICER_EXPORTER',str(Path(__file__).with_name('slicer_export.py')))))
    exporter=importlib.util.module_from_spec(spec);spec.loader.exec_module(exporter)
    import hashlib
    report['exporter_sha256']=hashlib.sha256(Path(spec.origin).read_bytes()).hexdigest()
    report['exporter_override_used']='CAS_SLICER_EXPORTER' in os.environ
    native=slicer.mrmlScene.AddNewNodeByClass('vtkMRMLScalarVolumeNode','Synthetic CT — no patient data')
    shape=(80,80,80);z,y,x=np.indices(shape,dtype=float)
    volume=(40+.4*x+.3*y+.2*z+80*np.exp(-((x-40)**2+(y-40)**2)/180)).astype(np.float32)
    slicer.util.updateVolumeFromArray(native,volume)
    theta=np.deg2rad(23);rot=np.array([[np.cos(theta),-np.sin(theta),0],[np.sin(theta),np.cos(theta),0],[0,0,1.]])
    affine=np.eye(4);affine[:3,:3]=rot@np.diag([.8,.9,1.2]);affine[:3,3]=-affine[:3,:3]@np.array([39.5]*3)
    matrix=vtk.vtkMatrix4x4()
    for i in range(4):
        for j in range(4):matrix.SetElement(i,j,float(affine[i,j]))
    native.SetIJKToRASMatrix(matrix);native.CreateDefaultDisplayNodes()
    source=vtk.vtkPoints();target=vtk.vtkPoints()
    for px in (-12.,0.,12.):
        for py in (-12.,0.,12.):
            for pz in (-20.,0.,20.):
                source.InsertNextPoint(px,py,pz)
                target.InsertNextPoint(px+.8*(py/10.)**2+.4*np.sin(pz/12),py+.4*np.sin(px/10),pz+.12*px*py/100)
    warp=vtk.vtkThinPlateSplineTransform();warp.SetSourceLandmarks(source);warp.SetTargetLandmarks(target);warp.SetBasisToR();warp.Update()
    transform=slicer.mrmlScene.AddNewNodeByClass('vtkMRMLTransformNode','Synthetic nonlinear CPR-to-native')
    transform.SetAndObserveTransformFromParent(warp)
    cpr=slicer.mrmlScene.AddNewNodeByClass('vtkMRMLScalarVolumeNode','Existing synthetic 3D CPR')
    n,h,w=25,21,31;spacing=np.array([.45,.7,1.1]);origin=-spacing*np.array([(w-1)/2,(h-1)/2,(n-1)/2])
    kk,jj,ii=np.indices((n,h,w),dtype=float);grid=np.stack((ii,jj,kk),axis=-1);ras=grid*spacing+origin
    mapped=np.array([warp.TransformPoint(tuple(p)) for p in ras.reshape(-1,3)]).reshape(ras.shape)
    inv=np.linalg.inv(affine);indices=mapped@inv[:3,:3].T+inv[:3,3]
    existing=map_coordinates(volume,indices[...,::-1].reshape(-1,3).T,order=1,mode='constant',cval=-1024).reshape((n,h,w)).astype(np.float32)
    slicer.util.updateVolumeFromArray(cpr,existing);cpr.SetSpacing(*spacing);cpr.SetOrigin(*origin);cpr.CreateDefaultDisplayNodes()
    entry={'path_id':'synthetic-curved-path','display_name':'Synthetic nonlinear path','cpr_node':cpr,'transform_node':transform,'mapping_direction':'from_parent'}
    options={'project_id':'slicer-synthetic-live','case_id':'nonlinear','geometry_id':'slicer-tps-v1','intensity_units':'HU','synthetic':True}
    # Reject unhandled parent chains explicitly. Do not harden/ignore them in
    # the adapter or reuse a partial rejected destination as a successful one.
    parent=slicer.mrmlScene.AddNewNodeByClass('vtkMRMLLinearTransformNode','QA parent')
    for label,node in [('native',native),('cpr',cpr),('mapping',transform)]:
        node.SetAndObserveTransformNodeID(parent.GetID())
        try:
            exporter.export_package(native,[entry],out/('rejected-parent-'+label),**options)
            raise AssertionError('Unhandled parent accepted: '+label)
        except ValueError as exc:
            check('reject_'+label+'_parent','parent' in str(exc).lower())
        finally:node.SetAndObserveTransformNodeID(None)
    result=exporter.export_package(native,[entry],out/'exported-package',**options)
    check('exporter_samples_pass',result['status']=='PASS' and all(p['maximum_native_continuous_index_error_voxel']<=.1 for p in result['paths']))
    report['exporter_report']=result
    # Independent deterministic sample stream, separate from the exporter's.
    rng=np.random.default_rng(99173);probes=rng.uniform([0,0,0],[w-1,h-1,n-1],size=(6000,3));probes[:2]=[[0,0,0],[w-1,h-1,n-1]]
    actual=np.array([warp.TransformPoint(tuple(p)) for p in probes*spacing+origin])
    with np.load(out/'exported-package/nonlinear/synthetic-curved-path.npz',allow_pickle=False) as archive:field=archive['native_lps']
    interpolated=np.column_stack([map_coordinates(field[...,k],probes[:,::-1].T,order=1,mode='nearest',prefilter=False) for k in range(3)])*[-1.,-1.,1.]
    error=np.linalg.norm((interpolated-actual)@inv[:3,:3].T,axis=1);maximum=float(error.max())
    check('independent_6000_subvoxel_samples',maximum<=.1)
    design=np.column_stack((probes,np.ones(len(probes))));fit=design@np.linalg.lstsq(design,actual,rcond=None)[0]
    nonlinear=float(np.linalg.norm((fit-actual)@inv[:3,:3].T,axis=1).max())
    check('transform_is_non_affine',nonlinear>.1)
    report['independent_comparison']={'samples':len(probes),'maximum_native_continuous_index_error_voxel':maximum,'limit_voxel':.1,'best_affine_residual_voxel':nonlinear,'mapping_direction':'from_parent','spacing_uv_mm':spacing[:2].tolist(),'native_spacing_mm':[.8,.9,1.2],'native_rotation_degrees':23,'transform_class':warp.GetClassName()}
    slicer.util.setSliceViewerLayers(background=native);slicer.util.resetSliceViews()
    for _ in range(5):slicer.app.processEvents()
    check('slicer_window_screenshot',slicer.util.mainWindow().grab().save(str(out/'slicer-synthetic.png')))
    report['proc_translated']=subprocess.run(['/usr/sbin/sysctl','-in','sysctl.proc_translated'],capture_output=True,text=True).stdout.strip() if sys.platform=='darwin' else None
    report['status']='PASS'
except Exception:
    report['failure']=traceback.format_exc();print(report['failure'],file=sys.stderr)
finally:
    (out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
    slicer.util.exit(0 if report['status']=='PASS' else 2)
