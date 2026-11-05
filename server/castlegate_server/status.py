"""The status answer: SERVICE=CASTLEGATE&REQUEST=Status.

Signed in, it says who the request is signed in as, whether the project's rules
loaded and when they were read, and the plugin's version. With no valid
sign-in it answers 401 with WWW-Authenticate: Bearer and the reason, so a client
can tell "the plugin is here, sign in" from "the plugin is missing", which QGIS
Server answers as an unknown service.
"""

import json
import time
import traceback

from qgis.core import Qgis
from qgis.server import QgsService

from .state import log

VERSION = "0.1.0"


def _when(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t)) if t else None


class StatusService(QgsService):
    def __init__(self, plugin):
        super().__init__()
        self.plugin = plugin

    def name(self):
        return "CASTLEGATE"

    def version(self):
        return "1.0.0"

    def executeRequest(self, request, response, project):
        try:
            response.setHeader("Content-Type", "application/json")
            response.write(json.dumps(self._body(response)))
        except Exception:  # noqa: BLE001 - an exception escaping here ends the QGIS Server process
            log("status failed: " + traceback.format_exc(), Qgis.MessageLevel.Critical)
            try:
                response.setStatusCode(500)
            except Exception:  # noqa: BLE001
                pass

    def _body(self, response):
        state = self.plugin.state
        if state.person is None:
            response.setStatusCode(401)
            response.setHeader("WWW-Authenticate", "Bearer")
            return {"signedIn": False, "reason": state.sign_in_error, "version": VERSION}
        rules = state.project_rules
        return {
            "signedIn": True,
            "id": state.person.id,
            "email": state.person.email,
            "groups": sorted(state.person.groups),
            "rules": {
                "loaded": bool(rules and rules.loaded),
                "layer": rules.layer_name if rules else None,
                "rows": rules.rows if rules else 0,
                "problems": list(rules.problems) if rules else [],
                "error": (rules.error or None) if rules else "no project was opened",
                "readAt": _when(rules.read_at) if rules else None,
            },
            "version": VERSION,
        }
