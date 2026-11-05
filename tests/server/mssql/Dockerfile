# The QGIS Server image under test with Microsoft's ODBC Driver 18 for SQL Server and Qt's ODBC
# plugin, which QGIS's SQL Server provider connects through: Qt 5's for QGIS 3, Qt 6's for QGIS 4.
# Building it accepts Microsoft's licence for the driver (https://aka.ms/odbc18eula).
ARG BASE=qgis/qgis-server:ltr
FROM ${BASE}
RUN apt-get update \
 && if dpkg -s libqt6sql6 >/dev/null 2>&1; then odbc=libqt6sql6-odbc; else odbc=libqt5sql5-odbc; fi \
 && apt-get install -y --no-install-recommends curl ca-certificates "$odbc" \
 && . /etc/os-release \
 && curl -fsSL "https://packages.microsoft.com/config/ubuntu/${VERSION_ID}/packages-microsoft-prod.deb" -o /tmp/packages-microsoft-prod.deb \
 && dpkg -i /tmp/packages-microsoft-prod.deb && rm /tmp/packages-microsoft-prod.deb \
 && apt-get update \
 && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
 && rm -rf /var/lib/apt/lists/*
# The data source the test projects name: service='DSN=castlegate'. The SQL Server container's
# certificate is self-signed, so the driver is told to trust it.
RUN printf '[castlegate]\nDriver=ODBC Driver 18 for SQL Server\nTrustServerCertificate=yes\n' > /etc/odbc.ini
