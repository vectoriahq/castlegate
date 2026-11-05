# The status check

`check.py` asks QGIS Server for Castlegate's status answer and says whether the plugin is loaded. It needs only Python's standard library, so it runs with the Python that comes with QGIS Server, and it ships inside the plugin folder as `castlegate_server/tools/check.py`.

```bash
python check.py https://maps.example.org/groundwork/qgisserver --map C:/Projects/assets.qgs
```

It prints one line per check and exits 0 when every check passes, 1 when one fails, and 2 when the server cannot be reached.

- **Always:** a request with no sign-in is refused with 401 and `WWW-Authenticate: Bearer`. If QGIS Server does not know the status service instead, the plugin is not loaded and the server answers everyone.
- **With `CASTLEGATE_TOKEN` set** to a bearer token in the environment: the token is accepted, and the project's rules load. Rows left out of the rules are listed.
- **With `--origin`**, such as `--origin https://app.groundwork.do`: the browser's preflight request from that origin is answered, and a refusal sent to it can be read by the browser.

Add the first form to your monitoring: if QGIS Server ever starts without the plugin, after an upgrade that breaks it for example, it answers everyone until someone notices.
