from copy import deepcopy
import base64
import csv
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import numpy as np
import SimpleITK as sitk
from annotation_app.synthetic import generate
from annotation_app.imaging import load_case, GeometryError
from annotation_app.package import validate_package, file_record, source_for_case
from annotation_app.coordinate_field import CoordinateFieldPath
from annotation_app.projects import project_for_package, database_for_project, bind_database
from annotation_app.domain import empty_state, apply_annotation, coverage_report
from annotation_app.storage import AnnotationStore
from annotation_app.legacy import import_package, import_annotations
from annotation_app.legacy_storage import AnnotationStore as LegacyStore
from tests.test_domain import annotation
from tests.test_imaging import write_tiny_case


class PublicPackageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="cas-package-test-")
        self.root=Path(self.temp.name)
        self.package=self.root/"demo"
        generate(self.package)

    def tearDown(self):self.temp.cleanup()

    def edit_case(self,cid,fn):
        path=self.package/cid/"case.json"
        m=json.loads(path.read_text());fn(m);path.write_text(json.dumps(m));return path

    def test_single_multiple_shared_paths_and_optional_metadata(self):
        report=validate_package(self.package)
        self.assertEqual([len(c['paths']) for c in report['cases']],[1,3,2])
        case=load_case(self.package/"single/case.json")
        self.assertNotIn("dataset",source_for_case(case));self.assertNotIn("split",source_for_case(case))

    def test_missing_hu_and_wrong_axes_fail_with_location(self):
        path=self.edit_case("single",lambda m:m.pop("intensity_units"))
        with self.assertRaisesRegex(GeometryError,"intensity_units"):load_case(path)
        def axes(m):m['paths']['LAD']['mapping']['axis_order']='u,v,s'
        path=self.edit_case("multiple",axes)
        with self.assertRaisesRegex(GeometryError,"paths/LAD.*axis_order"):load_case(path)

    def test_declared_scope_only_and_generic_id(self):
        state=empty_state("same","A")
        rec=annotation(0,10,path="coronary-path-A",finding="negative")
        state=apply_annotation(state,rec)
        self.assertTrue(coverage_report(state,{"coronary-path-A":10})['complete'])
        path=self.edit_case("multiple",lambda m:m.update(annotation_scope=['LAD']))
        self.assertEqual(load_case(path).manifest['annotation_scope'],['LAD'])

    def test_shared_lm_requires_explicit_evidence(self):
        path=self.edit_case("shared-lm",lambda m:m['canonical_lm'].pop('relationship_id'))
        with self.assertRaisesRegex(GeometryError,"relationship"):load_case(path)

    def test_hash_damage_identifies_path(self):
        path=self.package/'single/coronary-path-A.npz'
        raw=path.read_bytes();path.write_bytes(raw[:-1]+bytes([raw[-1]^1]))
        with self.assertRaisesRegex(GeometryError,"coronary-path-A.npz.*SHA-256"):load_case(self.package/'single/case.json')

    def test_absolute_asset_and_unbound_mapping_rejected(self):
        path=self.edit_case("single",lambda m:m.update(native="../other.nii.gz"))
        with self.assertRaises(ValueError):load_case(path)

    def test_case_identity_collision_across_projects(self):
        one={"project_id":"one","geometry_id":"g"};two={"project_id":"two","geometry_id":"g"}
        with AnnotationStore(self.root/'one/db.sqlite') as a,AnnotationStore(self.root/'two/db.sqlite') as b:
            a.commit(apply_annotation(a.load('same','reader',one),annotation()),'apply')
            self.assertEqual(b.load('same','reader',two)['annotations'],[])
            with self.assertRaisesRegex(ValueError,'Project'):a.load('same','reader',two)
        self.assertNotEqual(database_for_project('ABC'),database_for_project('abc'))

    def test_package_cannot_mix_projects(self):
        self.edit_case('single',lambda m:m.update(project_id='other'))
        with self.assertRaisesRegex(ValueError,'project or case identity'):project_for_package(self.package)

    def test_reused_geometry_id_cannot_silently_rebind_changed_image_assets(self):
        path=self.package/'single/case.json';case=load_case(path)
        with AnnotationStore(self.root/'bound/annotations.sqlite') as store:
            store.commit(apply_annotation(store.load(case.case_id,'A',source_for_case(case)),annotation(0,10,path='coronary-path-A')),'apply')
            imagepath=self.package/'single/native.nii.gz'
            changed=sitk.ReadImage(str(imagepath))+1
            sitk.WriteImage(changed,str(imagepath))
            self.edit_case('single',lambda m:m.update(files=[file_record(path.parent/item['path'],path.parent) for item in m['files']]))
            replacement=load_case(path)
            self.assertEqual(replacement.manifest['geometry_id'],case.manifest['geometry_id'])
            self.assertNotEqual(source_for_case(replacement)['geometry_sha256'],source_for_case(case)['geometry_sha256'])
            with self.assertRaisesRegex(ValueError,'provenance'):store.load(case.case_id,'A',source_for_case(replacement))

    def test_offline_export_and_batch_manifest(self):
        case=load_case(self.package/'single/case.json');db=self.root/'records/db.sqlite'
        with AnnotationStore(db) as store:
            state=store.load(case.case_id,'A',source_for_case(case))
            store.commit(apply_annotation(state,annotation(0,10,path='coronary-path-A')),'apply')
        self.package.rename(self.root/'disconnected')
        with AnnotationStore(db) as store:result=store.export_batch('A',self.root/'out')
        manifest=Path(result['manifest']);data=json.loads(manifest.read_text())
        self.assertEqual(result['case_count'],1)
        self.assertEqual(data['project_id'],'synthetic-demo-v1')
        for item in data['files']:
            self.assertEqual(file_record(manifest.parent/item['path'],manifest.parent),item)
        exported_csv=next(manifest.parent.rglob('annotations.csv'))
        with exported_csv.open(encoding='utf-8-sig',newline='') as stream:rows=list(csv.DictReader(stream))
        self.assertEqual(rows[0]['project_id'],'synthetic-demo-v1')
        self.assertEqual(rows[0]['geometry_id'],'synthetic-single-v1')
        self.assertFalse(any(p.suffix in ('.nrrd','.npz','.gz') for p in manifest.parent.rglob('*')))

    def test_shared_dedup_not_inferred(self):
        c=load_case(self.package/'shared-lm/case.json')
        lengths={p:c.paths[p].length_mm for p in c.manifest['annotation_scope']}
        report=coverage_report(empty_state(c.case_id,'A'),lengths,canonical_lm=c.canonical_lm)
        plain=coverage_report(empty_state(c.case_id,'A'),lengths)
        self.assertLess(report['target_mm'],plain['target_mm'])


class CoordinateFieldTests(unittest.TestCase):
    def field(self):
        distances=np.array([0.,.6,2.,3.2,5.])
        z,y,x=np.meshgrid(distances,np.arange(4)*.8-1.2,np.arange(6)*.4-1.,indexing='ij')
        field=np.stack((-30+x+.07*y*z,15+y+.1*x*z,-20+z+.01*x*y),axis=-1)
        return CoordinateFieldPath(x+2*y+3*z,distances,field,[.4,.8],path_id='A')

    def test_nonlinear_subvoxel_ras_lps_nonuniform_and_endpoints(self):
        p=self.field()
        for s,u,v in [(0,0,0),(5,0,0),(1.123,.17,-.31),(4.11,-.6,.8)]:
            expected=np.array([-30+u+.07*v*s,15+v+.1*u*s,-20+s+.01*u*v])
            np.testing.assert_allclose(p.point(s,u,v),expected,atol=1e-12)
        self.assertEqual(p.cross_section(1.123).shape,(4,6))
        self.assertAlmostEqual(p.row_at(1.3),1.5)

    def test_no_extrapolation_or_fabricated_frame(self):
        p=self.field()
        for args in [(-1,0,0),(6,0,0),(2,2,0),(2,0,3)]:
            with self.assertRaises(GeometryError):p.point(*args)
        with self.assertRaises(GeometryError):p.frame(1)

    def test_readonly_arrays_and_wrong_shape(self):
        p=self.field();self.assertFalse(p.native_lps.flags.writeable)
        with self.assertRaises(GeometryError):CoordinateFieldPath(p.volume,p.distances,p.native_lps[1:],[.4,.8])


class LegacyImportTests(unittest.TestCase):
    def test_legacy_endpoint_overshoot_is_converted_using_validated_reader_geometry(self):
        with tempfile.TemporaryDirectory(prefix='cas-legacy-endpoint-') as folder:
            root=Path(folder);old=root/'old';old.mkdir();casepath,m=write_tiny_case(old)
            for pid,record in m['paths'].items():
                frames=old/record['frames']
                with np.load(frames) as data:fields={key:data[key] for key in data.files}
                fields['distance_mm'][-1]=10.25
                np.savez_compressed(frames,**fields)
                csvpath=old/record['path']
                with csvpath.open(newline='',encoding='utf-8') as stream:rows=list(csv.DictReader(stream))
                rows[-1]['distance_mm']='10.25'
                with csvpath.open('w',newline='',encoding='utf-8') as stream:
                    writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
            m['files']=[file_record(old/item['path'],old) for item in m['files']]
            casepath.write_text(json.dumps(m))
            (old/'manifest.json').write_text(json.dumps({'schema_version':'imagecasx-package-1.0','cases':[{'case_id':'synthetic','case_manifest':'case.json'}]}))
            original=(old/m['paths']['LAD']['frames']).read_bytes()
            import_package(old,root/'new','endpoint-project')
            case=load_case(root/'new/synthetic/case.json')
            self.assertEqual(case.paths['LAD'].length_mm,10.2)
            self.assertEqual(case.paths['LAD'].distances[-1],10.2)
            self.assertEqual((old/m['paths']['LAD']['frames']).read_bytes(),original)

    def test_lossless_explicit_import_without_modifying_original_files_or_database(self):
        with tempfile.TemporaryDirectory(prefix='cas-legacy-test-') as folder:
            root=Path(folder);old=root/'old';old.mkdir();casepath,_=write_tiny_case(old)
            (old/'manifest.json').write_text(json.dumps({'schema_version':'imagecasx-package-1.0','cases':[{'case_id':'synthetic','case_manifest':'case.json'}]}))
            with LegacyStore(root/'legacy.sqlite') as store:
                source={'geometry_id':'synthetic-v1','dataset':'ImageCAS-X'}
                saved=store.commit(apply_annotation_legacy(store.load('synthetic','A',source),annotation(1,3)),'apply')
                exported=store.export_case('synthetic','A',root/'exports')
            before={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
            import_package(old,root/'new','import-project')
            case=load_case(root/'new/synthetic/case.json')
            with AnnotationStore(root/'newdb/labels.sqlite') as store:
                imported=import_annotations(exported,store,case)
                self.assertEqual(imported['annotations'],saved['annotations'])
                audit=store._audit_records('synthetic','A')[-1]
                self.assertEqual(audit['details']['original_snapshot'],saved)
                self.assertEqual(base64.b64decode(audit['details']['original_files_base64']['annotations.json']),exported.read_bytes())
                with self.assertRaisesRegex(ValueError,'conflict'):import_annotations(exported,store,case)
            for path,digest in before.items():self.assertEqual(hashlib.sha256((root/path).read_bytes()).hexdigest(),digest)
            with self.assertRaisesRegex(ValueError,'Legacy/unknown'):AnnotationStore(root/'legacy.sqlite')
            self.assertEqual(hashlib.sha256((root/'legacy.sqlite').read_bytes()).hexdigest(),before['legacy.sqlite'])


from annotation_app.legacy_domain import apply_annotation as apply_annotation_legacy
