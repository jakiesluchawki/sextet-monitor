import type { Category, EventQuery, TimeBasis } from "./contracts";

export const CATEGORY_LABELS: Record<Category, string> = {
  earthquake: "Trzęsienia ziemi", disaster: "Katastrofy", weather: "Pogoda",
  aviation: "Lotnictwo", cyber: "Cyberbezpieczeństwo", internet: "Internet",
};
export const CATEGORY_SHORT: Record<Category, string> = {
  earthquake: "Trzęsienie", disaster: "Katastrofa", weather: "Pogoda",
  aviation: "Lotnictwo", cyber: "Cyber", internet: "Internet",
};
export const TIME_BASIS_LABELS: Record<TimeBasis, string> = {
  occurred: "Daty wystąpienia", changed: "Zmiany w monitorze", published: "Daty publikacji", validity: "Okresu ważności",
};
export const TIME_BASIS_HELP: Record<TimeBasis, string> = {
  occurred: "Czas zdarzenia, nie godzina pobrania. Brak daty pozostaje nieznany.",
  changed: "Zmiany lokalnych rekordów. Nowy import historyczny nie oznacza nowego zdarzenia.",
  published: "Data publikacji źródła, nie czas ataku lub incydentu. Gdy znany jest tylko dzień, nie dopisujemy godziny.",
  validity: "Przedział ważności zadeklarowany przez źródło przecina okno. Status jest bieżący; to nie odtworzony stan historyczny. Brak końca ważności pozostaje nieznany.",
};
export const DEFAULT_QUERY: EventQuery = {
  window_hours: 24, time_basis: "occurred", severity_min: 0, min_sources: 1, include_inactive: false, limit: 300,
};
export const WARSAW = { lat: 52.2297, lon: 21.0122 } as const;
export const SEVERITY_LABELS = ["Nieokreślona", "Niska", "Umiarkowana", "Wysoka", "Krytyczna"];

export function serializeQuery(query: EventQuery): string {
  const params = new URLSearchParams();
  const keys: Array<keyof EventQuery> = [
    "window_hours", "time_basis", "country", "region", "category", "severity_min", "min_sources",
    "lat", "lon", "radius_km", "include_inactive", "limit", "since", "until",
  ];
  for (const key of keys) {
    const value = query[key];
    if (value !== undefined && value !== "") params.set(key, String(value));
  }
  return params.toString();
}

export function changeQuery(query: EventQuery, patch: Partial<EventQuery>): EventQuery {
  const next = { ...query, ...patch };
  if ("window_hours" in patch) {
    delete next.since;
    delete next.until;
  }
  return next;
}

/** Move the shared window in UTC; the caller supplies its clock only on interaction. */
export function timeWindowPatch(query: EventQuery, hoursBack: number, nowMs: number): Pick<EventQuery,"since"|"until"> {
  if (!Number.isInteger(hoursBack) || hoursBack < 0 || hoursBack > 168 || !Number.isFinite(nowMs)) {
    throw new RangeError("Przesunięcie czasu musi wynosić od 0 do 168 pełnych godzin.");
  }
  if (hoursBack === 0) return { since: undefined, until: undefined };
  const start = query.since ? Date.parse(query.since) : NaN;
  const end = query.until ? Date.parse(query.until) : NaN;
  const width = Number.isFinite(start) && Number.isFinite(end) && end > start
    ? end - start : query.window_hours * 3_600_000;
  if (!Number.isFinite(width) || width <= 0) throw new RangeError("Nieprawidłowa szerokość okna czasu.");
  const until = nowMs - hoursBack * 3_600_000;
  return { since: new Date(until - width).toISOString(), until: new Date(until).toISOString() };
}

function numberInRange(value: unknown, min: number, max: number): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= min && value <= max;
}
function isoDate(value: unknown): value is string {
  return typeof value === "string" && /T.*(?:Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value));
}

/** The parser's confirmed interpretation is the GET /events contract, never free text or SQL. */
export function interpretationToQuery(value: Record<string, unknown> | null): EventQuery | null {
  if (!value) return null;
  const query = { ...DEFAULT_QUERY };
  if ("window_hours" in value && value.window_hours !== null) {
    if (!numberInRange(value.window_hours, 1, 720) || !Number.isInteger(value.window_hours)) return null;
    query.window_hours = value.window_hours;
  }
  if (value.time_basis != null) {
    if (typeof value.time_basis !== "string" || !Object.hasOwn(TIME_BASIS_LABELS, value.time_basis)) return null;
    query.time_basis = value.time_basis as TimeBasis;
  }
  if (value.country != null && value.country !== "") {
    if (typeof value.country !== "string" || !/^[A-Z]{2}$/.test(value.country)) return null;
    query.country = value.country;
  }
  if (value.region != null && value.region !== "") {
    if (value.region !== "europe") return null;
    query.region = value.region;
  }
  if (value.category != null && value.category !== "") {
    if (typeof value.category !== "string" || !Object.hasOwn(CATEGORY_LABELS, value.category)) return null;
    query.category = value.category as Category;
  }
  if (value.severity_min != null) {
    if (!numberInRange(value.severity_min, 0, 4) || !Number.isInteger(value.severity_min)) return null;
    query.severity_min = value.severity_min;
  }
  if (value.min_sources != null) {
    if (!numberInRange(value.min_sources, 1, 10) || !Number.isInteger(value.min_sources)) return null;
    query.min_sources = value.min_sources;
  }
  const radiusFields = [value.lat, value.lon, value.radius_km];
  if (radiusFields.some((part) => part != null)) {
    if (!numberInRange(value.lat, -90, 90) || !numberInRange(value.lon, -180, 180) || !numberInRange(value.radius_km, Number.MIN_VALUE, 20000)) return null;
    query.lat = value.lat;
    query.lon = value.lon;
    query.radius_km = value.radius_km;
  }
  if (value.include_inactive != null) {
    if (typeof value.include_inactive !== "boolean") return null;
    query.include_inactive = value.include_inactive;
  }
  if (value.limit != null) {
    if (!numberInRange(value.limit, 1, 1000) || !Number.isInteger(value.limit)) return null;
    query.limit = value.limit;
  }
  if (value.since != null) {
    if (!isoDate(value.since)) return null;
    query.since = value.since;
  }
  if (value.until != null) {
    if (!isoDate(value.until)) return null;
    query.until = value.until;
  }
  if (query.since && query.until && Date.parse(query.since) >= Date.parse(query.until)) return null;
  return query;
}

export function queryLabel(query: EventQuery): string {
  const place = query.country || (query.region === "europe" ? "Europa" : "Cały świat");
  const time = query.since || query.until ? "Wybrany zakres czasu" : query.window_hours === 168 ? "7 dni" : `${query.window_hours} h`;
  return [time, "według " + TIME_BASIS_LABELS[query.time_basis].toLowerCase(), place,
    query.category ? CATEGORY_SHORT[query.category] : "wszystkie kategorie",
    ...(query.radius_km ? [`promień ${query.radius_km} km`] : [])].join(" · ");
}
