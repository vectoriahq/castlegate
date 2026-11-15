// The test people, rules and tokens, shared by the Docker tests (run.mjs) and the IIS tests
// (tests/iis/run.mjs), so both check the same rules against the same people.

import { createSign, generateKeyPairSync } from "crypto";

export const TENANT = "9a9a9a9a-0000-4000-8000-000000000001";
// A version 2 Entra token's audience is the API's application (client) ID.
export const AUDIENCE = "c0c0c0c0-0000-4000-8000-0000000000c0";
export const SCOPE = "access";
export const G = "11111111-2222-3333-4444-555555555555";

export const people = {
  alice: { oid: "aaaaaaaa-0000-4000-8000-00000000000a", preferred_username: "alice@example.org" },
  bob: { oid: "bbbbbbbb-0000-4000-8000-00000000000b", preferred_username: "bob@example.org" },
  carol: { oid: "cccccccc-0000-4000-8000-00000000000c", preferred_username: "carol@example.org", groups: [G] },
  erin: { oid: "eeeeeeee-0000-4000-8000-00000000000e", preferred_username: "erin@example.org" },
  frank: { oid: "ffffffff-0000-4000-8000-00000000000f", preferred_username: "frank@example.org" },
};

const all = { can_read: true, can_insert: true, can_update_attributes: true, can_update_geometry: true, can_delete: true };
export const RULES = {
  a: [
    { who_type: "user", who: "alice@example.org", layer: "assets", ...all, edit_filter: '"owner" = {{email}}' },
    { who_type: "user", who: "alice@example.org", layer: "restricted", can_read: true },
    { who_type: "user", who: "alice@example.org", layer: "big", can_read: true, read_filter: '"owner" = {{email}}' },
    { who_type: "user", who: "alice@example.org", layer: "site notes", can_read: true, can_update_attributes: true },
    // A row naming the rules layer itself: the plugin must still never serve it.
    { who_type: "user", who: "alice@example.org", layer: "castlegate_rules", ...all },
    {
      who_type: "user", who: "bob@example.org", layer: "assets", can_read: true, can_update_attributes: true,
      read_filter: '"owner" = {{email}}', edit_filter: '"owner" = {{email}}', hidden_fields: "secret", readonly_fields: "status",
    },
    { who_type: "user", who: "bob@example.org", layer: "site notes", can_read: true },
    { who_type: "group", who: G, layer: "assets", can_read: true },
    // A group row granting deletes with no filter must not widen carol's changes.
    { who_type: "group", who: G, layer: "assets", can_delete: true },
    { who_type: "user", who: "carol@example.org", layer: "assets", can_update_attributes: true, edit_filter: '"owner" = {{email}}' },
    { who_type: "user", who: people.erin.oid, layer: "assets", can_read: true, can_update_geometry: true },
    { who_type: "role", who: "alice@example.org", layer: "big", ...all },
    { who_type: "user", who: "alice@example.org", layer: "big", ...all, read_filter: '"owner" = (' },
  ],
  b: [
    { who_type: "user", who: "bob@example.org", layer: "restricted", can_read: true },
    { who_type: "user", who: "bob@example.org", layer: "assets", can_read: true },
  ],
};

export const KID = "test-key-1";
export const b64 = (o) => Buffer.from(typeof o === "string" ? o : JSON.stringify(o)).toString("base64url");

/** A test signing key for an issuer, the tokens it signs, and the key set the stub issuer publishes. */
export function issuerKeys(issuer) {
  const signing = generateKeyPairSync("rsa", { modulusLength: 2048 });
  const stranger = generateKeyPairSync("rsa", { modulusLength: 2048 });
  function token(claims = {}, { key = signing.privateKey, header = {} } = {}) {
    const now = Math.floor(Date.now() / 1000);
    const body = { iss: issuer, aud: AUDIENCE, tid: TENANT, scp: SCOPE, iat: now, nbf: now - 10, exp: now + 3600, ver: "2.0", ...claims };
    for (const k of Object.keys(body)) if (body[k] === undefined) delete body[k];
    const input = `${b64({ alg: "RS256", typ: "JWT", kid: KID, ...header })}.${b64(body)}`;
    const signer = createSign("RSA-SHA256");
    signer.update(input);
    return `${input}.${signer.sign(key).toString("base64url")}`;
  }
  const jwks = { keys: [{ ...signing.publicKey.export({ format: "jwk" }), kid: KID, use: "sig", alg: "RS256" }] };
  return { stranger, token, tok: (who, extra) => token({ ...people[who], ...extra }), jwks };
}
