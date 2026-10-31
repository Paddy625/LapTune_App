# Build on Windows: python -m PyInstaller --clean --noconfirm telemetry_viewer.spec
from PyInstaller.utils.hooks import collect_data_files

analysis = Analysis(
    ['src/telemetry_viewer/__main__.py'],
    pathex=['src'],
    binaries=[],
    datas=collect_data_files('telemetry_viewer.maps') + collect_data_files('telemetry_viewer.gui')
          + collect_data_files('tzdata'),
    hiddenimports=['PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets',
                   'PySide6.QtWebChannel'],
    hookspath=[],
    hooksconfig={},
    excludes=['PyQt5', 'PyQt6', 'tkinter'],
    noarchive=False,
)
archive = PYZ(analysis.pure)
exe = EXE(archive, analysis.scripts, [], exclude_binaries=True, name='TelemetryViewer',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False, console=False)
coll = COLLECT(exe, analysis.binaries, analysis.datas, strip=False, upx=False,
               name='TelemetryViewer')
