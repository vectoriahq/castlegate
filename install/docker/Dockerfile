# QGIS Server with Castlegate Server built in, so an upgrade of this image carries the plugin
# along rather than leaving it behind. The build context is this folder with the plugin folder
# from the release zip, castlegate_server/, beside this file (README.md).
ARG BASE=qgis/qgis-server:ltr
FROM ${BASE}
# The image loads server plugins from QGIS_PLUGINPATH, which it sets to /io/plugins.
COPY castlegate_server /io/plugins/castlegate_server
COPY castlegate-cors.conf /etc/nginx/qgis.d/castlegate-cors.conf
# The status request every profile ends with, against the project named in CASTLEGATE_CHECK_MAP.
HEALTHCHECK --interval=60s --timeout=15s --start-period=30s \
  CMD python3 /io/plugins/castlegate_server/tools/check.py http://localhost/ows/ --map "$CASTLEGATE_CHECK_MAP" || exit 1
