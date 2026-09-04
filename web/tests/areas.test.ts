import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import test from "node:test";
import { fileURLToPath } from "node:url";
import {
  AREAS_STORAGE_KEY, COUNTRY_OPTIONS, MAX_FAVORITE_COUNTRIES, getScopeCountries, getScopeLabel,
  normalizeScopeId, readFavoriteCountries, scopeCountryCode, writeFavoriteCountries,
  type AreaStorage, type ScopeId,
} from "../lib/areas";

class MemoryStorage implements AreaStorage {
  readonly values = new Map<string, string>();
  writes = 0;
  getItem(key: string) { return this.values.get(key) ?? null; }
  setItem(key: string, value: string) { this.values.set(key, value); this.writes++; }
}

test("country options are an explicit complete ISO alpha-2 set plus Kosovo, not arbitrary Intl regions", () => {
  assert.equal(COUNTRY_OPTIONS.length, 250);
  assert.equal(new Set(COUNTRY_OPTIONS.map(({ code }) => code)).size, 250);
  assert.ok(COUNTRY_OPTIONS.every(({ code, label }) => /^[A-Z]{2}$/.test(code) && label.length > 0));
  assert.deepEqual(COUNTRY_OPTIONS.map(({ code }) => code), COUNTRY_OPTIONS.map(({ code }) => code).sort());
  for (const code of ["PL", "TR", "FR", "GB", "JP", "NO", "XK", "BQ", "AQ", "GP", "UM"]) {
    assert.ok(COUNTRY_OPTIONS.some((country) => country.code === code), code);
    const normalized = normalizeScopeId(`country:${code}`)!;
    assert.equal(scopeCountryCode(normalized), code);
    assert.deepEqual(getScopeCountries(normalized), [code]);
  }
  for (const code of ["ZZ", "EU", "UN", "UK", "XA", "XB", "QO", "AC", "TA", "IC"]) {
    assert.equal(normalizeScopeId(`country:${code}`), null, code);
  }
  assert.ok(Object.isFrozen(COUNTRY_OPTIONS));
  assert.ok(COUNTRY_OPTIONS.every(Object.isFrozen));
});

test("scope normalization gives each supported country a canonical spelling and preserves legacy links", () => {
  assert.equal(normalizeScopeId("world"), "world");
  assert.equal(normalizeScopeId("europe"), "europe");
  assert.equal(normalizeScopeId("poland"), "poland");
  assert.equal(normalizeScopeId("turkey"), "country:TR");
  assert.equal(normalizeScopeId("country:tr"), "country:TR");
  assert.equal(normalizeScopeId("country:pL"), "poland");
  assert.equal(normalizeScopeId("country:xk"), "country:XK");
  for (const value of [null, undefined, 0, [], {}, "", "WORLD", " country:PL", "country:PL ", "country:PL\n", "country:ŁÓ", "country:FR,GP", "country:USA", "COUNTRY:JP", "__proto__", "country:__proto__"]) {
    assert.equal(normalizeScopeId(value), null, String(value));
  }
});

test("scope helpers distinguish world, Europe, Poland and country-only scopes without inferred territory", () => {
  assert.equal(getScopeCountries("world"), null);
  assert.equal(scopeCountryCode("world"), null);
  assert.equal(scopeCountryCode("europe"), null);
  assert.equal(scopeCountryCode("poland"), "PL");
  assert.equal(getScopeLabel("world"), "Świat");
  assert.equal(getScopeLabel("europe"), "Europa");
  assert.equal(getScopeLabel("poland"), "Polska");
  assert.equal(getScopeLabel("country:TR"), "Turcja");
  assert.equal(getScopeLabel("country:XK"), "Kosowo");
  assert.deepEqual(getScopeCountries("country:FR"), ["FR"]);
  assert.equal(getScopeCountries("country:FR")?.includes("GP"), false);
  assert.equal(getScopeCountries("country:FR"), getScopeCountries("country:FR"));
  assert.ok(Object.isFrozen(getScopeCountries("country:FR")));
  assert.deepEqual(getScopeCountries("europe"), "AL AD AT BE BA BG BY CH CY CZ DE DK EE ES FI FR GB GR HR HU IE IS IT LI LT LU LV MC MD ME MK MT NL NO PL PT RO RS SE SI SK SM UA VA XK".split(" "));
  assert.equal(getScopeCountries("europe")?.includes("TR"), false);
  assert.equal(getScopeCountries("europe")?.includes("RU"), false);
});

test("invalid helper input fails closed instead of broadening to all countries", () => {
  for (const value of ["country:ZZ", "country:UK", "moon", "__proto__"] as ScopeId[]) {
    for (const helper of [scopeCountryCode, getScopeCountries, getScopeLabel]) assert.throws(() => helper(value), /Nieznany/);
  }
});

test("missing or broken Intl data falls back safely to stable Polish names or the country code", () => {
  const modulePath = fileURLToPath(new URL("../lib/areas.ts", import.meta.url));
  for (const setup of [
    "Intl.DisplayNames = undefined;",
    "Intl.DisplayNames = class { static supportedLocalesOf(){ return []; } };",
    "Intl.DisplayNames = class { static supportedLocalesOf(){ return ['pl']; } of(){ throw new Error('ICU data absent'); } };",
  ]) {
    const script = `${setup} const a=require(${JSON.stringify(modulePath)}); console.log(JSON.stringify(['poland','country:TR','country:AD','country:XK'].map(a.getScopeLabel)));`;
    const labels = JSON.parse(execFileSync(process.execPath, ["--import", "tsx", "-e", script], { encoding: "utf8" }));
    assert.deepEqual(labels, ["Polska", "Turcja", "AD", "Kosowo"]);
  }
});

test("favorite country storage is versioned, code-only, normalized and order preserving", () => {
  const storage = new MemoryStorage();
  assert.deepEqual(readFavoriteCountries(storage), []);
  const input = Object.freeze(["tr", "PL", "jp", "TR", "XK"]);
  assert.equal(writeFavoriteCountries(storage, input), true);
  assert.deepEqual(readFavoriteCountries(storage), ["TR", "PL", "JP", "XK"]);
  const encoded = storage.getItem(AREAS_STORAGE_KEY)!;
  assert.deepEqual(JSON.parse(encoded), { version: 1, codes: ["TR", "PL", "JP", "XK"] });
  assert.doesNotMatch(encoded, /country:|lat|lon|geometry|title|http|event|name|timestamp/);
  assert.deepEqual(input, ["tr", "PL", "jp", "TR", "XK"]);
  assert.equal(writeFavoriteCountries(storage, []), true);
  assert.deepEqual(readFavoriteCountries(storage), []);
});

test("favorite writes enforce supported codes and the eight-item cap without replacing earlier values", () => {
  const storage = new MemoryStorage();
  const maximum = COUNTRY_OPTIONS.slice(0, MAX_FAVORITE_COUNTRIES).map(({ code }) => code);
  assert.equal(writeFavoriteCountries(storage, maximum), true);
  const prior = storage.getItem(AREAS_STORAGE_KEY);
  for (const values of [["ZZ"], ["country:PL"], ["Polska"], ["TR "], ["<script>"], ["PL", null], new Array(2), [...maximum, "JP"]]) {
    assert.equal(writeFavoriteCountries(storage, values as string[]), false);
    assert.equal(storage.getItem(AREAS_STORAGE_KEY), prior);
  }
  assert.equal(writeFavoriteCountries(storage, "PL" as unknown as string[]), false);
  assert.equal(writeFavoriteCountries(storage, null as unknown as string[]), false);
});

test("favorite reads reject corrupted, oversized, augmented, duplicate and future-schema storage", () => {
  const storage = new MemoryStorage();
  const valid = { version: 1, codes: ["PL"] };
  for (const value of [
    "{", "x".repeat(1025), JSON.stringify(null), JSON.stringify(["PL"]), JSON.stringify({ ...valid, version: 2 }),
    JSON.stringify({ ...valid, position: [21, 52] }), JSON.stringify({ version: 1 }),
    JSON.stringify({ ...valid, codes: ["PL", "pl"] }), JSON.stringify({ ...valid, codes: ["EU"] }),
    JSON.stringify({ ...valid, codes: [null] }), JSON.stringify({ ...valid, codes: "PL" }),
    JSON.stringify({ ...valid, codes: COUNTRY_OPTIONS.slice(0, 9).map(({ code }) => code) }),
  ]) {
    storage.values.set(AREAS_STORAGE_KEY, value);
    assert.deepEqual(readFavoriteCountries(storage), [], value.slice(0, 80));
  }
  storage.values.set(AREAS_STORAGE_KEY, JSON.stringify({ version: 1, codes: ["pl", "tr"] }));
  assert.deepEqual(readFavoriteCountries(storage), ["PL", "TR"]);
});

test("favorites tolerate unavailable storage without claiming persistence", () => {
  const blocked: AreaStorage = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } };
  const quota: AreaStorage = { getItem() { return null; }, setItem() { throw new Error("quota"); } };
  for (const storage of [null, blocked, quota]) {
    assert.deepEqual(readFavoriteCountries(storage), []);
    assert.equal(writeFavoriteCountries(storage, ["PL"]), false);
  }
});

test("favorite writes are idempotent, reads do not mutate stored state and cross-tab reads use the latest completed write", () => {
  const storage = new MemoryStorage();
  assert.equal(writeFavoriteCountries(storage, ["JP", "FR"]), true);
  const writes = storage.writes;
  assert.equal(writeFavoriteCountries(storage, ["jp", "FR"]), true);
  assert.equal(storage.writes, writes);
  const firstRead = readFavoriteCountries(storage);
  firstRead.push("TR");
  assert.deepEqual(readFavoriteCountries(storage), ["JP", "FR"]);
  assert.equal(writeFavoriteCountries(storage, ["TR"]), true);
  assert.deepEqual(readFavoriteCountries(storage), ["TR"]);
  assert.equal(writeFavoriteCountries(storage, ["FR", "JP"]), true);
  assert.deepEqual(readFavoriteCountries(storage), ["FR", "JP"]);
});
