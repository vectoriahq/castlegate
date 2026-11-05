"""The names a layer is published under, as QGIS Server itself works them out."""

from qgis.server import QgsServerProjectUtils


def published_name(layer, project):
    """The name WMS publishes the layer under, which a LAYERS parameter and a rules row name."""
    if QgsServerProjectUtils.wmsUseLayerIds(project):
        return layer.id()
    return layer.serverProperties().shortName() or layer.name()


def wfs_type_name(layer):
    """The name WFS publishes the layer under, which a transaction's typeName names."""
    properties = layer.serverProperties()
    if hasattr(properties, "wfsTypeName"):
        return properties.wfsTypeName()
    # QGIS 3.44's layerTypeName (src/server/services/wfs/qgswfsutils.cpp).
    return (properties.shortName() or layer.name()).replace(" ", "_").replace(":", "-")


def type_name_map(project):
    """Each vector layer's WFS type name to its published name, or to None where two layers share a type name."""
    from qgis.core import QgsVectorLayer

    found = {}
    for layer in project.mapLayers().values():
        if isinstance(layer, QgsVectorLayer):
            name = wfs_type_name(layer)
            found[name] = None if name in found else published_name(layer, project)
    return found
