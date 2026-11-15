#!/usr/bin/env node
// Castlegate Server's tests against a real QGIS Server in Docker.
//
//   node tests/server/run.mjs [--image qgis/qgis-server:ltr] [work folder]
//   CASTLEGATE_BACKEND=mssql node tests/server/run.mjs [work folder]
//
// It builds the plugin (scripts/build.py) and the test projects, starts a stub
// OpenID Connect issuer over HTTPS and QGIS Server from the Docker install
// profile's image (install/docker/) built on the image under test, runs each
// check against http://localhost:8020, including install/common/check.py, and
// prints one line per check. It exits 1 if any check fails.
//
// With CASTLEGATE_BACKEND=mssql the layers and rules tables live in SQL Server:
// it builds the image in mssql/ from the chosen image, adding Microsoft's ODBC
// driver, and starts Microsoft's SQL Server 2022 Developer container. Both accept
// Microsoft's licence terms.

import { spawnSync } from "child_process";
import { cpSync, mkdirSync, rmSync, writeFileSync } from "fs";
import { randomBytes } from "crypto";
import { dirname, join, resolve } from "path";
import { fileURLToPath } from "url";
import { AUDIENCE, b64, G, issuerKeys, KID, people, RULES, SCOPE, TENANT } from "./fixtures.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, "..", "..");
const args = process.argv.slice(2);
const imageArg = args.indexOf("--image");
const BASE_IMAGE = imageArg >= 0 ? args.splice(imageArg, 2)[1] : "qgis/qgis-server:ltr";
const work = resolve(args[0] ?? join(root, ".work", "server"));
const MSSQL = process.env.CASTLEGATE_BACKEND === "mssql";
const IMAGE = MSSQL ? "castlegate-qgis-mssql" : BASE_IMAGE;
const PROFILE_IMAGE = "castlegate-profile-test";
const MSSQL_IMAGE = "mcr.microsoft.com/mssql/server:2022-latest";
// A test password for this run's throwaway container, meeting SQL Server's complexity rule.
const SA_PASSWORD = `Cg_${randomBytes(12).toString("hex")}_A1`;
const MSSQL_URI = `dbname='castlegate' service='DSN=castlegate' host='castlegate-mssql' user='sa' password='${SA_PASSWORD}'`;
const NET = "castlegate-tests";
const PORT = 8020;
const BASE = `http://localhost:${PORT}/`;
const OWS = "ows/";
const ISSUER_HOST = "castlegate-issuer";
const ISSUER = `https://${ISSUER_HOST}:8443/${TENANT}/v2.0`;
const MAP = "/io/data/castlegate.qgs";
const MAP_B = "/io/data/castlegate-b.qgs";
const MAP_NONE = "/io/data/castlegate-norules.qgs";
const MAP_MISSING = "/io/data/castlegate-missing.qgs";

const results = [];
function record(id, ok, detail) {
  results.push({ id, ok, detail });
  console.log(`${ok === null ? "INFO" : ok ? "PASS" : "FAIL"}  ${id}  ${detail}`);
}

function run(cmd, cmdArgs, { allowFail = false, env } = {}) {
  const r = spawnSync(cmd, cmdArgs, { encoding: "utf-8", env: { ...process.env, ...env } });
  if (r.error) throw r.error;
  if (r.status !== 0 && !allowFail) throw new Error(`${cmd} ${cmdArgs.join(" ")}\n${r.stderr}${r.stdout}`);
  return r;
}
function docker(dockerArgs, { allowFail = false, env } = {}) {
  if (env) dockerArgs = [dockerArgs[0], ...Object.keys(env).flatMap((k) => ["-e", k]), ...dockerArgs.slice(1)];
  return run("docker", dockerArgs, { allowFail, env });
}

// ---- keys, tokens and the stub issuer -------------------------------------------------

const { stranger, token, tok, jwks } = issuerKeys(ISSUER);

const dirs = {
  build: join(work, "build"),
  pristine: join(work, "pristine"),
  live: join(work, "live"),
  issuer: join(work, "issuer"),
  tls: join(work, "tls"),
  profile: join(work, "profile"),
};

function writeIssuer() {
  const wellKnown = join(dirs.issuer, TENANT, "v2.0", ".well-known");
  mkdirSync(wellKnown, { recursive: true });
  writeFileSync(join(wellKnown, "openid-configuration"), JSON.stringify({ issuer: ISSUER, jwks_uri: `https://${ISSUER_HOST}:8443/keys.json` }));
  writeFileSync(join(dirs.issuer, "keys.json"), JSON.stringify(jwks));
  // The plugin fetches over HTTPS only, so the stub issuer gets a certificate QGIS Server is told to trust.
  mkdirSync(dirs.tls, { recursive: true });
  // Made with the image's own openssl, so the host needs none.
  docker([
    "run", "--rm", "-v", `${dirs.tls}:/tls`, "--entrypoint", "openssl", IMAGE,
    "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-keyout", "/tls/key.pem", "-out", "/tls/cert.pem",
    "-subj", `/CN=${ISSUER_HOST}`, "-addext", `subjectAltName=DNS:${ISSUER_HOST}`,
  ], { env: { MSYS_NO_PATHCONV: "1" } });
  docker(["run", "--rm", "-v", `${dirs.tls}:/tls`, "--entrypoint", "chmod", IMAGE, "a+r", "/tls/key.pem", "/tls/cert.pem"]);
  writeFileSync(
    join(dirs.tls, "serve.py"),
    [
      "import functools, http.server, ssl",
      "handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory='/srv')",
      "server = http.server.ThreadingHTTPServer(('0.0.0.0', 8443), handler)",
      "context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)",
      "context.load_cert_chain('/tls/cert.pem', '/tls/key.pem')",
      "server.socket = context.wrap_socket(server.socket, server_side=True)",
      "server.serve_forever()",
    ].join("\n")
  );
}

// ---- SQL Server -------------------------------------------------------------------------

function sql(query, { db = "castlegate", allowFail = false } = {}) {
  return docker(
    ["exec", "castlegate-mssql", "sh", "-c", `SQLCMDPASSWORD="$MSSQL_SA_PASSWORD" /opt/mssql-tools18/bin/sqlcmd -C -b -I -S localhost -U sa -d ${db} -h -1 -W -Q "$Q"`],
    { allowFail, env: { Q: query } }
  );
}

const DATA_TABLES = ["assets", "restricted", "big", "site_notes"];
const RULES_TABLES = ["castlegate_rules", "castlegate_rules_b"];

async function startSqlServer() {
  docker(["build", "-q", "--build-arg", `BASE=${BASE_IMAGE}`, "-t", IMAGE, join(here, "mssql")]);
  docker(["run", "-d", "--name", "castlegate-mssql", "--network", NET, "-e", "ACCEPT_EULA=Y", "-e", "MSSQL_PID=Developer", "-e", `MSSQL_SA_PASSWORD=${SA_PASSWORD}`, MSSQL_IMAGE]);
  for (let i = 0; i < 90 && sql("SELECT 1", { db: "master", allowFail: true }).status !== 0; i++) await new Promise((r) => setTimeout(r, 1000));
  sql("CREATE DATABASE castlegate", { db: "master" });
  const data = "fid INT IDENTITY(1,1) PRIMARY KEY, name NVARCHAR(100), owner NVARCHAR(100), status NVARCHAR(20), secret NVARCHAR(100), geom geometry";
  for (const t of DATA_TABLES) sql(`CREATE TABLE dbo.${t} (${data}); CREATE SPATIAL INDEX ${t}_geom ON dbo.${t}(geom) WITH (BOUNDING_BOX = (144, -39, 146, -37));`);
  const text = ["who_type", "who", "layer", "can_read", "can_insert", "can_update_attributes", "can_update_geometry", "can_delete", "read_filter", "edit_filter", "hidden_fields", "readonly_fields"]
    .map((c) => `${c} NVARCHAR(400)`)
    .join(", ");
  for (const t of RULES_TABLES) sql(`CREATE TABLE dbo.${t} (fid INT IDENTITY(1,1) PRIMARY KEY, ${text})`);
  record("mssql-version", null, sql("SELECT @@VERSION", { db: "master" }).stdout.trim().split("\n")[0]);
}

function resetSqlServer() {
  sql("DELETE FROM dbo.assets; SET IDENTITY_INSERT dbo.assets ON; INSERT INTO dbo.assets (fid, name, owner, status, secret, geom) SELECT fid, name, owner, status, secret, geom FROM dbo.assets_pristine; SET IDENTITY_INSERT dbo.assets OFF; DBCC CHECKIDENT ('dbo.assets', RESEED) WITH NO_INFOMSGS;");
}

// ---- containers ------------------------------------------------------------------------

async function prepare() {
  rmSync(work, { recursive: true, force: true });
  mkdirSync(dirs.pristine, { recursive: true });
  run("python3", [join(root, "scripts", "build.py"), dirs.build], { allowFail: false });
  // The server under test is the Docker install profile's image, built on the image under test.
  mkdirSync(dirs.profile, { recursive: true });
  for (const f of ["Dockerfile", "castlegate-cors.conf"]) cpSync(join(root, "install", "docker", f), join(dirs.profile, f));
  cpSync(join(dirs.build, "castlegate_server"), join(dirs.profile, "castlegate_server"), { recursive: true });
  docker(["rm", "-f", "castlegate-issuer", "castlegate-server", "castlegate-mssql"], { allowFail: true });
  docker(["network", "rm", NET], { allowFail: true });
  docker(["network", "create", NET]);
  if (MSSQL) await startSqlServer();
  docker(["build", "-q", "--build-arg", `BASE=${IMAGE}`, "-t", PROFILE_IMAGE, dirs.profile]);
  docker([
    "run", "--rm", "--network", NET, "-e", "QT_QPA_PLATFORM=offscreen",
    // QGIS 4's image keeps its Python library outside the default path.
    "-e", "PYTHONPATH=/usr/share/qgis/python", "-e", `CASTLEGATE_TEST_RULES=${JSON.stringify(RULES)}`,
    ...(MSSQL ? ["-e", `CASTLEGATE_MSSQL=${MSSQL_URI}`] : []),
    "-v", `${dirs.pristine}:/io/data`, "-v", `${here}:/work:ro`,
    "--entrypoint", "python3", IMAGE, "/work/build_project.py",
  ]);
  if (MSSQL) sql("SELECT * INTO dbo.assets_pristine FROM dbo.assets");
  writeIssuer();
  docker([
    "run", "-d", "--name", "castlegate-issuer", "--network", NET, "-v", `${dirs.issuer}:/srv:ro`, "-v", `${dirs.tls}:/tls:ro`,
    "--entrypoint", "python3", IMAGE, "/tls/serve.py",
  ]);
}

async function startServer({ plugin = true, env = {} } = {}) {
  docker(["rm", "-f", "castlegate-server"], { allowFail: true });
  rmSync(dirs.live, { recursive: true, force: true });
  cpSync(dirs.pristine, dirs.live, { recursive: true });
  if (MSSQL) resetSqlServer();
  const vars = {
    QGIS_SERVER_LOG_LEVEL: "0",
    CASTLEGATE_ISSUER: ISSUER,
    CASTLEGATE_AUDIENCE: AUDIENCE,
    CASTLEGATE_SCOPE: SCOPE,
    CASTLEGATE_TENANT: TENANT,
    SSL_CERT_FILE: "/tls/cert.pem",
    CASTLEGATE_CHECK_MAP: MAP,
    ...env,
  };
  const dockerArgs = ["run", "-d", "--name", "castlegate-server", "--network", NET, "-p", `${PORT}:80`, "-v", `${dirs.live}:/io/data`, "-v", `${dirs.tls}:/tls:ro`];
  for (const [k, v] of Object.entries(vars)) dockerArgs.push("-e", `${k}=${v}`);
  dockerArgs.push(plugin ? PROFILE_IMAGE : IMAGE);
  docker(dockerArgs);
  // Files on a Windows bind mount show as root's with mode 644; QGIS Server runs as www-data.
  docker(["exec", "castlegate-server", "sh", "-c", "chmod a+rw /io/data /io/data/*"]);
  for (let i = 0; i < 90; i++) {
    try {
      const r = await fetch(`${BASE}${OWS}?SERVICE=WMS&REQUEST=GetCapabilities&MAP=${MAP}`);
      await r.arrayBuffer();
      return;
    } catch {
      await new Promise((r) => setTimeout(r, 1000));
    }
  }
  throw new Error("QGIS Server did not answer");
}

const logs = () => {
  const r = docker(["logs", "castlegate-server"], { allowFail: true });
  return (r.stdout ?? "") + (r.stderr ?? "");
};
const alive = () => docker(["inspect", "-f", "{{.State.Running}}", "castlegate-server"], { allowFail: true }).stdout.trim() === "true";

// ---- requests --------------------------------------------------------------------------

async function req(params, { who, auth, body, map = MAP, path = OWS, contentType = "text/xml" } = {}) {
  const url = new URL(BASE + path);
  if (map) url.searchParams.set("MAP", map);
  for (const [k, v] of Object.entries(params)) url.searchParams.set(k, v);
  const headers = {};
  const t = auth ?? (who ? tok(who) : undefined);
  if (t) headers.Authorization = `Bearer ${t}`;
  if (body) headers["Content-Type"] = contentType;
  const r = await fetch(url, { method: body ? "POST" : "GET", headers, body });
  const buf = Buffer.from(await r.arrayBuffer());
  return { status: r.status, headers: r.headers, text: buf.toString("utf-8"), buf };
}

const gps = (who, opts) => req({ SERVICE: "WMS", VERSION: "1.3.0", REQUEST: "GetProjectSettings" }, { who, ...opts });
const wfsCaps = (who, opts) => req({ SERVICE: "WFS", VERSION: "1.1.0", REQUEST: "GetCapabilities" }, { who, ...opts });
async function getFeatures(who, layer, extra = {}, opts = {}) {
  const r = await req({ SERVICE: "WFS", VERSION: "1.1.0", REQUEST: "GetFeature", TYPENAME: layer, OUTPUTFORMAT: "application/vnd.geo+json", ...extra }, { who, ...opts });
  try {
    return { ...r, json: JSON.parse(r.text) };
  } catch {
    return { ...r, json: null };
  }
}
const status = (who, opts) => req({ SERVICE: "CASTLEGATE", REQUEST: "Status" }, { who, ...opts });
const names = (xml) => [...xml.matchAll(/<Name>([^<]+)<\/Name>/g)].map((m) => m[1]);
const short = (s) => {
  const m = s.match(/<(?:\w+:)?(?:Message|ServiceException[^>]*|ServerException)>([^<]*)</);
  return (m ? `[${/SUCCESS/.test(s) ? "SUCCESS" : /FAILED/.test(s) ? "FAILED" : "exception"}] ${m[1]}` : s).replace(/\s+/g, " ").slice(0, 220);
};

const NS = 'xmlns:wfs="http://www.opengis.net/wfs" xmlns:ogc="http://www.opengis.net/ogc" xmlns:gml="http://www.opengis.net/gml" xmlns:qgs="http://www.qgis.org/gml"';
const tx = (inner) => `<?xml version="1.0"?><wfs:Transaction service="WFS" version="1.0.0" ${NS}>${inner}</wfs:Transaction>`;
const POINT = (x, y) => `<gml:Point srsName="EPSG:4326"><gml:coordinates>${x},${y}</gml:coordinates></gml:Point>`;
const update = (layer, fid, props) =>
  `<wfs:Update typeName="qgs:${layer}">${Object.entries(props)
    .map(([k, v]) => `<wfs:Property><wfs:Name>${k}</wfs:Name><wfs:Value>${v}</wfs:Value></wfs:Property>`)
    .join("")}<ogc:Filter><ogc:FeatureId fid="${layer}.${fid}"/></ogc:Filter></wfs:Update>`;
const insert = (layer, props) =>
  `<wfs:Insert><qgs:${layer}><qgs:geometry>${POINT(144.99, -37.81)}</qgs:geometry>${Object.entries(props)
    .map(([k, v]) => `<qgs:${k}>${v}</qgs:${k}>`)
    .join("")}</qgs:${layer}></wfs:Insert>`;
const del = (layer, fid) => `<wfs:Delete typeName="qgs:${layer}"><ogc:Filter><ogc:FeatureId fid="${layer}.${fid}"/></ogc:Filter></wfs:Delete>`;
const post = (who, inner, opts) => req({ SERVICE: "WFS", VERSION: "1.0.0", REQUEST: "Transaction" }, { who, body: tx(inner), ...opts });
const succeeded = (r) => r.status === 200 && /SUCCESS/.test(r.text) && !/FAILED|ServiceException/.test(r.text);
const feature = async (layer, fid) => (await getFeatures("alice", layer, { FEATUREID: `${layer}.${fid}` })).json?.features?.[0];
const coords = (f) => f?.geometry?.coordinates?.map((c) => c.toFixed(3)).join(",");

// ---- checks -----------------------------------------------------------------------------

async function checkTokens() {
  const none = await gps();
  record("no-token", none.status === 401 && !/assets/.test(none.text), `status ${none.status}: ${short(none.text)}`);
  record("no-token-www-authenticate", none.headers.get("www-authenticate") === "Bearer", `WWW-Authenticate: ${none.headers.get("www-authenticate")}`);
  const now = Math.floor(Date.now() / 1000);
  const bad = {
    "bad-signature": token(people.alice, { key: stranger.privateKey }),
    "alg-none": `${b64({ alg: "none", typ: "JWT", kid: KID })}.${b64({ ...people.alice, iss: ISSUER, aud: AUDIENCE, tid: TENANT, scp: SCOPE, exp: now + 600 })}.`,
    "unknown-kid": token(people.alice, { header: { kid: "other" } }),
    expired: tok("alice", { exp: now - 3600 }),
    "not-yet-valid": tok("alice", { nbf: now + 3600 }),
    "wrong-audience": tok("alice", { aud: "api://someone-else" }),
    "wrong-issuer": tok("alice", { iss: "https://login.microsoftonline.com/other/v2.0" }),
    "wrong-tenant": tok("alice", { tid: "00000000-0000-0000-0000-000000000000" }),
    "no-scope": tok("alice", { scp: "other" }),
    "groups-overage": tok("carol", { groups: undefined, _claim_names: { groups: "src1" } }),
  };
  for (const [name, t] of Object.entries(bad)) {
    const r = await gps(undefined, { auth: t });
    record(`token-${name}`, r.status === 401 && !/<Name>assets<\/Name>/.test(r.text), `status ${r.status}: ${short(r.text)}`);
  }
  const good = await gps("alice");
  record("token-good", good.status === 200 && /<Name>assets<\/Name>/.test(good.text), `status ${good.status}`);
}

async function checkStatus() {
  const s0 = await status(undefined);
  const body0 = JSON.parse(s0.text || "{}");
  record("status-no-token", s0.status === 401 && s0.headers.get("www-authenticate") === "Bearer" && body0.signedIn === false, `status ${s0.status}, WWW-Authenticate ${s0.headers.get("www-authenticate")}: ${s0.text.slice(0, 160)}`);
  const s1 = await status("carol");
  const body = JSON.parse(s1.text || "{}");
  record(
    "status-signed-in",
    s1.status === 200 && body.email === "carol@example.org" && body.groups?.includes(G) && body.rules?.loaded === true && body.rules?.rows === 11 && body.rules?.problems?.length === 2,
    `status ${s1.status}: id ${body.id}, rules ${JSON.stringify(body.rules)}`
  );
  const s2 = await status("alice", { map: MAP_NONE });
  const body2 = JSON.parse(s2.text || "{}");
  record("status-no-rules", body2.rules?.loaded === false && /castlegate_rules_table/.test(body2.rules?.error ?? ""), `a project with no rules variable: ${JSON.stringify(body2.rules)}`);
  const unknown = await req({ SERVICE: "NOSUCH", REQUEST: "Status" }, { who: "alice" });
  record("unknown-service-refused", unknown.status === 403, `status ${unknown.status}: ${short(unknown.text)}`);
}

async function checkLayers() {
  const [bob, alice] = [names((await gps("bob")).text), names((await gps("alice")).text)];
  record("hidden-layer-getprojectsettings", !bob.includes("restricted") && alice.includes("restricted"), `bob sees [${bob.join(", ")}], alice sees [${alice.join(", ")}]`);
  const [bobCaps, aliceCaps] = [(await wfsCaps("bob")).text, (await wfsCaps("alice")).text];
  record("hidden-layer-wfs-capabilities", !/<Name>restricted<\/Name>/.test(bobCaps) && /<Name>restricted<\/Name>/.test(aliceCaps), `restricted for bob: ${/<Name>restricted<\/Name>/.test(bobCaps)}, for alice: ${/<Name>restricted<\/Name>/.test(aliceCaps)}`);
  record("rules-layer-not-in-capabilities", !alice.includes("castlegate_rules") && !/castlegate_rules/.test(aliceCaps), `alice, who has a row naming it: GetProjectSettings ${alice.includes("castlegate_rules") ? "lists" : "does not list"} it, WFS capabilities ${/castlegate_rules/.test(aliceCaps) ? "list" : "do not list"} it`);
  const rulesRead = await getFeatures("alice", "castlegate_rules");
  record("rules-layer-not-served", !rulesRead.json?.features?.length && !/bob@example.org/.test(rulesRead.text), `GetFeature on the rules layer as alice: status ${rulesRead.status}, ${rulesRead.json?.features?.length ?? "no"} features: ${short(rulesRead.text)}`);
  const frank = names((await gps("frank")).text);
  record("no-rows-no-layers", !["assets", "restricted", "big", "site notes"].some((n) => frank.includes(n)), `frank, with no rows, sees [${frank.join(", ")}]`);
}

async function checkFilters() {
  const bob = await getFeatures("bob", "assets");
  const alice = await getFeatures("alice", "assets");
  const owners = [...new Set((bob.json?.features ?? []).map((f) => f.properties.owner))];
  record("filter-wfs-getfeature", bob.json?.features?.length === 4 && owners.join() === "bob@example.org" && alice.json?.features?.length === 8, `bob gets ${bob.json?.features?.length} (owners ${owners}), alice gets ${alice.json?.features?.length}`);
  const fields = Object.keys(bob.json?.features?.[0]?.properties ?? {});
  record("hidden-field-values-getfeature", fields.length > 0 && !fields.includes("secret"), `bob's fields: ${fields.join(", ")}`);
  // Review finding: QGIS Server runs a request's own filter or sort without asking the access
  // filter, so a filter on "secret" told bob its values, and functions such as aggregate() read
  // layers he may not see, the rules layer included. Each is now refused, whatever its outcome
  // would have been, and a filter on a field he may see still works.
  const features = (r) => (r.status === 200 ? r.json?.features?.length : undefined);
  const refusedFor = async (id, who, extra, label) => {
    const r = await getFeatures(who, "assets", extra);
    record(id, features(r) === undefined, `${label}: status ${r.status}: ${short(r.text)}`);
  };
  await refusedFor("filter-hidden-field-refused", "bob", { EXP_FILTER: `"secret" = 'secret 1'` }, `bob's EXP_FILTER on the hidden "secret"`);
  await refusedFor("filter-hidden-field-no-answer", "bob", { EXP_FILTER: `"secret" = 'no such secret'` }, `the same with a wrong guess`);
  await refusedFor("filter-other-layer-refused", "bob", { EXP_FILTER: `aggregate('restricted', 'count', $id) > 0` }, `bob's EXP_FILTER counting a layer he may not see`);
  await refusedFor("filter-rules-layer-refused", "bob", { EXP_FILTER: `get_feature('castlegate_rules', 'who', 'alice@example.org') IS NOT NULL` }, `bob's EXP_FILTER reading the rules layer`);
  await refusedFor("filter-eval-refused", "bob", { EXP_FILTER: `eval('"sec' || 'ret"') = 'secret 1'` }, `bob's EXP_FILTER building the name with eval()`);
  await refusedFor("filter-attribute-refused", "bob", { EXP_FILTER: `attribute($currentfeature, 'secret') = 'secret 1'` }, `bob's EXP_FILTER reading the field with attribute()`);
  await refusedFor("filter-variable-refused", "bob", { EXP_FILTER: `@project_path IS NOT NULL` }, `bob's EXP_FILTER with a variable`);
  // Review finding on #7: QGIS also finds a field by its alias, here "Secret code" for "secret".
  await refusedFor("filter-hidden-field-alias-refused", "bob", { EXP_FILTER: `"Secret code" = 'secret 1'` }, `bob's EXP_FILTER on "secret" by its alias`);
  await refusedFor("sort-hidden-field-alias-refused", "bob", { SORTBY: `"secret CODE" ASC` }, `bob's SORTBY on "secret" by its alias`);
  await refusedFor("sort-hidden-field-refused", "bob", { SORTBY: "secret ASC" }, `bob's SORTBY on the hidden "secret"`);
  const ogc = (inner) => `<ogc:Filter xmlns:ogc="http://www.opengis.net/ogc">${inner}</ogc:Filter>`;
  const equal = (name, value) => `<ogc:PropertyIsEqualTo><ogc:PropertyName>${name}</ogc:PropertyName><ogc:Literal>${value}</ogc:Literal></ogc:PropertyIsEqualTo>`;
  await refusedFor("ogc-filter-hidden-field-refused", "bob", { FILTER: ogc(equal("secret", "secret 1")) }, `bob's FILTER on the hidden "secret"`);
  await refusedFor("ogc-filter-hidden-field-alias-refused", "bob", { FILTER: ogc(equal("Secret code", "secret 1")) }, `bob's FILTER on "secret" by its alias`);
  // Review finding on #7: QGIS takes a property's name from its first child node, here the text before the comment.
  await refusedFor("ogc-filter-hidden-field-split-refused", "bob", { FILTER: ogc(equal("secret<!--x-->zzz", "secret 1")) }, `bob's FILTER naming "secret" before a comment`);
  await refusedFor("ogc-filter-function-refused", "bob", { FILTER: ogc(`<ogc:PropertyIsGreaterThan><ogc:Function name="aggregate"><ogc:Literal>restricted</ogc:Literal><ogc:Literal>count</ogc:Literal><ogc:Literal>1</ogc:Literal></ogc:Function><ogc:Literal>0</ogc:Literal></ogc:PropertyIsGreaterThan>`) }, `bob's FILTER calling aggregate()`);
  const allowed = async (id, who, extra, expected, label) => {
    const r = await getFeatures(who, "assets", extra);
    record(id, features(r) === expected, `${label}: status ${r.status}, ${features(r) ?? short(r.text)} features`);
  };
  // QGIS reads that name as "secret" for alice, who may see it.
  await allowed("ogc-filter-split-read-by-qgis", "alice", { FILTER: ogc(equal("secret<!--x-->zzz", "secret 0")) }, 1, `alice's FILTER naming "secret" before a comment`);
  await allowed("filter-visible-field-allowed", "bob", { EXP_FILTER: `"name" = 'asset 1'` }, 1, `bob's EXP_FILTER on "name"`);
  await allowed("ogc-filter-visible-field-allowed", "bob", { FILTER: ogc(equal("name", "asset 1")) }, 1, `bob's FILTER on "name"`);
  await allowed("sort-visible-field-allowed", "bob", { SORTBY: "name DESC" }, 4, `bob's SORTBY on "name"`);
  // QGIS resolves the alias for alice, who may see "secret", so the refusals above are not for an unknown name.
  await allowed("filter-alias-resolves", "alice", { EXP_FILTER: `"Secret code" = 'secret 0'` }, 1, `alice's EXP_FILTER on "secret" by its alias`);
  await allowed("filter-field-shown-allowed", "alice", { EXP_FILTER: `"secret" = 'secret 0'` }, 1, `alice's EXP_FILTER on "secret", which she may see`);
  const getFeatureBody = (inner) => `<wfs:GetFeature service="WFS" version="1.1.0" outputFormat="application/vnd.geo+json" xmlns:wfs="http://www.opengis.net/wfs" xmlns:ogc="http://www.opengis.net/ogc"><wfs:Query typeName="assets">${inner}</wfs:Query></wfs:GetFeature>`;
  const bodyHidden = await req({ SERVICE: "WFS" }, { who: "bob", body: getFeatureBody(ogc(equal("secret", "secret 1"))) });
  record("body-filter-hidden-field-refused", !(bodyHidden.status === 200 && /"features"\s*:\s*\[\s*\{/.test(bodyHidden.text)), `bob's GetFeature body filtering on "secret": status ${bodyHidden.status}: ${short(bodyHidden.text)}`);
  // Review finding on #7: Qt reads UTF-16BE with no byte order mark as XML, and the plugin skipped it.
  const utf16 = (text) => Buffer.from(text, "utf16le").swap16();
  const bodyUtf16 = await req({ SERVICE: "WFS", REQUEST: "GetFeature" }, { who: "bob", body: utf16(getFeatureBody(ogc(equal("secret", "secret 1")))) });
  record("body-utf16-hidden-field-refused", !(bodyUtf16.status === 200 && /"features"\s*:\s*\[\s*\{/.test(bodyUtf16.text)), `bob's UTF-16BE GetFeature body filtering on "secret": status ${bodyUtf16.status}: ${short(bodyUtf16.text)}`);
  // QGIS reads that body for alice, who may see "secret", so the refusal above is not for an unreadable body.
  const aliceUtf16 = await req({ SERVICE: "WFS", REQUEST: "GetFeature" }, { who: "alice", body: utf16(getFeatureBody(ogc(equal("secret", "secret 0")))) });
  record("body-utf16-read-by-qgis", aliceUtf16.status === 200 && /secret 0/.test(aliceUtf16.text), `alice's UTF-16BE GetFeature body filtering on "secret": status ${aliceUtf16.status}: ${short(aliceUtf16.text)}`);
  const bodyVisible = await req({ SERVICE: "WFS" }, { who: "bob", body: getFeatureBody(ogc(equal("name", "asset 1"))) });
  record("body-filter-visible-field-allowed", bodyVisible.status === 200 && /asset 1/.test(bodyVisible.text), `bob's GetFeature body filtering on "name": status ${bodyVisible.status}: ${short(bodyVisible.text)}`);
  const featureInfo = (who, filter) => req({ SERVICE: "WMS", VERSION: "1.3.0", REQUEST: "GetFeatureInfo", LAYERS: "assets", QUERY_LAYERS: "assets", INFO_FORMAT: "application/json", FILTER: filter, FEATURE_COUNT: "50", CRS: "EPSG:4326", WIDTH: "10", HEIGHT: "10", BBOX: "-90,-180,90,180" }, { who });
  const infoCount = (r) => { try { return r.status === 200 ? JSON.parse(r.text).features?.length : undefined; } catch { return undefined; } };
  const wmsHidden = await featureInfo("bob", `assets:"secret" = 'secret 1'`);
  record("wms-filter-hidden-field-refused", infoCount(wmsHidden) === undefined, `bob's WMS FILTER on "secret": status ${wmsHidden.status}: ${short(wmsHidden.text)}`);
  const wmsVisible = await featureInfo("bob", `(${ogc(equal("name", "asset 1"))})`);
  record("wms-filter-visible-field-allowed", infoCount(wmsVisible) === 1, `bob's WMS OGC FILTER on "name": status ${wmsVisible.status}, ${infoCount(wmsVisible) ?? short(wmsVisible.text)} features`);
  // Review finding on #7: a layer:condition is SQL for the database, which can read a hidden
  // column without its name being written, so a condition is refused whatever it names.
  const wmsCondition = await featureInfo("bob", `assets:"name" = 'asset 1'`);
  record("wms-filter-condition-refused", infoCount(wmsCondition) === undefined, `bob's WMS FILTER condition on "name": status ${wmsCondition.status}: ${short(wmsCondition.text)}`);
  const sld = `<StyledLayerDescriptor version="1.0.0" xmlns="http://www.opengis.net/sld"><NamedLayer><Name>assets</Name><UserStyle><FeatureTypeStyle><Rule><TextSymbolizer><Label><PropertyName xmlns="http://www.opengis.net/ogc">secret</PropertyName></Label></TextSymbolizer></Rule></FeatureTypeStyle></UserStyle></NamedLayer></StyledLayerDescriptor>`;
  const styled = await req({ SERVICE: "WMS", VERSION: "1.3.0", REQUEST: "GetMap", LAYERS: "assets", STYLES: "", CRS: "EPSG:4326", BBOX: "-38,144,-37,146", WIDTH: "64", HEIGHT: "64", FORMAT: "image/png", SLD_BODY: sld }, { who: "bob" });
  record("wms-style-in-request-refused", !(styled.status === 200 && /image\/png/.test(styled.headers?.get?.("content-type") ?? "")), `bob's GetMap with a style labelling "secret": status ${styled.status}: ${short(styled.text)}`);
  const big = await getFeatures("alice", "big");
  record("filter-placeholder", big.json?.features?.length === 1 && big.json.features[0].properties.owner === "alice@example.org", `alice's big features with "owner" = {{email}}: ${big.json?.features?.length}`);

  // WMS GetMap: asset 0 (alice's) is at 144.96,-37.81.
  const map = (who, bbox) =>
    req({ SERVICE: "WMS", VERSION: "1.3.0", REQUEST: "GetMap", LAYERS: "assets", STYLES: "", CRS: "EPSG:4326", BBOX: bbox, WIDTH: "64", HEIGHT: "64", FORMAT: "image/png", TRANSPARENT: "true" }, { who });
  const onAlice = "-37.8105,144.9595,-37.8095,144.9605";
  const empty = "-37.0005,144.0,-36.9995,144.001";
  const [bobOn, aliceOn, bobEmpty] = [await map("bob", onAlice), await map("alice", onAlice), await map("bob", empty)];
  record("filter-wms-getmap", bobOn.status === 200 && bobOn.buf.equals(bobEmpty.buf) && !aliceOn.buf.equals(bobEmpty.buf), `around alice's asset, bob's image ${bobOn.buf.equals(bobEmpty.buf) ? "equals" : "differs from"} an empty one; alice's ${aliceOn.buf.equals(bobEmpty.buf) ? "equals" : "differs from"} it`);
  const gfi = (who) =>
    req({ SERVICE: "WMS", VERSION: "1.3.0", REQUEST: "GetFeatureInfo", LAYERS: "assets", QUERY_LAYERS: "assets", STYLES: "", CRS: "EPSG:4326", BBOX: onAlice, WIDTH: "64", HEIGHT: "64", I: "32", J: "32", INFO_FORMAT: "application/json", FEATURE_COUNT: "5", FI_POINT_TOLERANCE: "16" }, { who });
  const [nb, na] = [JSON.parse((await gfi("bob")).text).features?.length, JSON.parse((await gfi("alice")).text).features?.length];
  record("filter-wms-getfeatureinfo", nb === 0 && na >= 1, `bob gets ${nb} features at alice's asset, alice gets ${na}`);
}

async function checkSaves() {
  // Rights QGIS Server itself checks.
  const ins = await post("bob", insert("assets", { name: "new", owner: "bob@example.org" }));
  record("refused-insert", !succeeded(ins), `bob inserts: status ${ins.status}: ${short(ins.text)}`);
  const dl = await post("bob", del("assets", 2));
  record("refused-delete", !succeeded(dl) && (await feature("assets", 2)), `bob deletes: status ${dl.status}: ${short(dl.text)}`);
  const up = await post("frank", update("assets", 2, { name: "frank" }));
  record("refused-update", !succeeded(up) && (await feature("assets", 2)).properties.name === "asset 1", `frank updates: status ${up.status}: ${short(up.text)}`);

  // The edit filter, checked by allowToEdit.
  const other = await post("alice", update("assets", 2, { name: "alice edits bob's" }));
  record("edit-filter-other-feature", !succeeded(other) && (await feature("assets", 2)).properties.name === "asset 1", `alice updates bob's: status ${other.status}: ${short(other.text)}`);
  const own = await post("alice", update("assets", 1, { name: "alice renamed" }));
  record("edit-filter-own-feature", succeeded(own) && (await feature("assets", 1)).properties.name === "alice renamed", `alice updates her own: ${short(own.text)}`);
  const away = await post("alice", update("assets", 3, { owner: "bob@example.org" }));
  record("edit-filter-moves-out-of-scope", !succeeded(away) && (await feature("assets", 3)).properties.owner === "alice@example.org", `alice gives hers to bob: status ${away.status}: ${short(away.text)}`);

  // A group row granting deletes with no filter does not widen carol's changes.
  const carol = await post("carol", update("assets", 2, { name: "carol was here" }));
  record("per-right-filter", !succeeded(carol) && (await feature("assets", 2)).properties.name === "asset 1", `carol, who may delete any feature but change only her own, changes bob's: status ${carol.status}: ${short(carol.text)}`);

  // Fields the plugin checks itself.
  const hidden = await post("bob", update("assets", 2, { secret: "bob wrote this" }));
  record("field-hidden-refused", !succeeded(hidden) && (await feature("assets", 2)).properties.secret === "secret 1", `bob sets hidden "secret": status ${hidden.status}: ${short(hidden.text)}`);
  record("refusal-response", null, `what a client receives for a save the rules refuse: ${hidden.text.replace(/\s+/g, " ").slice(0, 400)}`);
  // Review finding on #3: QGIS reads <Name>sec<x/>ret</Name> as "secret".
  const split = await post("bob", update("assets", 2, { "sec<x/>ret": "split name" }));
  record("field-hidden-split-name-refused", !succeeded(split) && (await feature("assets", 2)).properties.secret === "secret 1", `bob sets "sec<x/>ret": status ${split.status}: ${short(split.text)}`);
  const ro = await post("bob", update("assets", 2, { status: "closed" }));
  record("field-readonly-refused", !succeeded(ro) && (await feature("assets", 2)).properties.status === "open", `bob sets read-only "status": status ${ro.status}: ${short(ro.text)}`);
  const ok = await post("bob", update("assets", 2, { name: "bob renamed" }));
  record("field-allowed", succeeded(ok) && (await feature("assets", 2)).properties.name === "bob renamed", `bob sets "name": ${short(ok.text)}`);
  const mixed = await post("bob", update("assets", 4, { name: "fine" }) + update("assets", 4, { secret: "not fine" }));
  record("field-refusal-covers-the-whole-transaction", !succeeded(mixed) && (await feature("assets", 4)).properties.name === "asset 3", `bob's transaction with one allowed and one refused update: status ${mixed.status}: ${short(mixed.text)}`);

  // Updating attributes and updating geometry are separate rights.
  const erinMove = await post("erin", update("assets", 2, { geometry: POINT(145.0, -37.9) }));
  record("move-only-may-move", succeeded(erinMove) && coords(await feature("assets", 2)) === "145.000,-37.900", `erin, who may only move, moves a feature: ${short(erinMove.text)}`);
  const erinName = await post("erin", update("assets", 2, { name: "erin" }));
  record("move-only-may-not-change", !succeeded(erinName) && (await feature("assets", 2)).properties.name === "bob renamed", `erin changes a name: status ${erinName.status}: ${short(erinName.text)}`);
  const bobMove = await post("bob", update("assets", 4, { geometry: POINT(145.0, -37.9) }));
  record("change-only-may-not-move", !succeeded(bobMove) && coords(await feature("assets", 4)) !== "145.000,-37.900", `bob, who may change but not move, moves his own: status ${bobMove.status}: ${short(bobMove.text)}`);

  // A layer whose WFS type name differs from its published name.
  const note = await post("alice", update("site_notes", 1, { name: "renamed note" }));
  record("type-name-allowed", succeeded(note), `alice updates "site notes" as site_notes: ${short(note.text)}`);
  const bobNote = await post("bob", update("site_notes", 1, { name: "bob's note" }));
  record("type-name-refused", !succeeded(bobNote), `bob, who may only see "site notes", updates it: status ${bobNote.status}: ${short(bobNote.text)}`);

  // Saves the request filter refuses before QGIS Server reads them.
  const kvp = await req({ SERVICE: "WFS", VERSION: "1.0.0", REQUEST: "Transaction", OPERATION: "Delete", TYPENAME: "assets", FEATUREID: "assets.6" }, { who: "alice" });
  record("transaction-as-get-refused", !succeeded(kvp) && (await feature("assets", 6)), `alice's GET transaction: status ${kvp.status}: ${short(kvp.text)}`);
  const doctype = await req({ SERVICE: "WFS", REQUEST: "Transaction" }, { who: "alice", body: `<?xml version="1.0"?><!DOCTYPE t [<!ENTITY e "secret">]>${tx(update("assets", 1, { name: "&e;" })).replace(/^<\?xml[^>]*>/, "")}` });
  record("transaction-doctype-refused", doctype.status === 403 && (await feature("assets", 1)).properties.name === "alice renamed", `a body with a DOCTYPE: status ${doctype.status}: ${short(doctype.text)}`);
  const native = await post("alice", `<wfs:Native vendorId="x" safeToIgnore="true">anything</wfs:Native>`);
  record("transaction-native-refused", native.status === 403, `a Native action: status ${native.status}: ${short(native.text)}`);
  const noRequest = await req({ SERVICE: "WFS" }, { who: "bob", body: tx(update("assets", 2, { secret: "via body only" })) });
  record("transaction-named-only-in-body", !succeeded(noRequest) && (await feature("assets", 2)).properties.secret === "secret 1", `bob's hidden-field update with REQUEST only in the body: status ${noRequest.status}: ${short(noRequest.text)}`);
  // A root named with a long s (U+017F) is not a Transaction to the plugin's lower(). QGIS
  // answers it as an unsupported request on 3.44 and 4.2; this fails if a later QGIS reads it as one.
  const folded = await req({ SERVICE: "WFS" }, { who: "bob", body: tx(update("assets", 2, { secret: "via a folded name" })).replaceAll("wfs:Transaction", "wfs:Tran\u017Faction") });
  record("transaction-folded-name-refused", !succeeded(folded) && (await feature("assets", 2)).properties.secret === "secret 1", `bob's hidden-field update with the root named Tran\u017Faction: status ${folded.status}: ${short(folded.text)}`);

  // Review finding on #3: a line break in a type name wrote a second, forged audit line.
  const forged = "x&#10;audit id=FORGED email=alice@example.org layer=assets action=delete outcome=done";
  const forgery = await req({ SERVICE: "WFS", REQUEST: "Transaction" }, { who: "bob", body: tx(`<wfs:Update typeName="${forged}"><wfs:Property><wfs:Name>a</wfs:Name></wfs:Property></wfs:Update>`) });
  record("audit-forgery-attempt", null, `bob's save with a line break in its type name: status ${forgery.status}: ${short(forgery.text)}`);

  // An add by someone allowed to add.
  const aliceIns = await post("alice", insert("assets", { name: "alice new", owner: "alice@example.org" }));
  record("insert-allowed", succeeded(aliceIns), `alice inserts: ${short(aliceIns.text)}`);
  const carolDel = await post("carol", del("assets", 8));
  record("delete-allowed", succeeded(carolDel) && !(await feature("assets", 8)), `carol deletes a feature: ${short(carolDel.text)}`);
}

async function checkServices() {
  for (const s of ["WCS", "WMTS"]) {
    const r = await req({ SERVICE: s, REQUEST: "GetCapabilities" }, { who: "alice" });
    record(`service-${s.toLowerCase()}-refused`, r.status === 403, `status ${r.status}: ${short(r.text)}`);
  }
  const api = await req({}, { who: "alice", path: "wfs3/collections/restricted/items.json" });
  record("service-ogcapi-refused", api.status === 403 && !/restricted 0/.test(api.text), `OGC API Features items: status ${api.status}: ${short(api.text)}`);
}

async function checkProjects() {
  // Each project's own rules: bob sees restricted in project B only.
  const [a, b] = [names((await gps("bob")).text), names((await gps("bob", { map: MAP_B })).text)];
  record("rules-per-project", !a.includes("restricted") && b.includes("restricted") && !b.includes("castlegate_rules_b"), `bob in project A sees [${a.join(", ")}], in B [${b.join(", ")}]`);
  const none = names((await gps("alice", { map: MAP_NONE })).text);
  record("no-rules-variable-serves-nothing", !["assets", "restricted"].some((n) => none.includes(n)), `alice in a project with no rules variable sees [${none.join(", ")}]`);
  const missing = names((await gps("alice", { map: MAP_MISSING })).text);
  record("missing-rules-layer-serves-nothing", !["assets", "restricted"].some((n) => missing.includes(n)), `alice in a project naming a missing rules layer sees [${missing.join(", ")}]`);
  // The shared capabilities cache stays off: bob after alice does not get alice's layers.
  await gps("alice");
  const after = names((await gps("bob")).text);
  record("cache-constant-key", !after.includes("restricted"), `bob after alice sees [${after.join(", ")}]`);
}

// install/common/check.py, run as IT would run it, against this server.
const check = (extra = [], env = {}) => run("python3", [join(root, "install", "common", "check.py"), `${BASE}${OWS}`, "--map", MAP, ...extra], { allowFail: true, env });

async function checkProfile() {
  const plain = check();
  record("check-loaded", plain.status === 0 && /Castlegate is loaded/.test(plain.stdout), `check.py with no token: exit ${plain.status}: ${plain.stdout.trim().replace(/\n/g, " | ")}`);
  const signed = check([], { CASTLEGATE_TOKEN: tok("carol") });
  record("check-signed-in", signed.status === 0 && /the rules load from castlegate_rules: 11 rows/.test(signed.stdout), `check.py with carol's token: exit ${signed.status}: ${signed.stdout.trim().replace(/\n/g, " | ").slice(0, 400)}`);
  const refused = check([], { CASTLEGATE_TOKEN: tok("alice", { aud: "api://other" }) });
  record("check-bad-token", refused.status === 1 && /the token is refused/.test(refused.stdout), `check.py with a token for another app: exit ${refused.status}`);
  const origin = check(["--origin", "https://app.groundwork.do"]);
  record("check-origin", origin.status === 0, `check.py --origin https://app.groundwork.do: exit ${origin.status}: ${origin.stdout.trim().replace(/\n/g, " | ").slice(0, 500)}`);
  const other = await fetch(`${BASE}${OWS}?SERVICE=CASTLEGATE&REQUEST=Status&MAP=${MAP}`, { method: "OPTIONS", headers: { Origin: "https://elsewhere.example", "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "authorization" } });
  const cors = [...other.headers.keys()].filter((k) => k.startsWith("access-control-"));
  record("preflight-other-origin-refused", other.status === 403 && cors.length === 0, `a preflight from another origin: status ${other.status}, Access-Control headers [${cors.join(", ")}]`);
  const health = docker(["exec", "castlegate-server", "sh", "-c", `python3 /io/plugins/castlegate_server/tools/check.py http://localhost/ows/ --map "$CASTLEGATE_CHECK_MAP"`], { allowFail: true });
  record("profile-health-check", health.status === 0, `the image's health check command: exit ${health.status}: ${health.stdout.trim()}`);
}

function checkLogs() {
  const text = logs();
  const audit = text.split("\n").filter((l) => /audit id=/.test(l));
  record("audit-lines", audit.some((l) => /action=update/.test(l) && /outcome="done"/.test(l)) && audit.some((l) => /outcome="refused/.test(l)), `${audit.length} audit lines; e.g. ${short(audit.find((l) => /outcome="refused \(/.test(l)) ?? audit[0] ?? "")}`);
  const carried = text.split("\n").filter((l) => /FORGED/.test(l));
  const forged = carried.filter((l) => /Castlegate\[\d+\]: audit id=FORGED/.test(l) || /^\s*audit id=FORGED/.test(l));
  record("audit-not-forged", carried.length > 0 && forged.length === 0, `${forged.length} lines start a forged entry; the attempt sits inside ${carried.length} line(s) of bob's own, e.g. ${(carried.find((l) => /audit id=/.test(l)) ?? carried[0] ?? "").slice(0, 260)}`);
  const raised = text.split("\n").filter((l) => /failed, (answering no|refusing)|Traceback/.test(l));
  record("no-exceptions", raised.length === 0, `${raised.length} exception log lines${raised.length ? ": " + short(raised.slice(0, 2).join(" || ")) : ""}`);
  record("server-still-running", alive(), `QGIS Server container ${alive() ? "running" : "stopped"}`);
  record("loaded", /Castlegate.*loaded 0\.1\.0/.test(text), short(text.split("\n").find((l) => /loaded 0\.1\.0/.test(l)) ?? "no load line"));
}

async function main() {
  console.log(`work folder ${work}, image ${BASE_IMAGE}${MSSQL ? ", layers in SQL Server" : ""}`);
  await prepare();
  await startServer();
  const version = docker(["exec", "castlegate-server", "sh", "-c", "dpkg-query -W -f '${Version}' qgis-server 2>/dev/null"], { allowFail: true }).stdout.trim();
  record("qgis-version", null, `qgis-server ${version}`);
  await checkTokens();
  await checkStatus();
  await checkLayers();
  await checkFilters();
  await checkSaves();
  await checkServices();
  await checkProjects();
  await checkProfile();
  checkLogs();
  writeFileSync(join(work, "server-log.txt"), logs());

  console.log("\n# Stock QGIS Server, no plugin");
  await startServer({ plugin: false });
  const open = await gps(undefined);
  record("stock-serves-everyone", null, `with no plugin, a request with no token gets status ${open.status} and [${names(open.text).join(", ")}]`);
  const stockCheck = check();
  record("check-not-loaded", stockCheck.status === 1 && /NOT loaded/.test(stockCheck.stdout), `check.py against a server with no plugin: exit ${stockCheck.status}: ${stockCheck.stdout.trim()}`);
  const stockSplit = await post(undefined, update("assets", 2, { "sec<x/>ret": "split name" }));
  const afterSplit = (await getFeatures(undefined, "assets", { FEATUREID: "assets.2" })).json?.features?.[0]?.properties?.secret;
  record("stock-reads-split-name", null, `with no plugin, an update naming "sec<x/>ret" ${succeeded(stockSplit) ? "succeeded" : "failed"} and secret is now "${afterSplit}"`);

  docker(["rm", "-f", "castlegate-server", "castlegate-issuer", "castlegate-mssql"], { allowFail: true });
  docker(["network", "rm", NET], { allowFail: true });
  writeFileSync(join(work, "results.json"), JSON.stringify(results, null, 1));
  const failed = results.filter((r) => r.ok === false);
  console.log(`\n${results.filter((r) => r.ok).length} passed, ${failed.length} failed, ${results.filter((r) => r.ok === null).length} observations`);
  process.exitCode = failed.length ? 1 : 0;
}

main().catch((e) => {
  console.error(e);
  process.exitCode = 2;
});
