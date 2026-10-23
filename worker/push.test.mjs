// node worker/push.test.mjs - checks web push encryption against RFC 8291's
// own test vector, the VAPID signature, and the settle rules.
import assert from "node:assert/strict";
import { b64u, encrypt, vapidJwt, pickStatus, slipStatus, priceAlerts } from "./push.js";

const body = await encrypt("When I grow up, I want to be a watermelon",
  "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
  "BTBZMqHH6r4Tts7J_aSIgg",
  { salt: "DGv6ra1nlYgDCS1FRnbzlw", asPrivate: "yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw",
    asPublic: "BP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8" });
assert.equal(b64u.enc(body),
  // RFC 8291 section 5: header and ciphertext concatenated, then base64url-encoded
  "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN");

// VAPID: sign with a fresh key, verify with its public half.
const kp = await crypto.subtle.generateKey({ name: "ECDSA", namedCurve: "P-256" }, true, ["sign", "verify"]);
const jwk = await crypto.subtle.exportKey("jwk", kp.privateKey);
const pub = new Uint8Array(await crypto.subtle.exportKey("raw", kp.publicKey));
const jwt = await vapidJwt("https://fcm.googleapis.com", { VAPID_PUBLIC_KEY: b64u.enc(pub), VAPID_PRIVATE_KEY: jwk.d });
const [h, c, sig] = jwt.split(".");
assert.ok(await crypto.subtle.verify({ name: "ECDSA", hash: "SHA-256" }, kp.publicKey,
  b64u.dec(sig), new TextEncoder().encode(`${h}.${c}`)));
assert.equal(JSON.parse(new TextDecoder().decode(b64u.dec(c))).aud, "https://fcm.googleapis.com");

// Settle rules (mirror account.html).
assert.equal(pickStatus({ selection: "Home Win" }, null), "pending");
assert.equal(pickStatus({ selection: "Home Win" }, { outcome: "H" }), "won");
assert.equal(pickStatus({ selection: "Draw", league: "Premier League" }, { outcome: "D" }), "won");
assert.equal(pickStatus({ selection: "Home Win", league: "NFL" }, { outcome: "D" }), "void");
assert.equal(slipStatus(["won", "pending", "lost"]), "lost");
assert.equal(slipStatus(["won", "pending"]), "pending");
assert.equal(slipStatus(["won", "void"]), "won");
assert.equal(slipStatus(["won", "ungradable"]), "pending");
// Price alerts: only our side, only before kickoff, only once.
const now = Date.parse("2026-10-23T12:00:00Z");
const warn = [{ type: "price", text: "Price has moved against this pick since it was made (4 points)" }];
const served = new Map([["m1", { selection: "Home Win", warnings: warn }], ["m2", { selection: "Away Win", warnings: [] }]]);
const tp = (id, match_id, selection, kickoff_utc) => ({ id, user_id: "u", match_id, selection, kickoff_utc, home_team: "A", away_team: "B" });
const yes = () => true;
assert.equal(priceAlerts([tp(1, "m1", "Home Win", "2026-10-23 18:00")], served, now, yes).length, 1);
assert.equal(priceAlerts([tp(2, "m1", "Away Win", "2026-10-23 18:00")], served, now, yes).length, 0);  // other side
assert.equal(priceAlerts([tp(3, "m1", "Home Win", "2026-10-23 11:00")], served, now, yes).length, 0);  // kicked off
assert.equal(priceAlerts([tp(4, "m2", "Away Win", "2026-10-23 18:00")], served, now, yes).length, 0);  // no warning
assert.equal(priceAlerts([tp(5, "m1", "Home Win", "2026-10-23 18:00")], served, now, () => false).length, 0);  // already sent
console.log("push tests OK");
