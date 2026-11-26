# PyInstaller spec - build with:  pyinstaller packaging/PoliSciRAG.spec --noconfirm
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

ROOT = SPECPATH + "/.."

hidden = (collect_submodules("uvicorn") + collect_submodules("polisci_rag")
          + ["pymupdf", "docx", "anyio._backends._asyncio"])

a = Analysis(
    [ROOT + "/launcher.py"],
    pathex=[ROOT],
    datas=[(ROOT + "/polisci_rag/static", "polisci_rag/static")] + collect_data_files("docx"),
    hiddenimports=hidden,
    excludes=["matplotlib", "pandas", "scipy", "torch", "IPython", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="PoliSciRAG",
    console=False,
    icon=ROOT + "/packaging/app.ico",
)
coll = COLLECT(exe, a.binaries, a.datas, name="PoliSciRAG")
