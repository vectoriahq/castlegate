"""Castlegate spike: an access-control filter, a request filter and a status service.

Spike switches, read from the environment, exist only to show stock behaviour:
  CASTLEGATE_CACHE_KEY   constant (default) | empty
  CASTLEGATE_FIELD_CHECK on (default) | off
  CASTLEGATE_RAISE       name of one filter method that raises, uncaught
"""

import json
import os
import time
import traceback
import xml.etree.ElementTree as ET

from qgis.core import (
    Qgis,
    QgsExpression,
    QgsExpressionContext,
    QgsExpressionContextUtils,
    QgsMessageLog,
)
from qgis.server import (
    QgsAccessControlFilter,
    QgsServerException,
    QgsServerFilter,
    QgsService,
)

from .rules import Rules
from .token import TokenError, Verifier

VERSION = "0.0.1-spike"
SERVICES = {"WMS", "WFS", "CASTLEGATE"}
TAG = "Castlegate"


def log(message, level=Qgis.Info):
    QgsMessageLog.logMessage(message, TAG, level)


def published_name(layer):
    try:
        short = layer.serverProperties().shortName()
    except AttributeError:
        short = layer.shortName()
    return short or layer.name()


def local(tag):
    return tag.rsplit("}", 1)[-1].rsplit(":", 1)[-1]


class State:
    """What the current request is allowed. One request at a time per process."""

    def __init__(self):
        self.person = None
        self.error = None
        self.audit = []
        self.challenge = False


class RequestFilter(QgsServerFilter):
    def __init__(self, iface, plugin):
        super().__init__(iface)
        self.plugin = plugin

    def _refuse(self, message, code=403):
        handler = self.serverInterface().requestHandler()
        if code == 401:
            # A header set before the exception is lost; onResponseComplete adds it.
            self.plugin.state.challenge = True
        handler.setServiceException(QgsServerException(message, code))

    def onRequestReady(self):
        state = self.plugin.state
        state.person, state.error, state.audit, state.challenge = None, None, [], False
        if self.plugin.raise_in == "onRequestReady":
            raise RuntimeError("spike: raised in onRequestReady")
        try:
            handler = self.serverInterface().requestHandler()
            params = {k.upper(): v for k, v in handler.parameterMap().items()}
            service = params.get("SERVICE", "").upper()
            if service not in SERVICES:
                self._refuse("Castlegate: this service is not available")
                return True
            try:
                state.person = self.plugin.verifier.verify(handler.requestHeader("Authorization"))
            except TokenError as e:
                state.error = str(e)
            if state.person is None:
                if service == "CASTLEGATE":
                    return True  # the status service answers 401 itself
                self._refuse("Castlegate: sign-in required (%s)" % state.error, 401)
                return True
            if service == "WFS" and params.get("REQUEST", "").lower() == "transaction":
                self._check_transaction(handler)
        except Exception:
            log("request filter failed, refusing: " + traceback.format_exc(), Qgis.Critical)
            state.person = None
            try:
                self._refuse("Castlegate: request refused")
            except Exception:
                pass
        return True

    def _check_transaction(self, handler):
        body = bytes(handler.data())
        if not body:
            self._refuse("Castlegate: a transaction must be sent as a POST body")
            return
        if b"<!DOCTYPE" in body or b"<!ENTITY" in body:
            self._refuse("Castlegate: document type declarations are not accepted")
            return
        root = ET.fromstring(body)
        person = self.plugin.state.person
        for el in root.iter():
            tag = local(el.tag)
            if tag == "Update":
                layer = local(el.get("typeName") or el.get("typeNames") or "")
                names = []
                for prop in el:
                    if local(prop.tag) != "Property":
                        continue
                    for child in prop:
                        if local(child.tag) in ("Name", "ValueReference"):
                            names.append(local((child.text or "").strip()))
                self._check_fields(person, layer, "update", names)
            elif tag == "Insert":
                for feature in el:
                    self._check_fields(person, local(feature.tag), "insert", [local(c.tag) for c in feature])
            elif tag == "Delete":
                layer = local(el.get("typeName") or el.get("typeNames") or "")
                self.plugin.state.audit.append(("delete", layer, []))

    def _check_fields(self, person, layer, action, names):
        self.plugin.state.audit.append((action, layer, names))
        if not self.plugin.field_check:
            return
        answer = self.plugin.rules.answer(person, layer)
        if not answer["any"]:
            self._refuse("Castlegate: no rights on layer %s" % layer)
            return
        if action == "update" and "geometry" in names and "move" not in answer["may"]:
            self._refuse("Castlegate: you may not move features on layer %s" % layer)
            return
        blocked = sorted(set(names) & answer["blocked"])
        if blocked:
            self._refuse("Castlegate: you may not change %s on layer %s" % (", ".join(blocked), layer))

    def onResponseComplete(self):
        try:
            state = self.plugin.state
            if state.challenge:
                self.serverInterface().requestHandler().setResponseHeader("WWW-Authenticate", "Bearer")
            if state.audit and state.person:
                body = bytes(self.serverInterface().requestHandler().body())[:4000].decode("utf-8", "replace")
                outcome = "refused" if "ServiceException" in body or "FAILED" in body else "done"
                for action, layer, names in state.audit:
                    log(
                        "audit oid=%s email=%s layer=%s action=%s fields=%s outcome=%s"
                        % (state.person["oid"], state.person["email"], layer, action, ",".join(names), outcome)
                    )
        except Exception:
            log("audit failed: " + traceback.format_exc(), Qgis.Critical)
        return True


class AccessFilter(QgsAccessControlFilter):
    def __init__(self, iface, plugin):
        super().__init__(iface)
        self.plugin = plugin

    def _answer(self, layer):
        person = self.plugin.state.person
        if person is None:
            return None
        return self.plugin.rules.answer(person, published_name(layer))

    def _guard(self, name, default, fn):
        if self.plugin.raise_in == name:
            raise RuntimeError("spike: raised in " + name)
        try:
            return fn()
        except Exception:
            log("%s failed, answering no: %s" % (name, traceback.format_exc()), Qgis.Critical)
            return default

    def layerPermissions(self, layer):
        def fn():
            rights = QgsAccessControlFilter.LayerPermissions()
            rights.canRead = rights.canInsert = rights.canUpdate = rights.canDelete = False
            a = self._answer(layer)
            if a and a["any"]:
                may = a["may"]
                rights.canRead = "read" in may
                rights.canInsert = "insert" in may
                rights.canUpdate = "update" in may or "move" in may
                rights.canDelete = "delete" in may
            return rights

        none = QgsAccessControlFilter.LayerPermissions()
        none.canRead = none.canInsert = none.canUpdate = none.canDelete = False
        return self._guard("layerPermissions", none, fn)

    def layerFilterExpression(self, layer):
        def fn():
            a = self._answer(layer)
            if not a or not a["any"]:
                return "FALSE"
            return a["view"] or ""

        return self._guard("layerFilterExpression", "FALSE", fn)

    def layerFilterSubsetString(self, layer):
        return ""

    def authorizedLayerAttributes(self, layer, attributes):
        def fn():
            a = self._answer(layer)
            if not a or not a["any"]:
                return []
            return [f for f in attributes if f not in a["hidden"]]

        return self._guard("authorizedLayerAttributes", [], fn)

    def allowToEdit(self, layer, feature):
        def fn():
            a = self._answer(layer)
            if not a or not a["any"]:
                return False
            if a["edit"] is None:
                return True
            context = QgsExpressionContext(QgsExpressionContextUtils.globalProjectLayerScopes(layer))
            context.setFeature(feature)
            expression = QgsExpression(a["edit"])
            expression.prepare(context)
            value = expression.evaluate(context)
            if expression.hasEvalError():
                log("edit expression error: " + expression.evalErrorString(), Qgis.Warning)
                return False
            return bool(value)

        return self._guard("allowToEdit", False, fn)

    def cacheKey(self):
        return "" if self.plugin.cache_key_mode == "empty" else "castlegate"


class StatusService(QgsService):
    def __init__(self, plugin):
        super().__init__()
        self.plugin = plugin

    def name(self):
        return "CASTLEGATE"

    def version(self):
        return "1.0.0"

    def executeRequest(self, request, response, project):
        state = self.plugin.state
        if state.person is None:
            response.setStatusCode(401)
            response.setHeader("WWW-Authenticate", "Bearer")
            response.setHeader("Content-Type", "application/json")
            response.write(json.dumps({"signedIn": False, "reason": state.error}))
            return
        try:
            rules_loaded = os.path.exists(self.plugin.rules.path)
            stamp = os.stat(self.plugin.rules.path).st_mtime if rules_loaded else None
        except Exception:
            rules_loaded, stamp = False, None
        response.setHeader("Content-Type", "application/json")
        response.write(
            json.dumps(
                {
                    "signedIn": True,
                    "oid": state.person["oid"],
                    "email": state.person["email"],
                    "groups": state.person["groups"],
                    "rulesLoaded": rules_loaded,
                    "rulesChanged": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stamp)) if stamp else None,
                    "version": VERSION,
                    "pid": os.getpid(),
                }
            )
        )


class CastlegateSpike:
    def __init__(self, iface):
        self.iface = iface
        self.state = State()
        self.verifier = Verifier()
        self.rules = Rules()
        self.cache_key_mode = os.environ.get("CASTLEGATE_CACHE_KEY", "constant")
        self.field_check = os.environ.get("CASTLEGATE_FIELD_CHECK", "on") != "off"
        self.raise_in = os.environ.get("CASTLEGATE_RAISE", "")
        self.request_filter = RequestFilter(iface, self)
        self.access_filter = AccessFilter(iface, self)
        self.status_service = StatusService(self)
        iface.registerFilter(self.request_filter, 10)
        iface.registerAccessControl(self.access_filter, 10)
        iface.serviceRegistry().registerService(self.status_service)
        log(
            "loaded %s: cache key %s, field check %s, raise in %r"
            % (VERSION, self.cache_key_mode, self.field_check, self.raise_in)
        )
