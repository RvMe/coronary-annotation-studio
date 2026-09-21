"""Generate the published JSON Schemas from readable reusable definitions."""
import json
from pathlib import Path

ID={"type":"string","pattern":"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$"}
TEXT={"type":"string","minLength":1}
NUMBER={"type":"number"}
PATH={"type":"string","minLength":1,"description":"Relative portable asset path, contained within the case directory. Runtime validation rejects escapes/drives."}
def obj(properties,required=None):
    return {"type":"object","properties":properties,"required":list(properties) if required is None else required}
def array(items,minItems=0):return {"type":"array","items":items,"minItems":minItems}
def enum(*values):return {"enum":list(values)}
FILE=obj({"path":PATH,"bytes":{"type":"integer","minimum":0},"sha256":{"type":"string","pattern":"^[a-f0-9]{64}$"}})
MAPPING=obj({"type":enum('frames','coordinate_field'),"file":PATH,"axis_order":{"const":"s,v,u"},
             "inplane_spacing_mm":{"type":"array","items":{"type":"number","exclusiveMinimum":0},"minItems":2,"maxItems":2}})
PATH_RECORD=obj({"image":PATH,"mapping":MAPPING,"display_name":TEXT,"anatomical_identity":TEXT},['image','mapping'])
CASE=obj({"schema_version":{"const":"cas-case-1.0"},"project_id":ID,"case_id":ID,"geometry_id":ID,
          "units":{"const":"mm"},"coordinate_system":{"const":"LPS"},"intensity_units":{"const":"HU"},
          "native":PATH,"paths":{"type":"object","minProperties":1,"propertyNames":ID,"additionalProperties":PATH_RECORD},
          "annotation_scope":{"type":"array","items":ID,"uniqueItems":True,"minItems":1},"files":array(FILE,1),
          "metadata":{"type":"object"},"canonical_lm":obj({"status":enum('verified','not_present','ambiguous'),
          "owner":ID,"reference_paths":array(ID),"verified_end_mm":{"type":"number","exclusiveMinimum":0},
          "relationship_id":TEXT,"evidence":{}},['status'])},
         ['schema_version','project_id','case_id','geometry_id','units','coordinate_system','intensity_units','native','paths','annotation_scope','files'])
PACKAGE=obj({"schema_version":{"const":"cas-package-1.0"},"project_id":ID,"name":TEXT,
             "cases":array(obj({"case_id":ID,"case_manifest":PATH}),1)},['schema_version','project_id','cases'])
LABEL=obj({"finding_status":enum('negative','positive','uncertain','non_evaluable'),
           "plaque_composition":enum(None,'calcified','non_calcified','partially_calcified','uncertain'),
           "stenosis_grade":enum('0','1_24','25_49','50_69','70_99','100','unable'),
           "confidence":enum(None,'high','medium','low'),"reason":{"type":"string"},
           "reason_codes":{"type":"array","items":enum('motion_artifact','blooming_metal_artifact','poor_contrast','noise_blur','cpr_geometry_coverage','interpretive_uncertainty','other'),"uniqueItems":True},
           "s_peak_stenosis_mm":{"type":["number","null"],"minimum":0}},
          ['finding_status','plaque_composition','stenosis_grade','confidence'])
ANNOTATION=obj({"annotation_id":TEXT,"label_group_id":TEXT,"path_id":ID,"canonical_anatomy_id":TEXT,"anatomical_segment":TEXT,
                "s_start_mm":{"type":"number","minimum":0},"s_end_mm":{"type":"number","exclusiveMinimum":0},"label":LABEL,
                "native_anchors":{"type":["array","object"]},"provenance":{"type":"object"},"review_required":{"type":"boolean"},
                "review_status":TEXT},['annotation_id','label_group_id','path_id','canonical_anatomy_id','anatomical_segment','s_start_mm','s_end_mm','label','native_anchors','provenance','review_required'])
ANNOTATIONS=obj({"schema_version":{"const":"cas-annotations-1.0"},"case_id":ID,"reader_id":TEXT,"revision":{"type":"integer","minimum":0},
                 "source":obj({"project_id":ID,"geometry_id":ID,"geometry_sha256":{"type":"string","pattern":"^[a-f0-9]{64}$"},"annotation_scope":array(ID,1)},['project_id','geometry_id','geometry_sha256','annotation_scope']),
                 "annotations":array(ANNOTATION),"markers":array({"type":"object"}),"rereview_intervals":array({"type":"object"}),
                 "view_state":{"type":"object"},"case_status":enum('in_progress','complete','complete_with_gaps'),"completion":{"type":"object"}},
                ['schema_version','case_id','reader_id','revision','source','annotations','markers','rereview_intervals','view_state','case_status'])
EXPORT=obj({"schema_version":{"const":"cas-export-1.0"},"project_id":ID,"case_id":ID,"reader_id":TEXT,"revision":{"type":"integer","minimum":0},"files":array(FILE,3)})
BATCH=obj({"schema_version":{"const":"cas-batch-export-1.0"},"project_id":ID,"reader_id":TEXT,"cases":array(obj({"case_id":ID,"annotations":PATH})),"files":array(FILE)})

def main():
    root=Path(__file__).resolve().parents[1]/'schemas';root.mkdir(exist_ok=True)
    for name,schema in [('package',PACKAGE),('case',CASE),('annotations',ANNOTATIONS),('export',EXPORT),('batch-export',BATCH)]:
        document={"$schema":"https://json-schema.org/draft/2020-12/schema","title":f"Coronary Annotation Studio {name} 1.0",**schema}
        (root/f'{name}.schema.json').write_text(json.dumps(document,indent=2)+'\n',encoding='utf-8')

if __name__=='__main__':main()
