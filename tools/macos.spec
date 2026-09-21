"""Thin Mac target; execute only after both preflight and integration gates pass."""
import os
from pathlib import Path

project=Path(SPECPATH).parent
architecture=os.environ['CAS_BUILD_ARCH']
if architecture not in {'arm64','x86_64'}:
    raise ValueError('CAS_BUILD_ARCH must select exactly arm64 or x86_64')
version='0.1.0'
a=Analysis([str(project/'annotation_app/launch.py')],pathex=[str(project)],
    binaries=[],datas=[(str(project/'annotation_app/translations'),'annotation_app/translations')],
    hiddenimports=[],hookspath=[],hooksconfig={},noarchive=False,
    excludes=['tkinter','PyQt5','PyQt6','PySide2','vtk','torch','matplotlib','pandas','openpyxl','IPython',
              'PySide6.QtWebEngineCore','PySide6.QtWebEngineWidgets','PySide6.QtWebEngineQuick','PySide6.QtQml','PySide6.QtQuick'])


def needed(item):
    destination=item[0].replace('\\','/')
    for value in (destination,item[1].replace('\\','/')):
        for part in value.split('/'):
            if part.startswith('Qt') and part.endswith('.framework'):
                if part[:-10] not in {'QtCore','QtGui','QtWidgets','QtDBus','QtNetwork','QtOpenGL'}:
                    return False
    if 'PySide6/Qt/plugins/' in destination:
        rest=destination.split('PySide6/Qt/plugins/',1)[1]
        return rest.startswith('styles/') or rest in {'platforms/libqcocoa.dylib','platforms/libqminimal.dylib','platforms/libqoffscreen.dylib'}
    return 'PySide6/Qt/qml/' not in destination


a.binaries=[item for item in a.binaries if needed(item)]
a.datas=[item for item in a.datas if needed(item)]
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,[],exclude_binaries=True,name='CoronaryAnnotationStudio',debug=False,
        bootloader_ignore_signals=False,strip=False,upx=False,console=False,disable_windowed_traceback=False,
        argv_emulation=False,target_arch=architecture,codesign_identity=None,entitlements_file=None)
coll=COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='CoronaryAnnotationStudio')
app=BUNDLE(coll,name='Coronary Annotation Studio.app',bundle_identifier='org.coronaryannotationstudio.desktop',
           version=version,info_plist={'CFBundleDisplayName':'Coronary Annotation Studio',
           'CFBundleShortVersionString':version,'CFBundleVersion':version,'LSMinimumSystemVersion':'12.0',
           'NSHighResolutionCapable':True,'NSHumanReadableCopyright':'Coronary Annotation Studio contributors — Apache-2.0'})
