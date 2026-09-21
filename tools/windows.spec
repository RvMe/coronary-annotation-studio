from pathlib import Path
project = Path(SPECPATH).parent
datas = []
for folder in ('translations', 'resources'):
    source = project / 'annotation_app' / folder
    if source.is_dir():
        datas.append((str(source), 'annotation_app/' + folder))
a = Analysis([str(project/'annotation_app'/'launch.py')], pathex=[str(project)], binaries=[], datas=datas,
    hiddenimports=[], hookspath=[], hooksconfig={}, noarchive=False,
    excludes=['tkinter','PyQt5','PyQt6','PySide2','vtk','torch','matplotlib','pandas','openpyxl','IPython',
              'PySide6.QtWebEngineCore','PySide6.QtWebEngineWidgets','PySide6.QtWebEngineQuick','PySide6.QtQml','PySide6.QtQuick'])
allowed_qt = {'Qt6Core.dll','Qt6Gui.dll','Qt6Widgets.dll','Qt6Network.dll','Qt6OpenGL.dll'}
allowed_platform = {'qwindows.dll','qminimal.dll','qoffscreen.dll'}
def needed(item):
    dest=item[0].replace('\\','/');name=dest.rsplit('/',1)[-1]
    if name.startswith('Qt6') and name.endswith('.dll') and name not in allowed_qt:return False
    if 'PySide6/plugins/' in dest:
        rest=dest.split('PySide6/plugins/',1)[1]
        return rest.startswith('styles/') or (rest.startswith('platforms/') and name in allowed_platform)
    return 'PySide6/qml/' not in dest
a.binaries=[item for item in a.binaries if needed(item)]
a.datas=[item for item in a.datas if needed(item)]
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,[],exclude_binaries=True,name='CoronaryAnnotationStudio',debug=False,
        bootloader_ignore_signals=False,strip=False,upx=False,console=False,disable_windowed_traceback=False)
coll=COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='CoronaryAnnotationStudio')
