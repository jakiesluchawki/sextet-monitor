import type { Category, EventDetail } from "./contracts";
import { PUBLIC_SOURCE_IDS, PUBLIC_TIME_BASIS, validatePublicSnapshot, type PublicSnapshot, type PublicSourceId } from "./public-snapshot";

export interface SharedView {
  scope: "world" | "europe" | "poland" | "turkey";
  hours: 24 | 72 | 168;
  view: "overview" | "explore" | "briefing";
  eventId?: string;
  category?: Category;
  sourceId?: PublicSourceId;
  search?: string;
}

/** Only these explicit public-view fields may enter a shared URL. */
const SHARED_KEYS = ["scope", "hours", "view", "eventId", "category", "sourceId", "search"] as const;
const SCOPES = new Set(["world", "europe", "poland", "turkey"]);
const VIEWS = new Set(["overview", "explore", "briefing"]);
const SOURCES = new Set<string>(PUBLIC_SOURCE_IDS);
const CATEGORIES = new Set(Object.keys(PUBLIC_TIME_BASIS));
const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[1-8][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/i;
const CONTROL_CHARACTERS = /[\u0000-\u001f\u007f-\u009f\u202a-\u202e\u2066-\u2069]/;
export const MAX_SHARE_HASH_LENGTH = 2048;
export const MAX_SHARE_SEARCH_LENGTH = 200;

function validSharedView(value: Record<string, unknown>): Partial<SharedView> {
  const result: Partial<SharedView> = {};
  if (typeof value.scope === "string" && SCOPES.has(value.scope)) result.scope = value.scope as SharedView["scope"];
  if (value.hours === 24 || value.hours === 72 || value.hours === 168) result.hours = value.hours;
  if (typeof value.view === "string" && VIEWS.has(value.view)) result.view = value.view as SharedView["view"];
  if (typeof value.eventId === "string" && UUID.test(value.eventId)) result.eventId = value.eventId.toLowerCase();
  if (typeof value.category === "string" && CATEGORIES.has(value.category)) result.category = value.category as Category;
  if (typeof value.sourceId === "string" && SOURCES.has(value.sourceId)) result.sourceId = value.sourceId as PublicSourceId;
  if (typeof value.search === "string" && value.search.length <= MAX_SHARE_SEARCH_LENGTH && !CONTROL_CHARACTERS.test(value.search)) {
    const search = value.search.trim();
    if (search) result.search = search;
  }
  return result;
}

/** No defaults are invented: callers merge the accepted fields with their own defaults. */
export function parseSharedView(hash: string): Partial<SharedView> {
  if (typeof hash !== "string" || !hash.startsWith("#") || hash.length > MAX_SHARE_HASH_LENGTH) return {};
  const text = hash.slice(1);
  try {
    // URLSearchParams tolerates malformed percent encodings; shared links do not.
    decodeURIComponent(text.replace(/\+/g, " "));
    if (/%(?![a-f0-9]{2})/i.test(text)) return {};
    const params = new URLSearchParams(text);
    const value: Record<string, unknown> = {};
    for (const key of SHARED_KEYS) {
      if (params.getAll(key).length > 1) return {};
      const part = params.get(key);
      if (part !== null) value[key] = key === "hours" ? (/^(24|72|168)$/.test(part) ? Number(part) : undefined) : part;
    }
    return validSharedView(value);
  } catch {
    return {};
  }
}

export function serializeSharedView(view: Partial<SharedView>): string {
  const safe = validSharedView(view as Record<string, unknown>);
  const params = new URLSearchParams();
  for (const key of SHARED_KEYS) {
    const value = safe[key];
    if (value !== undefined) params.set(key, String(value));
  }
  const hash = params.toString();
  // Encoded non-ASCII search may be longer than the input; other view fields survive.
  if (hash.length + 1 > MAX_SHARE_HASH_LENGTH) {
    params.delete("search");
    return params.size ? `#${params.toString()}` : "";
  }
  return hash ? `#${hash}` : "";
}

/** The current URL's query/hash are never copied, including tokens or old filters. */
export function buildShareUrl(baseUrl: string, view: Partial<SharedView>): string {
  const url = new URL(baseUrl);
  if (!["https:", "http:"].includes(url.protocol) || url.username || url.password) {
    throw new Error("Link udostępniania wymaga adresu HTTP(S) bez danych logowania.");
  }
  url.search = "";
  url.hash = serializeSharedView(view);
  return url.href;
}

export type PublicStorage = Pick<Storage, "getItem" | "setItem">;
export const WATCH_STORAGE_KEY = "sextet.public.watch.v1";
export const BASELINE_STORAGE_KEY = "sextet.public.baseline.v1";
export const MAX_WATCH_IDS = 30;
export const MAX_BASELINE_EVENTS = 10000;
export const MAX_BASELINE_BYTES = 1024 * 1024;
const MAX_WATCH_BYTES = 4096;
const FINGERPRINT = /^[a-f0-9]{32}$/;
const ISO = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})$/;

export interface WatchState { version: 1; ids: string[]; updatedAt: string | null }
export interface SnapshotBaseline { version: 1; generatedAt: string; entries: Array<[string, string]> }
export interface SnapshotComparison {
  status: "first_visit" | "same_snapshot" | "newer_snapshot" | "out_of_order";
  addedIds: string[];
  changedIds: string[];
  missingCount: number;
  /** Publication time of the baseline, not a reconstructed visit or incident time. */
  comparedAt: string | null;
  snapshotAt: string;
  limitations: string[];
}

const object = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value);
const onlyKeys = (value: Record<string, unknown>, keys: string[]) => Object.keys(value).length === keys.length && Object.keys(value).every(key => keys.includes(key));
const emptyWatch = (): WatchState => ({ version: 1, ids: [], updatedAt: null });

function timestamp(value: unknown): value is string {
  if (typeof value !== "string" || !ISO.test(value) || !Number.isFinite(Date.parse(value))) return false;
  const [year, month, day, hour, minute, second] = value.slice(0, 19).split(/[-T:]/).map(Number);
  return month >= 1 && month <= 12 && day >= 1 && day <= new Date(Date.UTC(year, month, 0)).getUTCDate() && hour < 24 && minute < 60 && second < 60;
}

function parseStored(text: string | null, maxBytes: number): unknown {
  if (typeof text !== "string" || text.length > maxBytes || new TextEncoder().encode(text).byteLength > maxBytes) return null;
  try { return JSON.parse(text); } catch { return null; }
}

function validatedWatch(value: unknown): WatchState | null {
  if (!object(value) || !onlyKeys(value, ["version", "ids", "updatedAt"]) || value.version !== 1 || !timestamp(value.updatedAt)) return null;
  if (!Array.isArray(value.ids) || value.ids.length > MAX_WATCH_IDS || !value.ids.every(id => typeof id === "string" && UUID.test(id))) return null;
  const ids = value.ids.map(id => id.toLowerCase());
  if (new Set(ids).size !== ids.length) return null;
  return { version: 1, ids, updatedAt: value.updatedAt };
}

export function readWatchState(storage: PublicStorage | null): WatchState {
  if (!storage) return emptyWatch();
  try { return validatedWatch(parseStored(storage.getItem(WATCH_STORAGE_KEY), MAX_WATCH_BYTES)) || emptyWatch(); }
  catch { return emptyWatch(); }
}

/** Full selection, last completed write wins. Storage events must re-read storage, not replay stale event.newValue. */
export function writeWatchState(storage: PublicStorage | null, ids: readonly string[], nowMs = Date.now()): boolean {
  if (!storage || !Array.isArray(ids) || ids.length > MAX_WATCH_IDS || !ids.every(id => typeof id === "string" && UUID.test(id)) || !Number.isFinite(nowMs)) return false;
  const safeIds = [...new Set(ids.map(id => id.toLowerCase()))];
  try {
    const previous = validatedWatch(parseStored(storage.getItem(WATCH_STORAGE_KEY), MAX_WATCH_BYTES));
    if (previous && JSON.stringify(previous.ids) === JSON.stringify(safeIds)) return true;
    // A sequential write is newer even within one millisecond or after a clock correction.
    const updatedAt = new Date(Math.max(nowMs, previous?.updatedAt ? Date.parse(previous.updatedAt) + 1 : nowMs)).toISOString();
    const serialized = JSON.stringify({ version: 1, ids: safeIds, updatedAt });
    if (serialized.length > MAX_WATCH_BYTES) return false;
    storage.setItem(WATCH_STORAGE_KEY, serialized);
    return true;
  } catch { return false; }
}

function validatedBaseline(value: unknown): SnapshotBaseline | null {
  if (!object(value) || !onlyKeys(value, ["version", "generatedAt", "entries"]) || value.version !== 1 || !timestamp(value.generatedAt)) return null;
  // A corrupt future timestamp must not pin the browser to an unreachable baseline.
  if (Date.parse(value.generatedAt) > Date.now() + 300000) return null;
  if (!Array.isArray(value.entries) || value.entries.length > MAX_BASELINE_EVENTS) return null;
  const entries: Array<[string, string]> = [];
  const seen = new Set<string>();
  for (const entry of value.entries) {
    if (!Array.isArray(entry) || entry.length !== 2 || typeof entry[0] !== "string" || !UUID.test(entry[0]) || typeof entry[1] !== "string" || !FINGERPRINT.test(entry[1])) return null;
    const id = entry[0].toLowerCase();
    if (seen.has(id)) return null;
    seen.add(id);
    entries.push([id, entry[1]]);
  }
  entries.sort(([a], [b]) => a.localeCompare(b));
  return { version: 1, generatedAt: value.generatedAt, entries };
}

export function readBaseline(storage: PublicStorage | null): SnapshotBaseline | null {
  if (!storage) return null;
  try { return validatedBaseline(parseStored(storage.getItem(BASELINE_STORAGE_KEY), MAX_BASELINE_BYTES)); }
  catch { return null; }
}

/** An older tab cannot intentionally replace a newer baseline. localStorage has no atomic compare-and-swap. */
export function writeBaseline(storage: PublicStorage | null, baseline: SnapshotBaseline): boolean {
  if (!storage) return false;
  const safe = validatedBaseline(baseline);
  if (!safe) return false;
  try {
    const previous = validatedBaseline(parseStored(storage.getItem(BASELINE_STORAGE_KEY), MAX_BASELINE_BYTES));
    if (previous) {
      const difference = Date.parse(safe.generatedAt) - Date.parse(previous.generatedAt);
      if (difference < 0) return false;
      // Different content at the same publication time is not a trusted newer snapshot.
      if (difference === 0) return JSON.stringify(previous.entries) === JSON.stringify(safe.entries);
    }
    const serialized = JSON.stringify(safe);
    if (serialized.length > MAX_BASELINE_BYTES) return false;
    storage.setItem(BASELINE_STORAGE_KEY, serialized);
    return true;
  } catch { return false; }
}

/** Stable object order; the caller explicitly sorts fields whose array order has no meaning. */
function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (object(value)) return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}`;
  return JSON.stringify(value) ?? "null";
}

/** Compact, non-cryptographic 128-bit content fingerprint, not a signature or proof of provenance. */
function fingerprint(text: string): string {
  let a = 1779033703, b = 3144134277, c = 1013904242, d = 2773480762;
  for (let i = 0; i < text.length; i++) {
    const code = text.charCodeAt(i);
    a = b ^ Math.imul(a ^ code, 597399067);
    b = c ^ Math.imul(b ^ code, 2869860233);
    c = d ^ Math.imul(c ^ code, 951274213);
    d = a ^ Math.imul(d ^ code, 2716044179);
  }
  a = Math.imul(c ^ (a >>> 18), 597399067);
  b = Math.imul(d ^ (b >>> 22), 2869860233);
  c = Math.imul(a ^ (c >>> 17), 951274213);
  d = Math.imul(b ^ (d >>> 19), 2716044179);
  return [a ^ b ^ c ^ d, b ^ a, c ^ a, d ^ a].map(part => (part >>> 0).toString(16).padStart(8, "0")).join("");
}

function eventFingerprint(event: EventDetail): string {
  const instant = (value: string | null) => value === null ? null : new Date(value).toISOString();
  const evidence = event.evidence.map(item => ({
    source_id: item.source_id, provider_record_id: item.provider_record_id,
    issued_at: instant(item.issued_at), source_updated_at: instant(item.source_updated_at),
    origins: [...item.origins].sort(), source_url: item.source_url,
  })).map(canonical).sort();
  return fingerprint(canonical({
    kind: event.kind, category: event.category, title: event.title, description: event.description,
    occurred_start: instant(event.occurred_start), occurred_end: instant(event.occurred_end),
    issued_at: instant(event.issued_at), source_updated_at: instant(event.source_updated_at),
    valid_from: instant(event.valid_from), valid_to: instant(event.valid_to),
    countries: [...event.countries].sort(), geometry: event.geometry,
    location_precision: event.location_precision, time_precision: event.time_precision,
    severity: event.severity, severity_label: event.severity_label, severity_reason: event.severity_reason,
    original_severity: event.original_severity, lifecycle_status: event.lifecycle_status,
    verification_status: event.verification_status, source_ids: [...event.source_ids].sort(),
    independent_source_count: event.independent_source_count, source_url: event.source_url,
    tags: event.tags.filter(tag => tag !== "cached_public_data").sort(), evidence,
    // Import clocks, cached-data flags, raw hashes, revisions and derived relations are deliberately absent.
    // Rebuilding the public database or re-fetching an unchanged record is not a source change.
  }));
}

/** No event text, coordinates, evidence payload, private identifiers or URLs are persisted. */
export function makeBaseline(snapshot: PublicSnapshot): SnapshotBaseline {
  const safe = validatePublicSnapshot(snapshot);
  const entries: Array<[string, string]> = safe.events.map(event => [event.id.toLowerCase(), eventFingerprint(event)]);
  if (new Set(entries.map(([id]) => id)).size !== entries.length) throw new Error("Powtórzony identyfikator publicznego rekordu po normalizacji UUID.");
  entries.sort(([a], [b]) => a.localeCompare(b));
  return { version: 1, generatedAt: safe.generated_at, entries };
}

export function compareSnapshots(snapshot: PublicSnapshot, baseline: SnapshotBaseline | null): SnapshotComparison {
  const current = makeBaseline(snapshot);
  const previous = validatedBaseline(baseline);
  const limitations = [
    "Porównanie obejmuje dwa publiczne zestawy w tej przeglądarce, nie pełną historię źródeł.",
    "Rekord nieobecny w nowszym zestawie nie oznacza zakończenia zdarzenia; mógł wypaść z okna źródła, limitu lub grupowania.",
    "Nowy rekord oznacza nowy wpis w zestawie, nie potwierdzony nowy incydent. Czasy publikacji, wystąpienia i ważności pozostają odrębne.",
  ];
  const result: SnapshotComparison = {
    status: "first_visit", addedIds: [], changedIds: [], missingCount: 0,
    comparedAt: previous?.generatedAt ?? null, snapshotAt: current.generatedAt, limitations,
  };
  if (!previous) return result;
  const difference = Date.parse(current.generatedAt) - Date.parse(previous.generatedAt);
  if (difference < 0) {
    result.status = "out_of_order";
    limitations.push("Odczytany zestaw jest starszy niż zapisany punkt odniesienia. Nie wyznaczono zmian ani nie cofnięto punktu odniesienia.");
    return result;
  }
  if (difference === 0) {
    result.status = "same_snapshot";
    if (JSON.stringify(current.entries) !== JSON.stringify(previous.entries)) {
      limitations.push("Zestawy mają ten sam czas publikacji, lecz różną treść. Nie można ustalić ich kolejności; porównanie zmian pominięto.");
    }
    return result;
  }
  result.status = "newer_snapshot";
  const previousEntries = new Map(previous.entries);
  for (const [id, hash] of current.entries) {
    const before = previousEntries.get(id);
    if (before === undefined) result.addedIds.push(id);
    else if (before !== hash) result.changedIds.push(id);
    previousEntries.delete(id);
  }
  result.missingCount = previousEntries.size;
  return result;
}
