# The QGIS Server image with Microsoft's ODBC Driver 18 for SQL Server and Qt's ODBC plugin,
# which QGIS's SQL Server provider connects through. Building it accepts Microsoft's licence
# for the driver (https://aka.ms/odbc18eula).
FROM qgis/qgis-server:ltr
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates libqt5sql5-odbc \
 && curl -fsSL https://packages.microsoft.com/config/ubuntu/26.04/packages-microsoft-prod.deb -o /tmp/packages-microsoft-prod.deb \
 && dpkg -i /tmp/packages-microsoft-prod.deb && rm /tmp/packages-microsoft-prod.deb \
 && apt-get update \
 && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
 && rm -rf /var/lib/apt/lists/*
# The data source the test projects name: service='DSN=castlegate'. The SQL Server container's
# certificate is self-signed, so the driver is told to trust it.
RUN printf '[castlegate]\nDriver=ODBC Driver 18 for SQL Server\nTrustServerCertificate=yes\n' > /etc/odbc.ini
