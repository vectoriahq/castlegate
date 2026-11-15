"""QGIS Server's access-control questions, answered from the rules.

Every method answers and changes nothing: QGIS Server keeps one copy of a
project for every request, so a change to a layer would reach the next person.
Every method catches everything and answers no, because an exception escaping
a filter method ends the QGIS Server process.
"""

import traceback

from qgis.core import Qgis, QgsExpression, QgsExpressionContext, QgsExpressionContextUtils
from qgis.server import QgsAccessControlFilter

from .core.rules import NOTHING
from .names import published_name
from .state import log

# A constant, non-empty key. QGIS Server switches its shared capabilities cache
# off for a request whose access filters return any non-empty key, and with an
# empty key the first person's capabilities would be served to everyone.
CACHE_KEY = "castlegate"

# Which edit filter each kind of transaction action is checked against.
_ACTION_RIGHTS = {"insert": ("insert",), "delete": ("delete",)}


def _no_permissions():
    rights = QgsAccessControlFilter.LayerPermissions()
    rights.canRead = rights.canInsert = rights.canUpdate = rights.canDelete = False
    return rights


class AccessFilter(QgsAccessControlFilter):
    def __init__(self, iface, plugin):
        super().__init__(iface)
        self.plugin = plugin

    def _answer(self, layer):
        """The person's Answer for this layer of the opened project, or NOTHING."""
        state = self.plugin.state
        rules = state.project_rules
        if state.person is None or rules is None or not rules.loaded or state.read_refusal:
            return NOTHING
        if layer.id() == rules.layer_id:
            return NOTHING  # the rules table is never served
        project = layer.project()
        if project is None or project.fileName() != rules.path:
            return NOTHING
        return rules.rules.answer(state.person, published_name(layer, project), QgsExpression.quotedString)

    def _guard(self, name, default, fn):
        try:
            return fn()
        except Exception:  # noqa: BLE001 - see the module docstring
            log("%s failed, answering no: %s" % (name, traceback.format_exc()), Qgis.MessageLevel.Critical)
            return default

    def layerPermissions(self, layer):
        def answer():
            rights = _no_permissions()
            a = self._answer(layer)
            rights.canRead = "read" in a.rights
            if not self.plugin.state.edit_refusal:
                rights.canInsert = "insert" in a.rights
                rights.canUpdate = "update_attributes" in a.rights or "update_geometry" in a.rights
                rights.canDelete = "delete" in a.rights
            return rights

        return self._guard("layerPermissions", _no_permissions(), answer)

    def layerFilterExpression(self, layer):
        def answer():
            expression = self._answer(layer).read_filter
            if expression and QgsExpression(expression).hasParserError():
                log("the read filter for %s does not parse; showing no features" % layer.name(), Qgis.MessageLevel.Warning)
                return "FALSE"
            return expression

        return self._guard("layerFilterExpression", "FALSE", answer)

    def layerFilterSubsetString(self, layer):
        # Empty: the expression filter is enough, and providers compile simple ones to SQL themselves.
        return ""

    def authorizedLayerAttributes(self, layer, attributes):
        def answer():
            a = self._answer(layer)
            if "read" not in a.rights:
                return []
            return [name for name in attributes if name not in a.hidden]

        return self._guard("authorizedLayerAttributes", [], answer)

    def allowToEdit(self, layer, feature):
        def answer():
            state = self.plugin.state
            if state.edit_refusal:
                return False
            a = self._answer(layer)
            rights = self._rights_for(layer)
            if not rights:
                return False
            context = QgsExpressionContext(QgsExpressionContextUtils.globalProjectLayerScopes(layer))
            context.setFeature(feature)
            for right in rights:
                if right not in a.rights:
                    return False
                text = a.edit_filter(right)
                if text == "":
                    continue
                expression = QgsExpression(text)
                if expression.hasParserError():
                    return False
                expression.prepare(context)
                value = expression.evaluate(context)
                if expression.hasEvalError() or not bool(value):
                    return False
            return True

        return self._guard("allowToEdit", False, answer)

    def _rights_for(self, layer):
        """The edit rights this request's actions on this layer use. QGIS does not say which action
        asks, so a feature must pass the edit filter of every kind of action the request makes on it."""
        state = self.plugin.state
        name = published_name(layer, layer.project())
        rights = set()
        for action in state.actions:
            if state.type_names.get(action.layer) != name:
                continue
            if action.kind == "update":
                if action.fields:
                    rights.add("update_attributes")
                if action.geometry:
                    rights.add("update_geometry")
            else:
                rights.update(_ACTION_RIGHTS[action.kind])
        return rights

    def cacheKey(self):
        return CACHE_KEY
