"""Bundle Astropy runtime without optional WCS plotting/upstream test payloads.

The default hook imports wcsaxes, which raises pytest's Skip exception when
pytest is installed but Matplotlib is absent. FITS previews need no plotting.
"""
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata


def runtime_module(name):
    return "tests" not in name.split(".") and not name.startswith("astropy.visualization.wcsaxes")


excludedimports = ["astropy.visualization.wcsaxes", "pytest"]
# Astropy's initializer requires this helper to expose astropy.test, but
# neither its actual test suite nor the pytest engine is a runtime dependency.
hiddenimports = collect_submodules("astropy", filter=runtime_module) + ["astropy.tests.runner", "numpy.lib.recfunctions"]
datas = collect_data_files("astropy", excludes=["**/tests/**", "**/wcsaxes/**"])
datas += copy_metadata("astropy") + copy_metadata("numpy")
datas += [(path, target) for path, target in collect_data_files("astropy", include_py_files=True)
          if path.endswith(("_parsetab.py", "_lextab.py"))]
