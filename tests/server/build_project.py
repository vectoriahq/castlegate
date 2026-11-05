"""Build the plugin tests' data and QGIS projects. Run inside the QGIS Server image (see run.mjs).

Writes into /io/data:
- castlegate.gpkg: the layers assets, restricted, big and site_notes, and two rules
  tables, castlegate_rules and castlegate_rules_b;
- castlegate.qgs: the layers and castlegate_rules, named in the castlegate_rules_layer
  project variable;
- castlegate-b.qgs: the same layers with castlegate_rules_b;
- castlegate-norules.qgs: the layers and no project variable;
- castlegate-missing.qgs: a project variable naming a layer the project lacks.

With CASTLEGATE_MSSQL set to a SQL Server connection, the layers and rules tables are
copied into SQL Server tables of the same names, which must exist, and the projects
name those instead.

Runs on QGIS 3.44 and 4.x.
"""

import json
import os
import random

from qgis.core import (
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransformContext,
    QgsExpressionContextUtils,
    QgsFeature,
    QgsField,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsVectorFileWriter,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QMetaType

OUT = os.environ.get("CASTLEGATE_OUT", "/io/data")
MSSQL = os.environ.get("CASTLEGATE_MSSQL")
GPKG = os.path.join(OUT, "castlegate.gpkg")
RULES = json.loads(os.environ["CASTLEGATE_TEST_RULES"])
PEOPLE = ["alice@example.org", "bob@example.org"]
TEXT = QMetaType.Type.QString

app = QgsApplication([], False)
app.initQgis()

ACTION = QgsVectorFileWriter.ActionOnExistingFile
first_write = [True]


def write(layer, table):
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    options.layerName = table
    options.actionOnExistingFile = ACTION.CreateOrOverwriteFile if first_write[0] else ACTION.CreateOrOverwriteLayer
    first_write[0] = False
    error = QgsVectorFileWriter.writeAsVectorFormatV3(layer, GPKG, QgsCoordinateTransformContext(), options)
    if error[0] != QgsVectorFileWriter.WriterError.NoError:
        raise SystemExit("writing %s failed: %s" % (table, error))


def point_layer(rows):
    layer = QgsVectorLayer("Point?crs=EPSG:4326", "tmp", "memory")
    layer.dataProvider().addAttributes([QgsField(n, TEXT) for n in ("name", "owner", "status", "secret")])
    layer.updateFields()
    features = []
    for x, y, attrs in rows:
        f = QgsFeature(layer.fields())
        f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x, y)))
        f.setAttributes(attrs)
        features.append(f)
    layer.dataProvider().addFeatures(features)
    return layer


def rules_layer(rows):
    layer = QgsVectorLayer("None", "tmp", "memory")
    columns = [
        "kind", "who", "layer", "can_see", "can_add", "can_change", "can_move", "can_delete",
        "see_filter", "edit_filter", "hidden_fields", "readonly_fields",
    ]
    layer.dataProvider().addAttributes([QgsField(c, TEXT) for c in columns])
    layer.updateFields()
    features = []
    for row in rows:
        f = QgsFeature(layer.fields())
        f.setAttributes([None if row.get(c) is None else str(row.get(c)) for c in columns])
        features.append(f)
    layer.dataProvider().addFeatures(features)
    return layer


assets = [(144.96 + i * 0.001, -37.81, ["asset %d" % i, PEOPLE[i % 2], "open", "secret %d" % i]) for i in range(8)]
restricted = [(144.97, -37.82, ["restricted %d" % i, PEOPLE[0], "open", "x"]) for i in range(2)]
random.seed(1)
big = [
    (144.5 + random.random(), -38.2 + random.random(), ["big %d" % i, "person%03d@example.org" % (i % 100), "open", ""])
    for i in range(5000)
]
big[0][2][1] = PEOPLE[0]
notes = [(144.99, -37.83, ["note %d" % i, PEOPLE[0], "open", ""]) for i in range(2)]

DATA = ["assets", "restricted", "big", "site_notes"]
write(point_layer(assets), "assets")
write(point_layer(restricted), "restricted")
write(point_layer(big), "big")
write(point_layer(notes), "site_notes")
write(rules_layer(RULES["a"]), "castlegate_rules")
write(rules_layer(RULES["b"]), "castlegate_rules_b")


def mssql_source(table, spatial=True):
    geometry = " srid=4326 type=Point" if spatial else ""
    column = " (geom)" if spatial else ""
    return "%s%s key='fid' estimatedmetadata=true table=\"dbo\".\"%s\"%s" % (MSSQL, geometry, table, column)


def copy_to_mssql(table, spatial):
    source = QgsVectorLayer("%s|layername=%s" % (GPKG, table), table, "ogr")
    target = QgsVectorLayer(mssql_source(table, spatial), table, "mssql")
    if not target.isValid():
        raise SystemExit("SQL Server table %s is not valid: %s" % (table, target.dataProvider().error().message()))
    names = [f.name() for f in source.fields() if f.name() != "fid"]
    features = []
    for f in source.getFeatures():
        g = QgsFeature(target.fields())
        if spatial:
            g.setGeometry(f.geometry())
        for name in names:
            g[name] = f[name]
        features.append(g)
    ok, _ = target.dataProvider().addFeatures(features)
    if not ok:
        raise SystemExit("copying %s to SQL Server failed: %s" % (table, target.dataProvider().errors()))
    print("copied", len(features), "features to SQL Server table", table)


if MSSQL:
    for table in DATA:
        copy_to_mssql(table, True)
    for table in ("castlegate_rules", "castlegate_rules_b"):
        copy_to_mssql(table, False)


def layer_for(table, name, spatial=True):
    if MSSQL:
        return QgsVectorLayer(mssql_source(table, spatial), name, "mssql")
    return QgsVectorLayer("%s|layername=%s" % (GPKG, table), name, "ogr")


def project(path, rules_table=None, variable=None):
    p = QgsProject()
    p.setCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
    ids = []
    for table in DATA:
        # "site notes" is published with a space, so its WFS type name is site_notes.
        layer = layer_for(table, "site notes" if table == "site_notes" else table)
        if not layer.isValid():
            raise SystemExit("layer %s is not valid" % table)
        p.addMapLayer(layer)
        ids.append(layer.id())
    if rules_table:
        rules = layer_for(rules_table, rules_table, spatial=False)
        if not rules.isValid():
            raise SystemExit("rules layer %s is not valid" % rules_table)
        p.addMapLayer(rules)
        # Published in every service on purpose: the plugin must refuse it whatever the settings say.
        ids.append(rules.id())
    if variable:
        QgsExpressionContextUtils.setProjectVariable(p, "castlegate_rules_layer", variable)
    p.writeEntry("WMSServiceCapabilities", "/", True)
    p.writeEntry("WMSServiceTitle", "/", "Castlegate tests")
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


project(os.path.join(OUT, "castlegate.qgs"), "castlegate_rules", "castlegate_rules")
project(os.path.join(OUT, "castlegate-b.qgs"), "castlegate_rules_b", "castlegate_rules_b")
project(os.path.join(OUT, "castlegate-norules.qgs"))
project(os.path.join(OUT, "castlegate-missing.qgs"), None, "no_such_layer")
app.exitQgis()
