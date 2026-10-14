# PyInstaller spec: one folder with two executables sharing the same files.
#   TTSkyAnnot.exe       window (no console)
#   ttskyannot-cli.exe   console, for --annotate batch runs
import os

root = os.path.abspath(os.path.join(SPECPATH, ".."))
datas = [
    (os.path.join(root, "data"), "data"),
    (os.path.join(root, "LICENSE"), "."),
    (os.path.join(root, "THIRD_PARTY_NOTICES.md"), "."),
    (os.path.join(root, "README.md"), "."),
]
a = Analysis(
    [os.path.join(root, "packaging", "entry.py")],
    pathex=[root],
    datas=datas,
    hiddenimports=["skyannot.gui", "skyannot.run"],
    # The build machine has many unrelated packages (torch, dask, numba, ...). scipy/astropy reach them
    # through optional imports, so they are excluded explicitly; TTSkyAnnot uses none of them.
    # (astropy imports astropy.tests, astropy.io.votable and yaml at start-up: keep those.)
    excludes=["matplotlib", "mpl_toolkits", "IPython", "tkinter", "pandas", "astroquery", "cv2", "PySide6",
              "PyQt6", "notebook", "pytest", "sphinx", "jupyter_client", "zmq", "torch", "torchvision",
              "torchgen", "functorch", "dask", "numba", "llvmlite", "onnx", "onnxruntime", "zarr", "numcodecs",
              "sympy", "mpmath", "networkx", "skyfield", "sgp4", "jplephem", "pyarrow", "narwhals", "bs4",
              "lxml", "html5lib", "cryptography", "paramiko", "nacl", "bcrypt", "invoke", "aiohttp", "fsspec",
              "imagecodecs", "tensorboard", "jinja2", "cloudpickle", "partd", "fastavro", "google",
              "ml_dtypes", "tqdm", "win32com", "pywin", "cffi", "pycparser", "setuptools",
              "pkg_resources", "distutils", "toolz", "tlz", "polars", "duckdb", "pyspark", "ibis", "h5py",
              "astropy.visualization", "astropy.samp", "astropy.timeseries",
              "astropy.cosmology", "astropy.convolution", "astropy.modeling", "astropy.uncertainty", "scipy.io.matlab", "colorama", "psutil", "filelock", "donfig",
              "google_crc32c", "pytz", "cycler", "defusedxml", "soupsieve", "webencodings", "multiprocess"],
    noarchive=False,
)
pyz = PYZ(a.pure)
icon = os.path.join(root, "data", "app", "skyannot.ico")
gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name="TTSkyAnnot", console=False, icon=icon,
          upx=False)
cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name="ttskyannot-cli", console=True, icon=icon,
          upx=False)
coll = COLLECT(gui, cli, a.binaries, a.datas, name="TTSkyAnnot", upx=False)
