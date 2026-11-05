"""What the plugin knows about the request in hand.

QGIS Server handles one request at a time in each worker process, so the
plugin keeps one RequestState per process, cleared when a request starts and
again when it ends.
"""

from dataclasses import dataclass, field

TAG = "Castlegate"


@dataclass
class RequestState:
    # The signed-in Person, or None.
    person: object = None
    # Why the sign-in was refused, when it was.
    sign_in_error: str = ""
    # The service the request names, upper case.
    service: str = ""
    # ProjectRules for the project QGIS Server opened, set in onProjectReady.
    project_rules: object = None
    # The actions of a WFS Transaction, read before the project opened.
    actions: list = field(default_factory=list)
    # When the plugin refuses the transaction's edits: why. Every edit permission then answers no.
    edit_refusal: str = ""
    # Answer a 401 with WWW-Authenticate: a header set before a refusal is lost.
    challenge: bool = False
    # A WFS type name to the published name the rules use, for the opened project.
    type_names: dict = field(default_factory=dict)


def log(message, level=None):
    from qgis.core import Qgis, QgsMessageLog

    QgsMessageLog.logMessage(message, TAG, level if level is not None else Qgis.MessageLevel.Info)
