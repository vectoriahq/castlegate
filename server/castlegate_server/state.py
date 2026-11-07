"""What the plugin knows about the request in hand.

QGIS Server handles one request at a time in each worker process, so the
plugin keeps one RequestState per process, cleared when a request starts and
again when it ends.
"""

import re
from dataclasses import dataclass, field

TAG = "Castlegate"

# C0 and C1 controls, DEL, and the Unicode line and paragraph separators.
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")


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
    # The request's parameters, names in upper case, and its body.
    params: dict = field(default_factory=dict)
    body: bytes = b""
    # When the plugin refuses the request's own filters or sorts: why. Every answer is then no.
    read_refusal: str = ""


def one_line(text):
    """The text with every line break and other control character written as an escape.

    Values in the plugin's log lines can come from the request, so a line break in
    one could start a second, made-up line, such as a forged audit line.
    """
    return _CONTROL.sub(lambda m: "\\u%04x" % ord(m.group()), str(text))


def log(message, level=None):
    """Write one line to QGIS Server's log, whatever the message holds."""
    from qgis.core import Qgis, QgsMessageLog

    QgsMessageLog.logMessage(one_line(message), TAG, level if level is not None else Qgis.MessageLevel.Info)
