// node tools/calc.test.mjs - checks the Tools-tab calculators' maths.
import assert from "node:assert/strict";
import { createRequire } from "node:module";
const C = createRequire(import.meta.url)("../webapp/js/calc.js");
const near = (a, b) => assert.ok(Math.abs(a - b) < 1e-9, `${a} != ${b}`);

near(C.toDecimal("2.5"), 2.5);
near(C.toDecimal("3/2"), 2.5);
near(C.toDecimal("+150"), 2.5);
near(C.toDecimal("-200"), 1.5);
for (const bad of ["1", "0.9", "abc", "", "-50", "+99", "3/0"]) assert.ok(isNaN(C.toDecimal(bad)), bad);

assert.deepEqual(
    (({decimal, fractional, american}) => ({decimal, fractional, american}))(C.convert("2.5")),
    {decimal: 2.5, fractional: "3/2", american: "+150"});
assert.equal(C.convert("1.5").american, "-200");
assert.equal(C.convert("1.5").fractional, "1/2");
near(C.convert("4").implied, 0.25);

const r = C.returns(1000, ["1.5", "2"]);
near(r.totalOdds, 3); near(r.payout, 3000); near(r.profit, 2000);
assert.equal(C.returns(1000, ["1.5", "x"]), null);

// Backed 1000 at 3.00, other side now 1.50: hedge 2000, 3000 back either way, profit 0.
const h = C.hedge(1000, "3", "1.5");
near(h.hedgeStake, 2000); near(h.payout, 3000); near(h.profit, 0);
assert.ok(C.hedge(1000, "2", "1.5").profit < 0);   // 1/2 + 1/1.5 > 1: locked loss
assert.ok(C.hedge(1000, "2", "2.5").profit > 0);   // 1/2 + 1/2.5 < 1: locked profit

// Dutching 2.0 and 4.0 with 1000: book 0.75, stakes 666.67 / 333.33, each pays 1333.33.
const d = C.dutch(1000, ["2", "4"]);
near(d.book, 0.75); near(d.stakes[0] * 2, d.stakes[1] * 4); near(d.payout, 1000 / 0.75);
assert.ok(C.dutch(1000, ["1.9", "1.9"]).profit < 0); // over-round: loses either way
// Kelly: 50% at 3.00 -> f = (1.5 - 1) / 2 = 25%; 30% at 3.00 -> negative, no bet.
const k = C.kelly(10000, 50, "3");
near(k.fraction, 0.25); near(k.full, 2500); near(k.half, 1250);
assert.ok(C.kelly(10000, 30, "3").fraction < 0 && C.kelly(10000, 30, "3").full === 0);
assert.equal(C.kelly(10000, 100, "3"), null);
console.log("calc tests OK");
