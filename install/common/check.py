"""Check that Castlegate Server is loaded, and optionally that a sign-in works and the rules load.

  python check.py <QGIS Server address> --map <project path> [--origin <browser origin>]

With the CASTLEGATE_TOKEN environment variable set to a bearer token, it also
checks that the token is accepted and that the project's rules load. The token
is read from the environment so it stays out of the command line and shell
history.

It needs only Python's standard library, so it runs with the Python QGIS Server
already has. It prints one line per check and exits 0 when every check passes,
1 when one fails, and 2 when the server cannot be reached.

Examples:
  python check.py https://maps.example.org/groundwork/qgisserver --map C:/Projects/assets.qgs
  python check.py http://localhost:8010/ows/ --map /io/data/project.qgs --origin https://app.groundwork.do
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_opener = urllib.request.build_opener(_NoRedirects)


class Unreachable(Exception):
    pass


def request(url, method="GET", headers=None):
    """(status, headers, body text) for one request; HTTP errors are answers, not failures.

    Any failure to get an answer at all, such as a refused connection, a timeout or a
    reset, raises Unreachable.
    """
    r = urllib.request.Request(url, method=method, headers=headers or {})
    try:
        with _opener.open(r, timeout=30) as response:
            return response.status, response.headers, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read().decode("utf-8", "replace")
    except (OSError, ValueError) as e:  # URLError, timeouts and resets are OSErrors; a bad URL a ValueError
        raise Unreachable(str(e))


def status_url(base, project):
    query = urllib.parse.urlencode({"SERVICE": "CASTLEGATE", "REQUEST": "Status", "MAP": project})
    return base + ("&" if "?" in base else "?") + query


def main(argv=None):
    parser = argparse.ArgumentParser(description="Check that Castlegate Server is loaded.")
    parser.add_argument("address", help="the QGIS Server address, as Groundwork is given it")
    parser.add_argument("--map", required=True, help="a project path the server opens, as the MAP parameter takes it")
    parser.add_argument("--origin", help="a browser origin that must be allowed, such as https://app.groundwork.do")
    args = parser.parse_args(argv)
    url = status_url(args.address, args.map)
    failed = []

    def report(ok, message):
        print(("PASS  " if ok else "FAIL  ") + message)
        if not ok:
            failed.append(message)

    try:
        return _checks(args, url, failed, report)
    except Unreachable as e:
        print("FAIL  could not reach %s: %s" % (args.address, e))
        return 2


def _checks(args, url, failed, report):
    code, headers, body = request(url)
    challenge = headers.get("WWW-Authenticate", "")
    if code == 401 and challenge.lower().startswith("bearer"):
        report(True, "Castlegate is loaded: a request with no sign-in is refused with 401 and WWW-Authenticate: Bearer")
    elif "Service unknown" in body or "CASTLEGATE" in body:
        report(False, "Castlegate is NOT loaded: QGIS Server does not know its status service, so this server answers everyone")
    else:
        report(False, "unexpected answer to the status request: %d %s" % (code, body[:200].replace("\n", " ")))

    token = os.environ.get("CASTLEGATE_TOKEN", "").strip()
    if token:
        code, _, body = request(url, headers={"Authorization": "Bearer " + token})
        try:
            answer = json.loads(body)
        except ValueError:
            answer = {}
        if code == 200 and answer.get("signedIn"):
            report(True, "the token is accepted: signed in as %s (%s), %d groups" % (answer.get("email") or "-", answer.get("id"), len(answer.get("groups", []))))
            rules = answer.get("rules") or {}
            if rules.get("loaded"):
                report(True, "the rules load from %s: %d rows" % (rules.get("layer"), rules.get("rows", 0)))
                for problem in rules.get("problems", []):
                    print("WARN  a rules row was left out: " + problem)
            else:
                report(False, "the rules do not load: %s" % rules.get("error"))
        else:
            report(False, "the token is refused: %s" % (answer.get("reason") or "%d %s" % (code, body[:200])))

    if args.origin:
        code, headers, _ = request(
            url,
            method="OPTIONS",
            headers={"Origin": args.origin, "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "authorization"},
        )
        allowed = headers.get("Access-Control-Allow-Origin", "")
        allowed_headers = headers.get("Access-Control-Allow-Headers", "").lower()
        report(
            200 <= code < 300 and allowed == args.origin and "authorization" in allowed_headers,
            "the browser's preflight from %s is answered: %d, Allow-Origin %r, Allow-Headers %r" % (args.origin, code, allowed, allowed_headers),
        )
        code, headers, _ = request(url, headers={"Origin": args.origin})
        exposed = headers.get("Access-Control-Expose-Headers", "").lower()
        report(
            headers.get("Access-Control-Allow-Origin", "") == args.origin and "www-authenticate" in exposed,
            "a refusal sent to %s can be read by the browser: Allow-Origin %r, Expose-Headers %r" % (args.origin, headers.get("Access-Control-Allow-Origin", ""), exposed),
        )

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
