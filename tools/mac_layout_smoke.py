"""Cocoa layout assertions at compact/default sizes, with saved pixel evidence."""
import argparse,json,sys,traceback,hashlib,platform
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QPushButton
from annotation_app.app import MainWindow
from annotation_app.label_logic import label_applicability


def main():
 p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--package',type=Path,required=True);a=p.parse_args()
 out=a.output.resolve();out.mkdir(parents=True,exist_ok=False);app=QApplication([]);app.setQuitOnLastWindowClosed(False)
 report={'status':'FAIL','machine':platform.machine(),'qt_platform':app.platformName(),'checks':[],'screenshots':[]};w=None
 def check(name,value):
  report['checks'].append({'name':name,'passed':bool(value)})
  if not value:raise AssertionError(name)
 try:
  check('native_interactive_platform',app.platformName()==('windows' if sys.platform=='win32' else 'cocoa'))
  w=MainWindow(out/'workspace/qa.sqlite',a.package,'QA-LAYOUT','single');errors=[];w.error=lambda title,error:errors.append(str(error));w.show()
  for _ in range(1600):
   QTest.qWait(25)
   if errors:raise RuntimeError(errors)
   if w.case and not w.loading and not w.render_busy and all(c.qimage is not None for c in w.canvases):break
  check('ready_synthetic',w.case is not None and w.case.manifest['metadata']['synthetic'] is True and not w.render_busy)
  w.set_label('finding_status','positive')
  available=w.screen().availableGeometry()
  maximum_width=available.width()-(w.frameGeometry().width()-w.width())-2
  maximum_height=available.height()-(w.frameGeometry().height()-w.height())-2
  sizes=list(dict.fromkeys((min(width,maximum_width),min(height,maximum_height)) for width,height in [(1280,800),(1440,900),(1050,650)]))
  report['available_logical_size']=[available.width(),available.height()]
  report['device_pixel_ratio']=w.devicePixelRatioF()
  report['tested_logical_sizes']=sizes
  for width,height in sizes:
   w.resize(width,height)
   for language in ['en','zh_CN']:
    w.languages.set_language(language);QTest.qWait(100)
    prefix=f'{width}x{height}-{language}'
    check(prefix+'-requested_size',abs(w.width()-width)<=1 and abs(w.height()-height)<=1)
    check(prefix+'-form_no_horizontal_scroll',w.annotation_scroll.horizontalScrollBar().maximum()==0)
    viewport=w.annotation_scroll.viewport()
    for key,(_,buttons) in w.groups.items():
     if w.field_sections[key].isVisible():
      for value,button in buttons.items():
       left=button.mapTo(viewport,QPoint(0,0)).x()
       check(prefix+'-choice-'+key+'-'+value,left>=0 and left+button.width()<=viewport.width())
    check(prefix+'-apply_visible',w.rect().contains(w.apply_button.mapTo(w,QPoint(0,0))) and w.rect().contains(w.apply_button.mapTo(w,w.apply_button.rect().bottomRight())))
    for kind,panel in w.image_panels.items():
     canvas=getattr(w,kind)
     check(prefix+'-canvas-contained-'+kind,panel.rect().contains(canvas.mapTo(panel,QPoint(0,0))) and panel.rect().contains(canvas.mapTo(panel,canvas.rect().bottomRight())))
     for index,button in enumerate(panel.findChildren(QPushButton)):
      left=button.mapTo(panel,QPoint(0,0)).x()
      check(prefix+'-image-action-'+kind+str(index),left>=0 and left+button.width()<=panel.width())
    shot=out/(prefix+'.png');check(prefix+'-screenshot',w.grab().save(str(shot)))
    report['screenshots'].append({'path':shot.name,'sha256':hashlib.sha256(shot.read_bytes()).hexdigest()})
  check('normal_close',w.close() and w._last_close_save_succeeded)
  report['status']='PASS'
 except Exception:report['failure']=traceback.format_exc();print(report['failure'],file=sys.stderr)
 finally:
  if w is not None and not w._close_completed:w._smoke_silent_exit=True;w.close()
  (out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps({'status':report['status'],'checks':len(report['checks'])}))
 return 0 if report['status']=='PASS' else 2


if __name__=='__main__':raise SystemExit(main())
