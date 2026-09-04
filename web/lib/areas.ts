import { COUNTRY_NAMES_PL } from "./countries";

export type ScopeId = "world" | "europe" | "poland" | `country:${string}`;
export type AreaStorage = Pick<Storage, "getItem" | "setItem">;
export interface CountryOption { code: string; label: string }

/** ISO 3166-1 alpha-2 facts (same 249 codes as the provider parser), plus explicit XK.
 * Countries and dependent territories are not inferred from coordinates or sovereignty.
 * Intl.DisplayNames is used for labels only, never as the validator for country codes.
 */
const COUNTRY_CODES = Object.freeze((
  "AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ " +
  "CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR " +
  "GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT " +
  "JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ " +
  "NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW " +
  "SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ " +
  "UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS XK YE YT ZA ZM ZW"
).split(" "));
const supportedCodes = new Set(COUNTRY_CODES);
const countrySets = new Map(COUNTRY_CODES.map((code) => [code, Object.freeze([code])]));
/** Keep the exact existing Europe scope; this is a country set, not a geometric continent. */
const EUROPE_COUNTRIES = Object.freeze("AL AD AT BE BA BG BY CH CY CZ DE DK EE ES FI FR GB GR HR HU IE IS IT LI LT LU LV MC MD ME MK MT NL NO PL PT RO RS SE SI SK SM UA VA XK".split(" "));

function polishRegionNames(): Intl.DisplayNames | null {
  try {
    if (typeof Intl.DisplayNames !== "function" || !Intl.DisplayNames.supportedLocalesOf(["pl"]).length) return null;
    return new Intl.DisplayNames(["pl"], { type: "region", fallback: "none" });
  } catch { return null; }
}
const regionNames = polishRegionNames();
function countryLabel(code: string): string {
  // XK is an explicitly supported non-ISO code; do not let CLDR omissions turn it into "unknown".
  if (code === "XK") return "Kosowo";
  try {
    const label = regionNames?.of(code);
    if (label && label !== code) return label;
  } catch { /* Missing/limited Intl data must not break a supported country choice. */ }
  return COUNTRY_NAMES_PL[code] || code;
}
/** Stable code order avoids ordering differences between server/browser collation versions. */
export const COUNTRY_OPTIONS: readonly Readonly<CountryOption>[] = Object.freeze(
  COUNTRY_CODES.map((code) => Object.freeze({ code, label: countryLabel(code) })),
);
const countryLabels = new Map(COUNTRY_OPTIONS.map(({ code, label }) => [code, label]));

function normalizeCountryCode(value: unknown): string | null {
  if (typeof value !== "string" || !/^[A-Za-z]{2}$/.test(value)) return null;
  const code = value.toUpperCase();
  return supportedCodes.has(code) ? code : null;
}

/** Legacy URLs survive, but newly saved state uses one canonical spelling per scope. */
export function normalizeScopeId(value: unknown): ScopeId | null {
  if (value === "world" || value === "europe" || value === "poland") return value;
  if (value === "turkey") return "country:TR";
  if (typeof value !== "string" || !value.startsWith("country:")) return null;
  const code = normalizeCountryCode(value.slice("country:".length));
  if (!code) return null;
  return code === "PL" ? "poland" : `country:${code}`;
}
function requireScope(scope: ScopeId): ScopeId {
  const normalized = normalizeScopeId(scope);
  if (!normalized) throw new Error("Nieznany lub nieobsługiwany obszar.");
  return normalized;
}

export function scopeCountryCode(scope: ScopeId): string | null {
  const normalized = requireScope(scope);
  return normalized === "poland" ? "PL" : normalized.startsWith("country:") ? normalized.slice("country:".length) : null;
}
export function getScopeLabel(scope: ScopeId): string {
  const normalized = requireScope(scope);
  if (normalized === "world") return "Świat";
  if (normalized === "europe") return "Europa";
  const code = scopeCountryCode(normalized)!;
  return countryLabels.get(code)!;
}
export function getScopeCountries(scope: ScopeId): readonly string[] | null {
  const normalized = requireScope(scope);
  if (normalized === "world") return null;
  if (normalized === "europe") return EUROPE_COUNTRIES;
  return countrySets.get(scopeCountryCode(normalized)!)!;
}

export const MAX_FAVORITE_COUNTRIES = 8;
export const AREAS_STORAGE_KEY = "sextet.public.areas.v1";
const MAX_AREAS_BYTES = 1024;
interface FavoriteCountries { version: 1; codes: string[] }

function validatedFavorites(value: unknown): FavoriteCountries | null {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return null;
  const object = value as Record<string, unknown>;
  if (Object.keys(object).length !== 2 || !Object.hasOwn(object, "version") || !Object.hasOwn(object, "codes") || object.version !== 1) return null;
  if (!Array.isArray(object.codes) || object.codes.length > MAX_FAVORITE_COUNTRIES) return null;
  const codes = Array.from(object.codes, normalizeCountryCode);
  if (codes.some((code) => code === null) || new Set(codes).size !== codes.length) return null;
  return { version: 1, codes: codes as string[] };
}
function parseFavorites(text: string | null): FavoriteCountries | null {
  if (typeof text !== "string" || text.length > MAX_AREAS_BYTES) return null;
  try {
    if (new TextEncoder().encode(text).byteLength > MAX_AREAS_BYTES) return null;
    return validatedFavorites(JSON.parse(text));
  } catch { return null; }
}

/** Per-device country choices only; blocked, corrupt and future-schema storage is empty. */
export function readFavoriteCountries(storage: AreaStorage | null): string[] {
  if (!storage) return [];
  try { return parseFavorites(storage.getItem(AREAS_STORAGE_KEY))?.codes || []; }
  catch { return []; }
}

/** Full selection; last completed write wins. Cross-tab listeners must re-read this key. */
export function writeFavoriteCountries(storage: AreaStorage | null, codes: readonly string[]): boolean {
  if (!storage || !Array.isArray(codes) || codes.length > MAX_FAVORITE_COUNTRIES) return false;
  const normalized = Array.from(codes, normalizeCountryCode);
  if (normalized.some((code) => code === null)) return false;
  const safeCodes = [...new Set(normalized)] as string[];
  try {
    const previous = parseFavorites(storage.getItem(AREAS_STORAGE_KEY));
    if (previous && JSON.stringify(previous.codes) === JSON.stringify(safeCodes)) return true;
    const value: FavoriteCountries = { version: 1, codes: safeCodes };
    storage.setItem(AREAS_STORAGE_KEY, JSON.stringify(value));
    return true;
  } catch { return false; }
}
