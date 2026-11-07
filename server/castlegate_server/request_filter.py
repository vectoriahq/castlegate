"""The request filter in front of every service.

onRequestReady runs before QGIS Server opens the project. It refuses a service
outside the allowlist and a request with no valid sign-in, and reads a WFS
Transaction's body. A refusal there stops the service.

onProjectReady runs once QGIS Server has opened the project and before the
service. It reads that project's rules and checks the transaction against
them, and the request's own filters and sorts (request_expressions.py). A
service exception set there does not stop the service (QgsServer checks for
one only before opening the project), so what it refuses is refused through
the access answers instead: for a transaction every edit permission answers
no, and for a filter or sort every answer is no, for the rest of the request.
QGIS Server then answers with its own refusal.

onResponseComplete adds WWW-Authenticate to a 401 and writes one audit line per
action of a transaction.

Each method catches everything and refuses: an exception escaping a filter
method ends the QGIS Server process.
"""

import json
import traceback

from qgis.core import Qgis, QgsProject
from qgis.server import QgsServerException, QgsServerFilter

from . import request_expressions, transaction
from .core.tokens import TokenError, verify
from .names import type_name_map
from .state import RequestState, log

# The allowlist. A service QGIS Server adds later is refused until the plugin learns it.
SERVICES = frozenset({"WMS", "WFS", "CASTLEGATE"})


class RequestFilter(QgsServerFilter):
    def __init__(self, iface, plugin):
        super().__init__(iface)
        self.plugin = plugin

    def _handler(self):
        return self.serverInterface().requestHandler()

    def _refuse(self, message, code=403):
        if code == 401:
            self.plugin.state.challenge = True
        self._handler().setServiceException(QgsServerException("Castlegate: " + message, code))

    # ---- before the project opens ----------------------------------------------------

    def onRequestReady(self):
        self.plugin.state = state = RequestState()
        try:
            handler = self._handler()
            params = {str(k).upper(): str(v) for k, v in handler.parameterMap().items()}
            state.params = params
            state.service = params.get("SERVICE", "").upper()
            if state.service not in SERVICES:
                self._refuse("this service is not available on this server")
                return True
            try:
                state.person = verify(handler.requestHeader("Authorization"), self.plugin.settings, self.plugin.keys)
            except TokenError as e:
                state.sign_in_error = str(e)
            if state.person is None:
                if state.service == "CASTLEGATE":
                    return True  # the status service answers 401 itself, with the reason
                self._refuse("sign-in required: " + state.sign_in_error, 401)
                return True
            state.body = body = bytes(handler.data() or b"")
            if state.service == "WFS":
                if params.get("REQUEST", "").lower() == "transaction" or (body and transaction.is_transaction(body)):
                    try:
                        state.actions = transaction.read(body)
                    except transaction.TransactionError as e:
                        self._refuse(str(e))
        except Exception:  # noqa: BLE001 - see the module docstring
            log("request filter failed, refusing: " + traceback.format_exc(), Qgis.MessageLevel.Critical)
            state.person = None
            try:
                self._refuse("the request was refused")
            except Exception:  # noqa: BLE001
                pass
        return True

    # ---- once the project is open ------------------------------------------------------

    def onProjectReady(self):
        state = self.plugin.state
        if state.person is None:
            # Only the status service lets a request with no sign-in this far, and it
            # answers 401 without the rules, so they are not read for it.
            return True
        try:
            project = QgsProject.instance()
            if project is None or not project.fileName():
                project = None
            state.project_rules = self.plugin.rules_cache.get(project)
            if project is not None:
                state.type_names = type_name_map(project)
            rules = state.project_rules
            if state.service != "CASTLEGATE" and rules is not None and rules.loaded:
                from qgis.core import QgsExpression

                hidden = rules.rules.hidden_anywhere(state.person, QgsExpression.quotedString)
                state.read_refusal = request_expressions.refusal(
                    state.service, state.params, state.body, hidden, request_expressions.qgis_check_expression
                )
                if state.read_refusal:
                    log("refused a request for %s: %s" % (state.person.id, state.read_refusal), Qgis.MessageLevel.Warning)
            if state.actions:
                state.edit_refusal = self._check_actions(state)
                if state.edit_refusal:
                    log("refused a transaction for %s: %s" % (state.person.id, state.edit_refusal), Qgis.MessageLevel.Warning)
        except Exception:  # noqa: BLE001 - see the module docstring
            log("onProjectReady failed, refusing: " + traceback.format_exc(), Qgis.MessageLevel.Critical)
            state.project_rules = None
            state.edit_refusal = state.read_refusal = "the request was refused"
        return True

    def _check_actions(self, state):
        """Why the transaction is refused, or "" when the rules allow every action in it."""
        from qgis.core import QgsExpression

        rules = state.project_rules
        if rules is None or not rules.loaded:
            return "the rules could not be read"
        for action in state.actions:
            layer = state.type_names.get(action.layer)
            if not layer:
                return "the transaction names %s, which is not one layer of this project" % action.layer
            answer = rules.rules.answer(state.person, layer, QgsExpression.quotedString)
            if action.kind == "insert":
                needed = ["add"]
            elif action.kind == "delete":
                needed = ["delete"]
            else:
                needed = (["change"] if action.fields else []) + (["move"] if action.geometry else [])
                if not needed:
                    return "an Update on %s names no field" % layer
            for right in needed:
                if right not in answer.rights:
                    return "you may not %s features on layer %s" % (right, layer)
            for right in ("add", "change"):
                if right in needed:
                    blocked = sorted(action.fields & answer.blocked_on(right))
                    if blocked:
                        return "you may not set %s on layer %s" % (", ".join(blocked), layer)
        return ""

    # ---- once the response is written --------------------------------------------------

    def onResponseComplete(self):
        state = self.plugin.state
        try:
            handler = self._handler()
            if state.challenge:
                handler.setResponseHeader("WWW-Authenticate", "Bearer")
            if state.actions and state.person is not None:
                self._audit(state, bytes(handler.body() or b""))
        except Exception:  # noqa: BLE001 - see the module docstring
            log("onResponseComplete failed: " + traceback.format_exc(), Qgis.MessageLevel.Critical)
        finally:
            self.plugin.state = RequestState()
        return True

    def _audit(self, state, body):
        text = body[:8000].decode("utf-8", "replace")
        if state.edit_refusal or state.read_refusal:
            outcome = "refused (%s)" % (state.edit_refusal or state.read_refusal)
        elif "ServiceException" in text or "FAILED" in text:
            outcome = "refused by QGIS Server"
        else:
            outcome = "done"
        for action in state.actions:
            # Every value that comes from the token or the request is written as a
            # JSON string, so none can pose as another field of the line.
            log(
                "audit id=%s email=%s layer=%s action=%s features=%s fields=%s shape=%s outcome=%s"
                % (
                    json.dumps(state.person.id),
                    json.dumps(state.person.email),
                    json.dumps(state.type_names.get(action.layer) or action.layer),
                    action.kind,
                    action.features or "by-filter",
                    json.dumps(sorted(action.fields)),
                    "yes" if action.geometry else "no",
                    json.dumps(outcome),
                ),
                Qgis.MessageLevel.Warning,
            )
