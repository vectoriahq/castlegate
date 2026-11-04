#!/usr/bin/env node
// Package 1 harness for the Castlegate spike. Needs Docker and the qgis/qgis-server:ltr image.
//
//   node spike/run.mjs <work folder>
//   CASTLEGATE_BACKEND=mssql node spike/run.mjs <work folder>
//
// It builds the test project into <work folder>, starts a stub OpenID Connect
// issuer and the QGIS Server with the plugin in containers, runs each check
// against http://localhost:8020 and prints one line per check.
//
// With CASTLEGATE_BACKEND=mssql the layers live in SQL Server instead of the GeoPackage:
// it builds the image in mssql/, which has Microsoft's ODBC driver, and starts Microsoft's
// SQL Server 2022 Developer container. Both accept Microsoft's licence terms.

import { spawnSync } from "child_process";
import { cpSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "fs";
import { createSign, generateKeyPairSync as genKeys, randomBytes } from "crypto";
import { dirname, join, resolve } from "path";
import { fileURLToPath } from "url";

const here = dirname(fileURLToPath(import.meta.url));
const work = resolve(process.argv[2] ?? join(here, ".work"));
const MSSQL = process.env.CASTLEGATE_BACKEND === "mssql";
const IMAGE = MSSQL ? "castlegate-qgis-mssql" : "qgis/qgis-server:ltr";
const MSSQL_IMAGE = "mcr.microsoft.com/mssql/server:2022-latest";
// A test password for this run's throwaway container, meeting SQL Server's complexity rule.
const SA_PASSWORD = `Cg_${randomBytes(12).toString("hex")}_A1`;
const MSSQL_URI = `dbname='castlegate' service='DSN=castlegate' host='castlegate-mssql' user='sa' password='${SA_PASSWORD}'`;
const NET = "castlegate-spike";
const PORT = 8020;
const BASE = `http://localhost:${PORT}/`;
const OWS = "ows/";
const TENANT = "9a9a9a9a-0000-4000-8000-000000000001";
const ISSUER = `http://castlegate-issuer:8000/${TENANT}/v2.0`;
const AUDIENCE = "api://castlegate-spike";
const SCOPE = "access";
const GROUP = "11111111-2222-3333-4444-555555555555";
const MAP = "/io/data/castlegate.qgs";
const MAP100 = "/io/data/castlegate-100.qgs";

const results = [];
function record(id, ok, detail) {
  results.push({ id, ok, detail });
  console.log(`${ok === null ? "INFO" : ok ? "PASS" : "FAIL"}  ${id}  ${detail}`);
}

function docker(args, { quiet = true, allowFail = false, env } = {}) {
  if (env) args = [args[0], ...Object.keys(env).flatMap((k) => ["-e", k]), ...args.slice(1)];
  const r = spawnSync("docker", args, { encoding: "utf-8", env: { ...process.env, ...env } });
  if (r.status !== 0 && !allowFail) throw new Error(`docker ${args.join(" ")}\n${r.stderr}${r.stdout}`);
  if (!quiet) process.stdout.write(r.stdout);
  return r;
}

// ---- keys, tokens and the stub issuer -------------------------------------------------

const signing = genKeys("rsa", { modulusLength: 2048 });
const stranger = genKeys("rsa", { modulusLength: 2048 });
const KID = "spike-key-1";

const b64 = (o) => Buffer.from(typeof o === "string" ? o : JSON.stringify(o)).toString("base64url");

function token(claims = {}, { key = signing.privateKey, header = {} } = {}) {
  const now = Math.floor(Date.now() / 1000);
  const body = {
    iss: ISSUER,
    aud: AUDIENCE,
    tid: TENANT,
    scp: SCOPE,
    iat: now,
    nbf: now - 10,
    exp: now + 3600,
    ver: "2.0",
    ...claims,
  };
  for (const k of Object.keys(body)) if (body[k] === undefined) delete body[k];
  const input = `${b64({ alg: "RS256", typ: "JWT", kid: KID, ...header })}.${b64(body)}`;
  const signer = createSign("RSA-SHA256");
  signer.update(input);
  return `${input}.${signer.sign(key).toString("base64url")}`;
}

const people = {
  alice: { oid: "aaaaaaaa-0000-4000-8000-00000000000a", preferred_username: "alice@example.org" },
  bob: { oid: "bbbbbbbb-0000-4000-8000-00000000000b", preferred_username: "bob@example.org" },
  carol: { oid: "cccccccc-0000-4000-8000-00000000000c", preferred_username: "carol@example.org", groups: [GROUP] },
};
const tok = (who, extra) => token({ ...people[who], ...extra });

function writeIssuer(dir) {
  const jwk = signing.publicKey.export({ format: "jwk" });
  const issuerDir = join(dir, TENANT, "v2.0", ".well-known");
  mkdirSync(issuerDir, { recursive: true });
  writeFileSync(
    join(issuerDir, "openid-configuration"),
    JSON.stringify({ issuer: ISSUER, jwks_uri: "http://castlegate-issuer:8000/keys.json" })
  );
  writeFileSync(join(dir, "keys.json"), JSON.stringify({ keys: [{ ...jwk, kid: KID, use: "sig", alg: "RS256" }] }));
}

// ---- containers ------------------------------------------------------------------------

const pristine = join(work, "pristine");
const live = join(work, "live");
const issuerDir = join(work, "issuer");
const rulesDir = join(work, "rules");

// T-SQL run in the SQL Server container; its own environment holds the password.
function sql(query, { db = "castlegate", allowFail = false } = {}) {
  return docker(
    ["exec", "castlegate-mssql", "sh", "-c", `SQLCMDPASSWORD="$MSSQL_SA_PASSWORD" /opt/mssql-tools18/bin/sqlcmd -C -b -I -S localhost -U sa -d ${db} -h -1 -W -Q "$Q"`],
    { allowFail, env: { Q: query } }
  );
}

async function startSqlServer() {
  docker(["build", "-q", "-t", IMAGE, join(here, "mssql")]);
  docker([
    "run", "-d", "--name", "castlegate-mssql", "--network", NET,
    "-e", "ACCEPT_EULA=Y", "-e", "MSSQL_PID=Developer", "-e", `MSSQL_SA_PASSWORD=${SA_PASSWORD}`, MSSQL_IMAGE,
  ]);
  for (let i = 0; i < 90 && sql("SELECT 1", { db: "master", allowFail: true }).status !== 0; i++) await new Promise((r) => setTimeout(r, 1000));
  sql("CREATE DATABASE castlegate", { db: "master" });
  const columns = "fid INT IDENTITY(1,1) PRIMARY KEY, name NVARCHAR(100), owner NVARCHAR(100), status NVARCHAR(20), secret NVARCHAR(100), geom geometry";
  for (const t of ["assets", "restricted", "big"]) {
    sql(`CREATE TABLE dbo.${t} (${columns}); CREATE SPATIAL INDEX ${t}_geom ON dbo.${t}(geom) WITH (BOUNDING_BOX = (144, -39, 146, -37));`);
  }
  const v = sql("SELECT @@VERSION", { db: "master" }).stdout.trim().split("\n")[0];
  record("mssql-version", null, v);
}

// Puts the assets table back as it was built, so each server mode starts from the same rows.
function resetSqlServer() {
  sql("DELETE FROM dbo.assets; SET IDENTITY_INSERT dbo.assets ON; INSERT INTO dbo.assets (fid, name, owner, status, secret, geom) SELECT fid, name, owner, status, secret, geom FROM dbo.assets_pristine; SET IDENTITY_INSERT dbo.assets OFF; DBCC CHECKIDENT ('dbo.assets', RESEED) WITH NO_INFOMSGS;");
}

async function prepare() {
  rmSync(work, { recursive: true, force: true });
  mkdirSync(pristine, { recursive: true });
  docker(["rm", "-f", "castlegate-issuer", "castlegate-spike", "castlegate-mssql"], { allowFail: true });
  docker(["network", "rm", NET], { allowFail: true });
  docker(["network", "create", NET]);
  if (MSSQL) await startSqlServer();
  docker([
    "run", "--rm", "--network", NET, "-e", "QT_QPA_PLATFORM=offscreen",
    ...(MSSQL ? ["-e", `CASTLEGATE_MSSQL=${MSSQL_URI}`] : []),
    "-v", `${pristine}:/io/data`, "-v", `${here}:/work:ro`,
    "--entrypoint", "python3", IMAGE, "/work/build_project.py",
  ]);
  if (MSSQL) {
    sql("SELECT * INTO dbo.assets_pristine FROM dbo.assets");
    const counts = sql("SELECT (SELECT COUNT(*) FROM dbo.assets), (SELECT COUNT(*) FROM dbo.restricted), (SELECT COUNT(*) FROM dbo.big)").stdout.trim();
    record("mssql-rows", null, `rows in assets, restricted, big: ${counts.replace(/\s+/g, ", ")}`);
  }
  writeIssuer(issuerDir);
  const rules = JSON.parse(readFileSync(join(here, "rules.json"), "utf-8"));
  for (let i = 0; i < 100; i++) {
    rules.rows.push({ who: { email: "alice@example.org" }, layer: `layer_${String(i).padStart(3, "0")}`, may: ["read"], view: null, edit: null });
  }
  mkdirSync(rulesDir, { recursive: true });
  writeFileSync(join(rulesDir, "rules.json"), JSON.stringify(rules, null, 1));
  docker([
    "run", "-d", "--name", "castlegate-issuer", "--network", NET, "-v", `${issuerDir}:/srv:ro`,
    "--entrypoint", "python3", IMAGE, "-m", "http.server", "8000", "--directory", "/srv",
  ]);
}

async function startServer(mode = {}) {
  docker(["rm", "-f", "castlegate-spike"], { allowFail: true });
  rmSync(live, { recursive: true, force: true });
  cpSync(pristine, live, { recursive: true });
  if (MSSQL) resetSqlServer();
  const env = {
    QGIS_SERVER_LOG_LEVEL: "0",
    CASTLEGATE_ISSUER: ISSUER,
    CASTLEGATE_AUDIENCE: AUDIENCE,
    CASTLEGATE_SCOPE: SCOPE,
    CASTLEGATE_TENANT: TENANT,
    CASTLEGATE_RULES: "/io/rules/rules.json",
    ...(mode.env ?? {}),
  };
  const args = ["run", "-d", "--name", "castlegate-spike", "--network", NET, "-p", `${PORT}:80`, "-v", `${live}:/io/data`, "-v", `${rulesDir}:/io/rules:ro`];
  if (mode.plugin !== false) args.push("-v", `${join(here, "plugin")}:/io/plugins:ro`);
  for (const [k, v] of Object.entries(env)) args.push("-e", `${k}=${v}`);
  args.push(IMAGE);
  docker(args);
  // Files on a Windows bind mount show as root's with mode 644; QGIS Server runs as www-data.
  docker(["exec", "castlegate-spike", "sh", "-c", "chmod a+rw /io/data/*.gpkg"]);
  for (let i = 0; i < 60; i++) {
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
  const r = docker(["logs", "castlegate-spike"], { allowFail: true });
  return (r.stdout ?? "") + (r.stderr ?? "");
};
const alive = () => docker(["inspect", "-f", "{{.State.Running}}", "castlegate-spike"], { allowFail: true }).stdout.trim() === "true";

// ---- requests --------------------------------------------------------------------------

async function req(params, { who, auth, body, map = MAP, path = OWS } = {}) {
  const url = new URL(BASE + path);
  if (map) url.searchParams.set("MAP", map);
  for (const [k, v] of Object.entries(params)) url.searchParams.set(k, v);
  const headers = {};
  const t = auth ?? (who ? tok(who) : undefined);
  if (t) headers.Authorization = `Bearer ${t}`;
  if (body) headers["Content-Type"] = "text/xml";
  const started = performance.now();
  const r = await fetch(url, { method: body ? "POST" : "GET", headers, body });
  const buf = Buffer.from(await r.arrayBuffer());
  return { status: r.status, headers: r.headers, text: buf.toString("utf-8"), buf, ms: performance.now() - started };
}

const gps = (who, opts) => req({ SERVICE: "WMS", VERSION: "1.3.0", REQUEST: "GetProjectSettings" }, { who, ...opts });
const wfsCaps = (who) => req({ SERVICE: "WFS", VERSION: "1.1.0", REQUEST: "GetCapabilities" }, { who });
async function getFeatures(who, layer, extra = {}) {
  const r = await req({ SERVICE: "WFS", VERSION: "1.1.0", REQUEST: "GetFeature", TYPENAME: layer, OUTPUTFORMAT: "application/vnd.geo+json", ...extra }, { who });
  try {
    return { ...r, json: JSON.parse(r.text) };
  } catch {
    return { ...r, json: null };
  }
}
const layerNamesIn = (xml) => [...xml.matchAll(/<Name>([^<]+)<\/Name>/g)].map((m) => m[1]);
const short = (s) => {
  const m = s.match(/<(?:\w+:)?(?:Message|ServiceException[^>]*|ServerException)>([^<]*)</);
  return (m ? `[${/SUCCESS/.test(s) ? "SUCCESS" : /FAILED/.test(s) ? "FAILED" : "exception"}] ${m[1]}` : s).replace(/\s+/g, " ").slice(0, 200);
};

const NS = 'xmlns:wfs="http://www.opengis.net/wfs" xmlns:ogc="http://www.opengis.net/ogc" xmlns:gml="http://www.opengis.net/gml" xmlns:qgs="http://www.qgis.org/gml"';
const tx = (inner) => `<?xml version="1.0"?><wfs:Transaction service="WFS" version="1.0.0" ${NS}>${inner}</wfs:Transaction>`;
const update = (layer, fid, props) =>
  `<wfs:Update typeName="qgs:${layer}">${Object.entries(props)
    .map(([k, v]) => `<wfs:Property><wfs:Name>${k}</wfs:Name><wfs:Value>${v}</wfs:Value></wfs:Property>`)
    .join("")}<ogc:Filter><ogc:FeatureId fid="${layer}.${fid}"/></ogc:Filter></wfs:Update>`;
const insert = (layer, props) =>
  `<wfs:Insert><qgs:${layer}><qgs:geometry><gml:Point srsName="EPSG:4326"><gml:coordinates>144.99,-37.81</gml:coordinates></gml:Point></qgs:geometry>${Object.entries(props)
    .map(([k, v]) => `<qgs:${k}>${v}</qgs:${k}>`)
    .join("")}</qgs:${layer}></wfs:Insert>`;
const del = (layer, fid) => `<wfs:Delete typeName="qgs:${layer}"><ogc:Filter><ogc:FeatureId fid="${layer}.${fid}"/></ogc:Filter></wfs:Delete>`;
const post = (who, inner) => req({ SERVICE: "WFS", VERSION: "1.0.0", REQUEST: "Transaction" }, { who, body: tx(inner) });
const succeeded = (r) => r.status === 200 && /SUCCESS/.test(r.text) && !/FAILED|ServiceException/.test(r.text);
const featureById = async (layer, fid) => (await getFeatures("alice", layer, { FEATUREID: `${layer}.${fid}` })).json?.features?.[0];

// ---- checks -----------------------------------------------------------------------------

async function checksDefault() {
  // Tokens
  const none = await gps();
  record("no-token", none.status === 401 && !/restricted|assets/.test(none.text), `status ${none.status}: ${short(none.text)}`);
  record("no-token-www-authenticate", none.headers.get("www-authenticate") === "Bearer", `WWW-Authenticate: ${none.headers.get("www-authenticate")}`);
  const bad = {
    "bad-signature": token(people.alice, { key: stranger.privateKey }),
    "alg-none": `${b64({ alg: "none", typ: "JWT", kid: KID })}.${b64({ ...people.alice, iss: ISSUER, aud: AUDIENCE, tid: TENANT, scp: SCOPE, exp: Math.floor(Date.now() / 1000) + 600 })}.`,
    "unknown-kid": token(people.alice, { header: { kid: "other" } }),
    expired: tok("alice", { exp: Math.floor(Date.now() / 1000) - 3600 }),
    "not-yet-valid": tok("alice", { nbf: Math.floor(Date.now() / 1000) + 3600 }),
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
  record("token-good", good.status === 200 && /<Name>assets<\/Name>/.test(good.text), `status ${good.status}, ${good.ms.toFixed(0)} ms`);

  // Status answer
  const s0 = await req({ SERVICE: "CASTLEGATE", REQUEST: "Status" }, { map: MAP });
  record("status-no-token", s0.status === 401 && s0.headers.get("www-authenticate") === "Bearer", `status ${s0.status}, WWW-Authenticate ${s0.headers.get("www-authenticate")}: ${short(s0.text)}`);
  const s1 = await req({ SERVICE: "CASTLEGATE", REQUEST: "Status" }, { who: "carol", map: MAP });
  record("status-signed-in", s1.status === 200 && /carol@example.org/.test(s1.text), `status ${s1.status}: ${short(s1.text)}`);
  const unknown = await req({ SERVICE: "NOSUCH", REQUEST: "Status" }, { who: "alice", map: MAP });
  record("unknown-service", null, `a service the plugin refuses answers status ${unknown.status}: ${short(unknown.text)}`);

  // Hidden layer
  const bobGps = await gps("bob");
  const aliceGps = await gps("alice");
  record(
    "hidden-layer-getprojectsettings",
    !layerNamesIn(bobGps.text).includes("restricted") && layerNamesIn(aliceGps.text).includes("restricted"),
    `bob sees [${layerNamesIn(bobGps.text).join(", ")}], alice sees [${layerNamesIn(aliceGps.text).join(", ")}]`
  );
  const bobCaps = await wfsCaps("bob");
  const aliceCaps = await wfsCaps("alice");
  record(
    "hidden-layer-wfs-capabilities",
    !/<Name>restricted<\/Name>/.test(bobCaps.text) && /<Name>restricted<\/Name>/.test(aliceCaps.text),
    `bob: restricted ${/<Name>restricted<\/Name>/.test(bobCaps.text) ? "present" : "absent"}; alice: ${/<Name>restricted<\/Name>/.test(aliceCaps.text) ? "present" : "absent"}`
  );
  record("hidden-field-schema", null, `bob's GetProjectSettings ${/secret/.test(bobGps.text) ? "still names" : "does not name"} the hidden field "secret"`);
  const dft = await req({ SERVICE: "WFS", VERSION: "1.1.0", REQUEST: "DescribeFeatureType", TYPENAME: "assets" }, { who: "bob" });
  record("hidden-field-describefeaturetype", null, `bob's DescribeFeatureType ${/name="secret"/.test(dft.text) ? "still names" : "does not name"} "secret"`);
  record("wfs-capabilities-edit-ops", null, `bob's WFS capabilities ${/Insert|<Operation>Insert/.test(bobCaps.text) ? "still advertise" : "do not advertise"} Insert for assets, which he may not do`);

  // Feature filter: WFS GetFeature
  const bobF = await getFeatures("bob", "assets");
  const aliceF = await getFeatures("alice", "assets");
  const owners = (bobF.json?.features ?? []).map((f) => f.properties.owner);
  record(
    "filter-wfs-getfeature",
    bobF.json?.features?.length === 4 && owners.every((o) => o === "bob@example.org") && aliceF.json?.features?.length === 8,
    `bob gets ${bobF.json?.features?.length} (owners ${[...new Set(owners)].join(",")}), alice gets ${aliceF.json?.features?.length}`
  );
  const bobFields = Object.keys(bobF.json?.features?.[0]?.properties ?? {});
  record("hidden-field-values-getfeature", !bobFields.includes("secret"), `bob's feature fields: ${bobFields.join(", ")}`);

  // Feature filter: WMS GetMap. Asset 0 (alice's) is at 144.96,-37.81.
  const map = (who, bbox) =>
    req({ SERVICE: "WMS", VERSION: "1.3.0", REQUEST: "GetMap", LAYERS: "assets", STYLES: "", CRS: "EPSG:4326", BBOX: bbox, WIDTH: "64", HEIGHT: "64", FORMAT: "image/png", TRANSPARENT: "true" }, { who });
  const aliceBox = "-37.8105,144.9595,-37.8095,144.9605";
  const emptyBox = "-37.0005,144.0,-36.9995,144.001";
  const [bobOnAlice, aliceOnAlice, bobEmpty] = [await map("bob", aliceBox), await map("alice", aliceBox), await map("bob", emptyBox)];
  record(
    "filter-wms-getmap",
    bobOnAlice.status === 200 && bobOnAlice.buf.equals(bobEmpty.buf) && !aliceOnAlice.buf.equals(bobEmpty.buf),
    `around alice's asset: bob's image ${bobOnAlice.buf.equals(bobEmpty.buf) ? "equals" : "differs from"} an empty tile; alice's ${aliceOnAlice.buf.equals(bobEmpty.buf) ? "equals" : "differs from"} it (${bobOnAlice.headers.get("content-type")})`
  );

  // Feature filter: GetFeatureInfo at the centre of the same box.
  const gfi = (who) =>
    req({ SERVICE: "WMS", VERSION: "1.3.0", REQUEST: "GetFeatureInfo", LAYERS: "assets", QUERY_LAYERS: "assets", STYLES: "", CRS: "EPSG:4326", BBOX: aliceBox, WIDTH: "64", HEIGHT: "64", I: "32", J: "32", INFO_FORMAT: "application/json", FEATURE_COUNT: "5", FI_POINT_TOLERANCE: "16" }, { who });
  const gb = await gfi("bob");
  const ga = await gfi("alice");
  const nb = JSON.parse(gb.text).features?.length;
  const na = JSON.parse(ga.text).features?.length;
  record("filter-wms-getfeatureinfo", nb === 0 && na >= 1, `bob gets ${nb} features at alice's asset, alice gets ${na}`);

  // Refused add, change and delete come back as a message.
  const ins = await post("bob", insert("assets", { name: "new", owner: "bob@example.org" }));
  record("refused-insert", !succeeded(ins), `bob insert: status ${ins.status}: ${short(ins.text)}`);
  const dl = await post("bob", del("assets", 2));
  record("refused-delete", !succeeded(dl) && (await featureById("assets", 2)), `bob delete: status ${dl.status}: ${short(dl.text)}`);
  const up = await post("carol", update("assets", 2, { name: "carol was here" }));
  record("refused-update", !succeeded(up) && (await featureById("assets", 2)).properties.name === "asset 1", `carol update: status ${up.status}: ${short(up.text)}`);

  // allowToEdit: alice may see everything but edit only her own.
  const notHers = await post("alice", update("assets", 2, { name: "alice edits bob's" }));
  record("allowtoedit-other-feature", !succeeded(notHers) && (await featureById("assets", 2)).properties.name === "asset 1", `alice updates bob's feature: status ${notHers.status}: ${short(notHers.text)}`);
  const hers = await post("alice", update("assets", 1, { name: "alice renamed" }));
  record("allowtoedit-own-feature", succeeded(hers) && (await featureById("assets", 1)).properties.name === "alice renamed", `alice updates her own: ${short(hers.text)}`);
  const giveAway = await post("alice", update("assets", 3, { owner: "bob@example.org" }));
  record("allowtoedit-moves-out-of-scope", !succeeded(giveAway) && (await featureById("assets", 3)).properties.owner === "alice@example.org", `alice sets owner to bob: status ${giveAway.status}: ${short(giveAway.text)}`);

  // Hidden and read-only fields on a save, with the request filter's field check on.
  const hidden = await post("bob", update("assets", 2, { secret: "bob wrote this" }));
  record("field-check-hidden-refused", !succeeded(hidden) && (await featureById("assets", 2)).properties.secret === "secret 1", `bob sets hidden "secret": status ${hidden.status}: ${short(hidden.text)}`);
  const ro = await post("bob", update("assets", 2, { status: "closed" }));
  record("field-check-readonly-refused", !succeeded(ro) && (await featureById("assets", 2)).properties.status === "open", `bob sets read-only "status": status ${ro.status}: ${short(ro.text)}`);
  const ok = await post("bob", update("assets", 2, { name: "bob renamed" }));
  record("field-check-allowed-field", succeeded(ok), `bob sets "name": ${short(ok.text)}`);
  const kvp = await req({ SERVICE: "WFS", VERSION: "1.0.0", REQUEST: "Transaction", OPERATION: "Delete", TYPENAME: "assets", FEATUREID: "assets.4" }, { who: "bob" });
  record("transaction-as-get-refused", !succeeded(kvp) && (await featureById("assets", 4)), `bob's GET transaction: status ${kvp.status}: ${short(kvp.text)}`);

  // Services outside the allowlist.
  for (const s of ["WCS", "WMTS"]) {
    const r = await req({ SERVICE: s, REQUEST: "GetCapabilities" }, { who: "alice" });
    record(`service-${s.toLowerCase()}-refused`, r.status >= 400, `status ${r.status}: ${short(r.text)}`);
  }
  const api = await req({}, { who: "bob", map: MAP, path: "wfs3/collections/restricted/items.json" });
  record("service-ogcapi-refused", api.status >= 400 && !/restricted 0/.test(api.text), `OGC API Features items as bob: status ${api.status}: ${short(api.text)}`);
  const landing = await req({}, { who: "bob", map: null, path: "wfs3.json" });
  record("service-ogcapi-landing", null, `OGC API landing page: status ${landing.status}: ${short(landing.text)}`);

  // Cache: with the constant key, bob after alice still does not get "restricted".
  await gps("alice");
  const bobAfter = await gps("bob");
  record("cache-constant-key", !layerNamesIn(bobAfter.text).includes("restricted"), `bob after alice sees [${layerNamesIn(bobAfter.text).join(", ")}]`);

  // Audit lines.
  const audit = logs().split("\n").filter((l) => /audit oid=/.test(l));
  record("audit-lines", audit.length > 0, `${audit.length} audit lines; first: ${short(audit[0] ?? "")}`);
  record("worker-processes", null, `processes: ${docker(["exec", "castlegate-spike", "sh", "-c", "pidof qgis_mapserv.fcgi"], { allowFail: true }).stdout.trim()}`);
}

async function timing(label, fn, n = 10) {
  const times = [];
  for (let i = 0; i < n; i++) times.push((await fn()).ms);
  times.sort((a, b) => a - b);
  return { label, median: times[Math.floor(n / 2)], min: times[0], max: times[n - 1] };
}

async function checksPerformance(pluginOn) {
  const who = pluginOn ? "alice" : undefined;
  const t = await timing("GetProjectSettings, 100 layers", () => gps(who, { map: MAP100 }));
  const first = await gps(who, { map: MAP100 });
  record(`perf-gps-100-${pluginOn ? "plugin" : "stock"}`, null, `${t.label}: median ${t.median.toFixed(0)} ms (min ${t.min.toFixed(0)}, max ${t.max.toFixed(0)}), ${layerNamesIn(first.text).length} names, ${(first.buf.length / 1024).toFixed(0)} KB`);
  const t3 = await timing("GetProjectSettings, 3 layers", () => gps(who));
  record(`perf-gps-3-${pluginOn ? "plugin" : "stock"}`, null, `${t3.label}: median ${t3.median.toFixed(0)} ms`);
  if (pluginOn) {
    const compiled = await timing("big, \"owner\" = alice", () => getFeatures("alice", "big"), 5);
    const a = await getFeatures("alice", "big");
    record("perf-filter-compiled", null, `${compiled.label}: median ${compiled.median.toFixed(0)} ms, ${a.json?.features?.length} features`);
    const uncompiled = await timing("big, regexp_match for carol", () => getFeatures("carol", "big"), 5);
    const c = await getFeatures("carol", "big");
    record("perf-filter-regexp", null, `${uncompiled.label}: median ${uncompiled.median.toFixed(0)} ms, ${c.json?.features?.length} features`);
  } else {
    const all = await timing("big, every feature, no filter", () => getFeatures(undefined, "big", { MAXFEATURES: "1" }), 5);
    record("perf-big-stock-one", null, `${all.label} (MAXFEATURES=1): median ${all.median.toFixed(0)} ms`);
    const filtered = await timing("big, EXP_FILTER owner = alice", () => getFeatures(undefined, "big", { EXP_FILTER: "\"owner\" = 'alice@example.org'" }), 5);
    record("perf-big-stock-expfilter", null, `${filtered.label}: median ${filtered.median.toFixed(0)} ms`);
  }
}

async function main() {
  console.log(`work folder ${work}`);
  await prepare();

  console.log("\n# Plugin, defaults");
  await startServer({ env: { CPL_DEBUG: "ON" } });
  console.log(short(logs().split("\n").filter((l) => /Castlegate|plugin/i.test(l)).join(" | ")));
  await checksDefault();
  await checksPerformance(true);
  if (MSSQL) {
    // What QGIS sent to SQL Server for the big layer, from SQL Server's own record of queries run.
    const q = sql("SELECT TOP 6 CAST(qs.execution_count AS varchar) + ' x ' + REPLACE(REPLACE(LEFT(t.text, 400), CHAR(13), ' '), CHAR(10), ' ') FROM sys.dm_exec_query_stats qs CROSS APPLY sys.dm_exec_sql_text(qs.sql_handle) t WHERE t.text LIKE '%big%' AND t.text LIKE '%owner%' AND t.text NOT LIKE '%dm_exec%' ORDER BY qs.last_execution_time DESC").stdout.trim();
    record("pushdown-mssql", null, `queries SQL Server ran on big naming owner: ${q.replace(/\n/g, " || ") || "none"}`);
  } else {
    const sqlLines = logs().split("\n").filter((l) => /big|SELECT/i.test(l) && /OGR|GPKG|SQLite|sql/i.test(l));
    record("pushdown-log", null, `${sqlLines.length} OGR/SQL log lines mention big; samples: ${short(sqlLines.slice(-3).join(" || "))}`);
  }
  writeFileSync(join(work, "log-default.txt"), logs());

  console.log("\n# Plugin, empty cache key");
  await startServer({ env: { CASTLEGATE_CACHE_KEY: "empty" } });
  await gps("alice");
  const leaked = await gps("bob");
  record("cache-empty-key-leaks", layerNamesIn(leaked.text).includes("restricted"), `with an empty key, bob after alice sees [${layerNamesIn(leaked.text).join(", ")}]`);
  const cached = await timing("GetProjectSettings, 100 layers", () => gps("alice", { map: MAP100 }));
  record("perf-gps-100-plugin-cache-on", null, `with the plugin and the shared cache in use: median ${cached.median.toFixed(0)} ms`);
  const caps1 = await wfsCaps("alice");
  const caps2 = await wfsCaps("bob");
  record("cache-empty-key-wfs", null, `WFS capabilities with an empty key: bob after alice ${/<Name>restricted<\/Name>/.test(caps2.text) ? "gets" : "does not get"} restricted (alice ${/<Name>restricted<\/Name>/.test(caps1.text) ? "did" : "did not"})`);

  console.log("\n# Plugin, field check off (stock save behaviour)");
  await startServer({ env: { CASTLEGATE_FIELD_CHECK: "off" } });
  const stock = await post("bob", update("assets", 2, { secret: "bob wrote this" }));
  const after = await featureById("assets", 2);
  record("field-check-off-hidden-saved", succeeded(stock) && after.properties.secret === "bob wrote this", `bob sets hidden "secret" with the check off: ${short(stock.text)}; value now "${after.properties.secret}"`);
  const stockIns = await post("alice", insert("assets", { name: "alice new", owner: "alice@example.org", secret: "set on insert" }));
  record("field-check-off-insert", null, `alice inserts with every field: ${short(stockIns.text)}`);

  for (const where of ["layerPermissions", "layerFilterExpression", "authorizedLayerAttributes", "onRequestReady"]) {
    console.log(`\n# Plugin raising in ${where}`);
    await startServer({ env: { CASTLEGATE_RAISE: where } });
    const safe = async (fn) => {
      try {
        return await fn();
      } catch (e) {
        return { status: `no answer (${e.cause?.code ?? e.message})`, text: "", json: null };
      }
    };
    const r = await safe(() => gps("bob"));
    const f = await safe(() => getFeatures("bob", "assets"));
    const err = logs().split("\n").filter((l) => /spike: raised|Traceback|RuntimeError/.test(l));
    record(
      `raise-${where}`,
      null,
      `GetProjectSettings status ${r.status}, names [${layerNamesIn(r.text).join(", ")}]; GetFeature status ${f.status}, ${f.json?.features?.length ?? "no"} features${f.json ? `, fields ${Object.keys(f.json.features?.[0]?.properties ?? {}).join(",")}` : `: ${short(f.text)}`}; container ${alive() ? "running" : "stopped"}; ${err.length} error log lines: ${short(err.slice(0, 2).join(" || "))}`
    );
  }

  console.log("\n# Stock, no plugin");
  await startServer({ plugin: false });
  await checksPerformance(false);
  const open = await gps(undefined);
  record("stock-no-token", null, `stock server, no token: status ${open.status}, names [${layerNamesIn(open.text).join(", ")}]`);

  docker(["rm", "-f", "castlegate-spike", "castlegate-issuer", "castlegate-mssql"], { allowFail: true });
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
