"""Castlegate Server: per-person access to QGIS Server layers, from a rules table and an OpenID Connect sign-in."""

import os

from qgis.core import Qgis

from .access_filter import AccessFilter
from .core.keys import KeySet
from .core.settings import Settings
from .project_rules import Cache
from .request_filter import RequestFilter
from .state import RequestState, log
from .status import VERSION, StatusService


class CastlegateServer:
    def __init__(self, iface):
        self.iface = iface
        self.state = RequestState()
        # Read from the environment once, when QGIS Server loads the plugin.
        self.settings = Settings.from_environment(os.environ)
        self.keys = KeySet(self.settings)
        self.rules_cache = Cache()
        self.request_filter = RequestFilter(iface, self)
        self.access_filter = AccessFilter(iface, self)
        self.status_service = StatusService(self)
        iface.registerFilter(self.request_filter, 10)
        iface.registerAccessControl(self.access_filter, 10)
        iface.serviceRegistry().registerService(self.status_service)
        if self.settings.missing:
            log(
                "loaded %s, but %s %s not set, so every sign-in is refused"
                % (VERSION, ", ".join(self.settings.missing), "is" if len(self.settings.missing) == 1 else "are"),
                Qgis.MessageLevel.Critical,
            )
        else:
            log("loaded %s for issuer %s" % (VERSION, self.settings.issuer), Qgis.MessageLevel.Warning)
