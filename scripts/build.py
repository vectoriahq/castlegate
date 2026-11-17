"""Build the Castlegate Server plugin folder and its zip.

  python scripts/build.py [output folder]

A QGIS plugin is installed as one self-contained folder, so the shared core is
copied into it here rather than installed beside it, and so are the status check
from install/common/, as tools/check.py, and the repository's LICENSE. Writes
<output>/castlegate_server/ and <output>/castlegate_server-<version>.zip; the
output folder defaults to build/.
"""

import configparser
import pathlib
import shutil
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc")


def build(out):
    out = pathlib.Path(out).resolve()
    plugin = out / "castlegate_server"
    if plugin.exists():
        shutil.rmtree(plugin)
    shutil.copytree(ROOT / "server" / "castlegate_server", plugin, ignore=IGNORE)
    shutil.copytree(ROOT / "core", plugin / "core", ignore=IGNORE)
    # The status check every install profile ends with, so it ships with the plugin.
    (plugin / "tools").mkdir()
    shutil.copy2(ROOT / "install" / "common" / "check.py", plugin / "tools" / "check.py")
    # The QGIS plugin repository refuses a zip without a LICENSE in the plugin's folder.
    shutil.copy2(ROOT / "LICENSE", plugin / "LICENSE")
    metadata = configparser.ConfigParser()
    metadata.read(plugin / "metadata.txt", encoding="utf-8")
    version = metadata["general"]["version"]
    archive = out / ("castlegate_server-%s.zip" % version)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(plugin.rglob("*")):
            if path.is_file():
                z.write(path, path.relative_to(out).as_posix())
    return plugin, archive


if __name__ == "__main__":
    plugin, archive = build(sys.argv[1] if len(sys.argv) > 1 else ROOT / "build")
    print(plugin)
    print(archive)
