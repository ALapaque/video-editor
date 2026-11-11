# Construction du moteur embarqué : un dossier autonome contenant Python et les dépendances.
#   uv run --with pyinstaller pyinstaller desktop/engine.spec --distpath desktop/engine-dist --workpath desktop/engine-build -y
# Un dossier (et non un fichier unique) : le démarrage est immédiat, rien n'est décompressé.
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

datas = collect_data_files("moto_blur")  # interface web compilée
binaries = collect_dynamic_libs("onnxruntime")

a = Analysis(
    ["engine_entry.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=["onnxruntime", "av", "cv2"],
    excludes=["tkinter", "matplotlib", "pytest"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="moto-blur-engine", console=True, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name="moto-blur-engine", upx=False)
