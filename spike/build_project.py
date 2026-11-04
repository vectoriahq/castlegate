"""Build the spike's GeoPackage and QGIS projects. Run inside the QGIS Server image:

  docker run --rm -e QT_QPA_PLATFORM=offscreen -v <out>:/io/data -v <this folder>:/work \
    --entrypoint python3 qgis/qgis-server:ltr /work/build_project.py

Writes /io/data/castlegate.gpkg, /io/data/castlegate.qgs (three layers) and
/io/data/castlegate-100.qgs (one hundred layers, for timing GetProjectSettings).
"""

import os
import random

from qgis.core import (
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransformContext,
    QgsFeature,
    QgsField,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
    QgsVectorFileWriter,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QVariant

OUT = "/io/data"
GPKG = os.path.join(OUT, "castlegate.gpkg")
PEOPLE = ["alice@example.org", "bob@example.org"]

app = QgsApplication([], False)
app.initQgis()


def memory_layer(name, rows):
    layer = QgsVectorLayer("Point?crs=EPSG:4326", name, "memory")
    fields = [
        QgsField("name", QVariant.String),
        QgsField("owner", QVariant.String),
        QgsField("status", QVariant.String),
        QgsField("secret", QVariant.String),
    ]
    layer.dataProvider().addAttributes(fields)
    layer.updateFields()
    features = []
    for x, y, attrs in rows:
        f = QgsFeature(layer.fields())
        f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x, y)))
        f.setAttributes(attrs)
        features.append(f)
    layer.dataProvider().addFeatures(features)
    return layer


def write(layer, table, first):
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    options.layerName = table
    options.actionOnExistingFile = (
        QgsVectorFileWriter.CreateOrOverwriteFile if first else QgsVectorFileWriter.CreateOrOverwriteLayer
    )
    error = QgsVectorFileWriter.writeAsVectorFormatV3(layer, GPKG, QgsCoordinateTransformContext(), options)
    if error[0] != QgsVectorFileWriter.NoError:
        raise SystemExit("writing %s failed: %s" % (table, error))


assets = [
    (144.96 + i * 0.001, -37.81, ["asset %d" % i, PEOPLE[i % 2], "open", "secret %d" % i]) for i in range(8)
]
restricted = [(144.97, -37.82, ["restricted %d" % i, PEOPLE[0], "open", "x"]) for i in range(2)]
random.seed(1)
big = [
    (
        144.5 + random.random(),
        -38.2 + random.random(),
        ["big %d" % i, "person%03d@example.org" % (i % 100), "open", ""],
    )
    for i in range(50000)
]
big[0][2][1] = PEOPLE[0]

write(memory_layer("assets", assets), "assets", True)
write(memory_layer("restricted", restricted), "restricted", False)
write(memory_layer("big", big), "big", False)


def project(path, layers):
    p = QgsProject()
    p.setCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
    ids = []
    for name, table in layers:
        layer = QgsVectorLayer("%s|layername=%s" % (GPKG, table), name, "ogr")
        if not layer.isValid():
            raise SystemExit("layer %s is not valid" % name)
        p.addMapLayer(layer)
        ids.append(layer.id())
    p.writeEntry("WMSServiceCapabilities", "/", True)
    p.writeEntry("WMSServiceTitle", "/", "Castlegate spike")
    p.writeEntry("WMSExtent", "/", ["144.4", "-38.3", "145.6", "-37.7"])
    p.writeEntry("WMSCrsList", "/", ["EPSG:4326", "EPSG:3857"])
    p.writeEntry("WFSLayers", "/", ids)
    for op in ("Insert", "Update", "Delete"):
        p.writeEntry("WFSTLayers", op, ids)
    for layer_id in ids:
        p.writeEntry("WFSLayersPrecision", "/" + layer_id, 8)
    if not p.write(path):
        raise SystemExit("writing %s failed" % path)
    print("wrote", path, len(ids), "layers")


project(os.path.join(OUT, "castlegate.qgs"), [("assets", "assets"), ("restricted", "restricted"), ("big", "big")])
project(os.path.join(OUT, "castlegate-100.qgs"), [("layer_%03d" % i, "assets") for i in range(100)])
app.exitQgis()
