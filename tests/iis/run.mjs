#!/usr/bin/env node
// Castlegate Server's tests through IIS, after install/iis/Install-Castlegate.ps1 has set it up
// beside an existing QGIS Server (New-ExistingServer.ps1). See .github/workflows/iis.yml.
//
//   node tests/iis/run.mjs <TLS .pfx for the stub issuer> <its passphrase>
//
// It serves a stub OpenID Connect issuer at https://localhost:8443 from this process, checks
// Groundwork's QGIS Server at http://localhost/groundwork/qgisserver and the existing one at
// http://localhost/existing/qgisserver, prints one line per check and exits 1 if any fails.

import { spawnSync } from "child_process";
import { readFileSync } from "fs";
import { createServer } from "https";
import { dirname, join, resolve } from "path";
import { fileURLToPath } from "url";
import { issuerKeys, TENANT } from "../server/fixtures.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, "..", "..");
const [pfx, passphrase] = process.argv.slice(2);
const ISSUER = `https://localhost:8443/${TENANT}/v2.0`;
const GW = "http://localhost/groundwork/qgisserver";
const EXISTING = "http://localhost/existing/qgisserver";
const MAP = "C:\\castlegate\\data\\castlegate.qgs";
const ORIGIN = "https://app.groundwork.do";
// One call, so the key set the stub issuer publishes is the one the tokens are signed with.
const { tok, jwks } = issuerKeys(ISSUER);

const results = [];
function record(id, ok, detail) {
  results.push({ id, ok, detail });
  console.log(`${ok === null ? "INFO" : ok ? "PASS" : "FAIL"}  ${id}  ${detail}`);
}

async function req(base, params, { who, body, headers = {}, method } = {}) {
  const url = new URL(base);
  url.searchParams.set("MAP", MAP);
  for (const [k, v] of Object.entries(params)) url.searchParams.set(k, v);
  const h = { ...headers };
  if (who) h.Authorization = `Bearer ${tok(who)}`;
  if (body) h["Content-Type"] = "text/xml";
  const r = await fetch(url, { method: method ?? (body ? "POST" : "GET"), headers: h, body });
  return { status: r.status, headers: r.headers, text: await r.text() };
}
const names = (xml) => [...xml.matchAll(/<Name>([^<]+)<\/Name>/g)].map((m) => m[1]);
const gps = (base, who) => req(base, { SERVICE: "WMS", VERSION: "1.3.0", REQUEST: "GetProjectSettings" }, { who });
const NS = 'xmlns:wfs="http://www.opengis.net/wfs" xmlns:ogc="http://www.opengis.net/ogc" xmlns:qgs="http://www.qgis.org/gml"';
const update = (fid, props) =>
  `<?xml version="1.0"?><wfs:Transaction service="WFS" version="1.0.0" ${NS}><wfs:Update typeName="qgs:assets">${Object.entries(props)
    .map(([k, v]) => `<wfs:Property><wfs:Name>${k}</wfs:Name><wfs:Value>${v}</wfs:Value></wfs:Property>`)
    .join("")}<ogc:Filter><ogc:FeatureId fid="assets.${fid}"/></ogc:Filter></wfs:Update></wfs:Transaction>`;
const save = (who, fid, props) => req(GW, { SERVICE: "WFS", VERSION: "1.0.0", REQUEST: "Transaction" }, { who, body: update(fid, props) });
const succeeded = (r) => r.status === 200 && /SUCCESS/.test(r.text) && !/FAILED|ServiceException/.test(r.text);
async function feature(fid) {
  const r = await req(GW, { SERVICE: "WFS", VERSION: "1.1.0", REQUEST: "GetFeature", TYPENAME: "assets", OUTPUTFORMAT: "application/vnd.geo+json", FEATUREID: `assets.${fid}` }, { who: "alice" });
  return JSON.parse(r.text).features?.[0];
}
const check = (base, extra = [], env = {}) =>
  spawnSync("python", [join(root, "install", "common", "check.py"), base, "--map", MAP, ...extra], { encoding: "utf-8", env: { ...process.env, ...env } });

async function checks() {
  // Sign-in, through IIS.
  const none = await gps(GW);
  record("no-token", none.status === 401 && none.headers.get("www-authenticate") === "Bearer", `status ${none.status}, WWW-Authenticate ${none.headers.get("www-authenticate")}`);
  const [alice, bob] = [names((await gps(GW, "alice")).text), names((await gps(GW, "bob")).text)];
  record("authorization-reaches-qgis", alice.includes("restricted") && !bob.includes("restricted"), `alice sees [${alice.join(", ")}], bob [${bob.join(", ")}]`);
  record("rules-layer-not-served", !alice.includes("castlegate_rules"), `alice's GetProjectSettings ${alice.includes("castlegate_rules") ? "lists" : "does not list"} the rules layer`);
  const bobF = JSON.parse((await req(GW, { SERVICE: "WFS", VERSION: "1.1.0", REQUEST: "GetFeature", TYPENAME: "assets", OUTPUTFORMAT: "application/vnd.geo+json" }, { who: "bob" })).text);
  record("feature-filter", bobF.features?.length === 4, `bob gets ${bobF.features?.length} of 8 assets`);
  const hidden = await save("bob", 2, { secret: "bob wrote this" });
  record("hidden-field-save-refused", !succeeded(hidden) && (await feature(2)).properties.secret === "secret 1", `bob sets "secret": status ${hidden.status}`);
  const allowed = await save("bob", 2, { name: "bob renamed" });
  record("allowed-save", succeeded(allowed) && (await feature(2)).properties.name === "bob renamed", `bob sets "name": status ${allowed.status}`);

  // The status check, as IT would run it.
  const signed = check(GW, ["--origin", ORIGIN], { CASTLEGATE_TOKEN: tok("carol") });
  record("check-signed-in-and-origin", signed.status === 0 && /rules load from castlegate_rules: 11 rows/.test(signed.stdout), `exit ${signed.status}: ${signed.stdout.trim().replace(/\r?\n/g, " | ").slice(0, 600)}`);

  // The IIS CORS module.
  const other = await req(GW, { SERVICE: "CASTLEGATE", REQUEST: "Status" }, { method: "OPTIONS", headers: { Origin: "https://elsewhere.example", "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "authorization" } });
  record("preflight-other-origin-refused", other.status >= 400 && !other.headers.get("access-control-allow-origin"), `status ${other.status}, Allow-Origin ${other.headers.get("access-control-allow-origin")}`);

  // The existing server is untouched: no plugin, everything to everyone.
  const open = names((await gps(EXISTING)).text);
  record("existing-serves-without-sign-in", open.includes("assets") && open.includes("castlegate_rules"), `the existing server, with no token, lists [${open.join(", ")}]`);
  const existingCheck = check(EXISTING);
  record("plugin-not-in-existing-pool", existingCheck.status === 1 && /NOT loaded/.test(existingCheck.stdout), `check.py against the existing server: exit ${existingCheck.status}: ${existingCheck.stdout.trim()}`);
  const gwLog = readFileSync("C:\\Groundwork\\IIS\\QgisServer\\qgis_server.log", "utf-8");
  const exLog = readFileSync("C:\\Existing\\QgisServer\\qgis_server.log", "utf-8");
  record("plugin-loads-in-groundwork-pool-only", /Castlegate.*loaded 0\.1\.0/.test(gwLog) && !/Castlegate/.test(exLog), `Groundwork's log ${/loaded 0\.1\.0/.test(gwLog) ? "has" : "lacks"} the load line; the existing log ${/Castlegate/.test(exLog) ? "mentions" : "never mentions"} Castlegate`);
  const audit = gwLog.split(/\r?\n/).filter((l) => /audit id=/.test(l));
  record("audit-lines", audit.length >= 2, `${audit.length} audit lines in Groundwork's log`);
}

async function main() {
  const server = createServer({ pfx: readFileSync(pfx), passphrase }, (request, response) => {
    const path = new URL(request.url, "https://localhost").pathname;
    const documents = {
      [`/${TENANT}/v2.0/.well-known/openid-configuration`]: { issuer: ISSUER, jwks_uri: "https://localhost:8443/keys.json" },
      "/keys.json": jwks,
    };
    const document = documents[path];
    response.writeHead(document ? 200 : 404, { "Content-Type": "application/json" });
    response.end(document ? JSON.stringify(document) : "{}");
  });
  await new Promise((r) => server.listen(8443, r));
  try {
    await checks();
  } finally {
    server.close();
  }
  const failed = results.filter((r) => r.ok === false);
  console.log(`\n${results.filter((r) => r.ok).length} passed, ${failed.length} failed`);
  process.exitCode = failed.length ? 1 : 0;
}

main().catch((e) => {
  console.error(e);
  process.exitCode = 2;
});
