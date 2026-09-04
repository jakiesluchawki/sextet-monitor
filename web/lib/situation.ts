import type { Category, EventDetail, SourceStatus, TimeBasis } from "./contracts";
import { countryName, formatDate, formatEventDate, KIND_LABELS, safeHttpUrl, STATE_LABELS } from "./format";
import { positionsOf } from "./map-data";
import { PUBLIC_SOURCE_IDS, PUBLIC_SOURCE_INFO, PUBLIC_TIME_BASIS, type PublicSnapshot } from "./public-snapshot";
import { getScopeCountries, getScopeLabel, normalizeScopeId, type ScopeId } from "./areas";

export type { ScopeId } from "./areas";
export const SITUATION_CATEGORIES: readonly Category[] = Object.freeze([
  "earthquake", "disaster", "weather", "aviation", "cyber", "internet", "space_weather",
]);
export const SITUATION_CATEGORY_LABELS: Readonly<Record<Category, string>> = Object.freeze({
  earthquake: "Trzęsienia ziemi", disaster: "Katastrofy", weather: "Pogoda", aviation: "Lotnictwo",
  cyber: "Cyberbezpieczeństwo", internet: "Usługi internetowe", space_weather: "Pogoda kosmiczna",
});
export const SITUATION_TIME_LABELS: Readonly<Record<TimeBasis, string>> = Object.freeze({
  occurred: "czas zdarzenia", published: "czas publikacji", validity: "okres ważności", changed: "czas zmiany",
});
export interface SituationHighlight { event: EventDetail; reason: string; timeBasis: TimeBasis }
export interface SituationTimelineBin {
  start: string; end: string; count: number;
  byBasis: Record<TimeBasis, number>;
}
export interface Situation {
  scope: ScopeId; scopeLabel: string; hours: number; since: string; until: string;
  events: EventDetail[]; highlights: SituationHighlight[];
  categoryCounts: Array<{ category: Category; count: number; timeBasis: TimeBasis }>;
  timeline: SituationTimelineBin[]; mapped: number; unlocated: number; limitations: string[];
  kindCounts: Array<{ kind: string; count: number }>;
  activeAdvisories: number; expiredAdvisories: number; withdrawnAdvisories: number;
  unknownTimeCount: number; unknownCountryCount: number; cachedCount: number;
  sourceWarnings: string[];
}
export type AdvisoryState = "active" | "expired" | "withdrawn" | "upcoming" | "unknown";
type Span = { start: number; end: number | null; point: boolean };
const HOUR = 3_600_000, DAY = 24 * HOUR;
const instant = (value: string | null | undefined): number | null => {
  if (!value) return null;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
};
const compareText = (a: string, b: string) => a < b ? -1 : a > b ? 1 : 0;

/** "Active" is evaluated at the saved snapshot clock, not at the reader's clock. */
export function advisoryState(event: EventDetail, at: string | number): AdvisoryState {
  const clock = typeof at === "number" ? at : instant(at);
  const start = instant(event.valid_from), end = instant(event.valid_to);
  if (event.lifecycle_status === "withdrawn") return "withdrawn";
  if (clock === null || !Number.isFinite(clock) || (start !== null && end !== null && end <= start)) return "unknown";
  if (event.lifecycle_status === "expired" || (end !== null && end <= clock)) return "expired";
  if (start === null) return "unknown";
  if (start > clock) return "upcoming";
  if (end !== null || event.lifecycle_status === "active") return "active";
  return "unknown";
}

/** Unknown occurrence/publication dates never fall back to ingestion or source update. */
function eventSpan(event: EventDetail): Span | null {
  const basis = PUBLIC_TIME_BASIS[event.category];
  const start = instant(basis === "validity" ? event.valid_from : basis === "published" ? event.issued_at : event.occurred_start);
  if (start === null) return null;
  if (basis === "validity") {
    const end = instant(event.valid_to);
    if ((event.valid_to !== null && end === null) || (end !== null && end <= start)) return null;
    // A missing end is not evidence that an expired/unknown advisory continues forever.
    if (end === null && event.lifecycle_status !== "active") return { start, end: null, point: true };
    return { start, end, point: false };
  }
  if (event.time_precision === "day" || event.tags.includes("date_only_utc_anchor")) {
    const day = Math.floor(start / DAY) * DAY;
    return { start: day, end: day + DAY, point: false };
  }
  return { start, end: null, point: true };
}
function intersects(span: Span, since: number, until: number): boolean {
  return span.point ? span.start >= since && span.start < until
    : span.start < until && (span.end === null || span.end > since);
}
function sourceWarning(source: SourceStatus, clock: number): string | null {
  const reasons: string[] = [];
  if (!source.enabled || source.status === "disabled") reasons.push("źródło wyłączone");
  else if (source.status !== "ok" && source.status !== "ok_empty") reasons.push(STATE_LABELS[source.status].toLowerCase());
  const success = instant(source.last_success_at);
  if (success === null) reasons.push("brak daty udanego odczytu");
  else if (success > clock + 300_000) reasons.push("data odczytu wyprzedza zegar odniesienia");
  else if (clock - success > Math.max(3 * HOUR, 3 * source.poll_interval_seconds * 1_000)) reasons.push("ostatni odczyt jest opóźniony");
  if (!reasons.length) return null;
  return `${source.name}: ${reasons.join("; ")}. Ostatni udany odczyt: ${formatDate(source.last_success_at)}.`;
}
function backedByEvidence(event: EventDetail, sourceIds: ReadonlySet<string>, until: number): boolean {
  return event.evidence.some((item) => {
    const retrieved = instant(item.retrieved_at);
    return event.source_ids.includes(item.source_id) && sourceIds.has(item.source_id)
      && retrieved !== null && retrieved <= until + 300_000;
  });
}
function recentTime(event: EventDetail): number {
  return instant(PUBLIC_TIME_BASIS[event.category] === "validity" ? event.valid_from
    : PUBLIC_TIME_BASIS[event.category] === "published" ? event.issued_at : event.occurred_start) ?? -Infinity;
}
function recentAdvisory(event: EventDetail, since: number, until: number): boolean {
  const issued = instant(event.issued_at), start = instant(event.valid_from);
  return (issued !== null && issued >= since && issued < until) || (start !== null && start >= since && start < until);
}
function highSourceSeverity(event: EventDetail): boolean {
  return event.severity >= 3 && event.original_severity !== null && event.original_severity !== undefined;
}
function highlightReason(event: EventDetail, until: number): string {
  const prefix = highSourceSeverity(event) ? "Wysoka waga według źródła. " : "";
  const basis = PUBLIC_TIME_BASIS[event.category];
  if (basis === "validity") {
    const status = advisoryState(event, until);
    const description: Record<AdvisoryState, string> = {
      active: "Ważne w chwili przygotowania zestawu; okres ważności przecina wybrane okno.",
      expired: "Ważność przecina okno lub jej początek jest w oknie; ostrzeżenie już wygasło według zestawu.",
      withdrawn: "Ważność przecina okno lub jej początek jest w oknie; źródło oznaczyło ostrzeżenie jako odwołane.",
      upcoming: "Podany początek ważności jest w przyszłości względem zestawu.",
      unknown: "Podany początek ważności jest w oknie; bieżącej ważności nie ustalono.",
    };
    return prefix + description[status] + (event.valid_to === null ? " Końca ważności nie ustalono." : "");
  }
  return prefix + (basis === "published"
    ? "Publikacja w wybranym oknie; nie oznacza czasu wystąpienia incydentu."
    : "Źródłowy czas początku zdarzenia w wybranym oknie.");
}
function chooseHighlights(events: EventDetail[], snapshot: PublicSnapshot, since: number, until: number): SituationHighlight[] {
  const sourceIds = new Set(snapshot.sources.map((source) => source.id));
  const candidates = events.filter((event) => backedByEvidence(event, sourceIds, until));
  const freshness = (event: EventDetail) => PUBLIC_TIME_BASIS[event.category] !== "validity" || recentAdvisory(event, since, until) ? 1 : 0;
  candidates.sort((a, b) => freshness(b) - freshness(a)
    || Number(highSourceSeverity(b)) - Number(highSourceSeverity(a))
    || recentTime(b) - recentTime(a) || compareText(a.id, b.id));
  const candidateRank = new Map(candidates.map((event, index) => [event.id, index]));
  const selected: EventDetail[] = [], categories = new Set<Category>();
  const repeatsSelection = (event: EventDetail) => selected.some((chosen) => (
    event.category === chosen.category && event.title.trim() === chosen.title.trim()
      && [...event.countries].sort().join(",") === [...chosen.countries].sort().join(",")
  ) || event.relations.some((relation) => relation.event_id === chosen.id && relation.relation_type === "possible_same_event")
    || chosen.relations.some((relation) => relation.event_id === event.id && relation.relation_type === "possible_same_event"));
  // One representative per category first; volume from a catalog cannot monopolize a brief.
  for (const event of candidates) {
    if (categories.has(event.category) || repeatsSelection(event)) continue;
    categories.add(event.category); selected.push(event);
  }
  const selectedIds = new Set(selected.map((event) => event.id));
  const representedSources = new Set(selected.flatMap((event) => event.source_ids));
  // At most one old still-valid advisory per category: context must not fill every spare card.
  const remainder = candidates.filter((event) => !selectedIds.has(event.id) && freshness(event) === 1);
  remainder.sort((a, b) => Number(b.source_ids.some((id) => !representedSources.has(id)))
    - Number(a.source_ids.some((id) => !representedSources.has(id)))
    || candidateRank.get(a.id)! - candidateRank.get(b.id)!);
  for (const event of remainder) {
    if (selected.length >= 8) break;
    if (repeatsSelection(event)) continue;
    selected.push(event);
  }
  return selected.slice(0, 8).map((event) => ({ event, reason: highlightReason(event, until), timeBasis: PUBLIC_TIME_BASIS[event.category] }));
}

/** Selection is deterministic. `now` affects only freshness warnings, never the data window. */
export function buildSituation(snapshot: PublicSnapshot, options: { scope: ScopeId; hours: number; now?: number }): Situation {
  const scope = normalizeScopeId(options.scope);
  if (!scope) throw new Error("Nieznany zakres sytuacji.");
  if (!Number.isInteger(options.hours) || options.hours < 1 || options.hours > 720) throw new Error("Okno sytuacji musi obejmować od 1 do 720 godzin.");
  const until = instant(snapshot.generated_at);
  if (until === null) throw new Error("Nieprawidłowy czas przygotowania zestawu.");
  const now = options.now ?? until;
  if (!Number.isFinite(now)) throw new Error("Nieprawidłowy zegar odniesienia.");
  const since = until - options.hours * HOUR, countries = getScopeCountries(scope);
  const inRegion = (event: EventDetail) => countries === null || event.countries.some((country) => countries.includes(country));
  const regional = snapshot.events.filter(inRegion);
  const unknownTimeCount = regional.filter((event) => eventSpan(event) === null).length;
  const unknownCountryCount = snapshot.events.filter((event) => {
    const span = eventSpan(event);
    return event.countries.length === 0 && span !== null && intersects(span, since, until);
  }).length;
  const events = regional.filter((event) => {
    const span = eventSpan(event);
    return span !== null && intersects(span, since, until);
  }).sort((a, b) => recentTime(b) - recentTime(a) || compareText(a.id, b.id));
  const mapped = events.filter((event) => positionsOf(event.geometry).length > 0).length;
  const categoryCounts = SITUATION_CATEGORIES.map((category) => ({
    category, count: events.filter((event) => event.category === category).length, timeBasis: PUBLIC_TIME_BASIS[category],
  }));
  const kinds = new Map<string, number>();
  for (const event of events) kinds.set(event.kind, (kinds.get(event.kind) ?? 0) + 1);
  const kindCounts = [...kinds].sort(([a], [b]) => compareText(a, b)).map(([kind, count]) => ({ kind, count }));
  const binHours = options.hours <= 24 ? 1 : options.hours <= 72 ? 3 : options.hours <= 168 ? 6 : 24;
  const timeline: SituationTimelineBin[] = [];
  for (let start = since; start < until; start += binHours * HOUR) {
    const end = Math.min(start + binHours * HOUR, until);
    const bin: SituationTimelineBin = { start: new Date(start).toISOString(), end: new Date(end).toISOString(), count: 0,
      byBasis: { occurred: 0, published: 0, validity: 0, changed: 0 } };
    for (const event of events) {
      const span = eventSpan(event)!;
      if (intersects(span, start, end)) { bin.count += 1; bin.byBasis[PUBLIC_TIME_BASIS[event.category]] += 1; }
    }
    timeline.push(bin);
  }
  const warnings = snapshot.sources.map((source) => sourceWarning(source, now)).filter((warning): warning is string => warning !== null);
  const presentSources = new Set(snapshot.sources.map((source) => source.id));
  for (const id of PUBLIC_SOURCE_IDS) if (!presentSources.has(id)) warnings.push(`${PUBLIC_SOURCE_INFO[id].name}: brak metadanych źródła w zestawie.`);
  const cachedCount = events.filter((event) => event.tags.includes("cached_public_data")).length;
  const validity = events.filter((event) => PUBLIC_TIME_BASIS[event.category] === "validity");
  const activeAdvisories = validity.filter((event) => advisoryState(event, until) === "active").length;
  const expiredAdvisories = validity.filter((event) => advisoryState(event, until) === "expired").length;
  const withdrawnAdvisories = validity.filter((event) => advisoryState(event, until) === "withdrawn").length;
  const limitations = [
    ...snapshot.limitations,
    "Przegląd obejmuje cały opublikowany zestaw, nie tylko pierwsze 300 rekordów listy. Brak rekordów nie oznacza braku zagrożeń.",
    "Okno kończy się w chwili przygotowania zestawu. Świeżość każdego źródła jest osobna; nowy plik nie odświeża automatycznie dowodów.",
    "Trzęsienia i katastrofy: czas początku zdarzenia. Pogoda i lotnictwo: przecięcie okresu ważności. Cyber, internet i pogoda kosmiczna: czas publikacji, nie czas incydentu.",
    "Oś czasu liczy rekordy przecinające przedział. Ostrzeżenia i rekordy z dokładnością do dnia mogą występować w kilku słupkach; suma słupków nie jest liczbą unikalnych zdarzeń.",
    "Wybór wyróżnień: po jednym przedstawicielu kategorii, następnie uzupełnienie do 8; pierwszeństwo mają daty z okna i wysoka waga źródłowa. Starsze nadal ważne ostrzeżenia zajmują najwyżej jedną pozycję na kategorię. To wybór według jawnych reguł, nie ranking ryzyka.",
    "Wyróżnienia pomijają powtarzające się tytuły w tej samej kategorii i krajach oraz raporty z jawną relacją możliwego tego samego zdarzenia. Nie scala to rekordów ani nie potwierdza ich tożsamości.",
    "Liczba kanałów i adresów nie jest liczbą niezależnych potwierdzeń. Agregatory mogą wskazywać te same dane pierwotne.",
  ];
  if (countries !== null) limitations.push(`Zakres ${getScopeLabel(scope)} filtruje wyłącznie jawne kody krajów w źródle. ${unknownCountryCount} rekordów z tego okna bez kraju pominięto; globalne usługi i komunikaty nie są automatycznie przypisywane do regionu.`);
  if (scope === "europe") limitations.push("Europa to jawna lista krajów używana przez monitor, z Cyprem i Kosowem, bez Rosji i Turcji. Nie jest to przecięcie geometrii z kontynentem.");
  if (unknownTimeCount > 0) limitations.push(`${unknownTimeCount} rekordów w tym zakresie geograficznym ma nieznany lub niespójny wymagany czas i nie da się ich przypisać do okna.`);
  if (validity.some((event) => event.valid_to === null)) limitations.push("Część ostrzeżeń nie ma końca ważności. Otwarte okresy uwzględniono tylko przy statusie aktywnym podanym przez źródło; w pozostałych przypadkach uwzględniono wyłącznie znany początek w oknie.");
  if (cachedCount > 0) limitations.push(`${cachedCount} rekordów pochodzi z poprzedniego publicznego odczytu; zachowano ich pierwotne daty dowodów.`);
  if (events.some((event) => !backedByEvidence(event, presentSources, until))) limitations.push("Rekordy bez pasujących dowodów z rozpoznanego źródła nie są automatycznie wyróżniane.");
  if (now < until - 300_000) limitations.push("Zegar urządzenia jest wcześniejszy niż czas zestawu; sprawdź zegar. Okno pozostaje oparte na zestawie.");
  else if (now - until > 3 * HOUR) limitations.push(`Zestaw ma ${Math.floor((now - until) / HOUR)} godzin. Przegląd nie przedstawia bieżącej sytuacji po czasie jego przygotowania.`);
  limitations.push(...warnings);
  return { scope, scopeLabel: getScopeLabel(scope), hours: options.hours,
    since: new Date(since).toISOString(), until: snapshot.generated_at, events,
    highlights: chooseHighlights(events, snapshot, since, until), categoryCounts, timeline, mapped,
    unlocated: events.length - mapped, limitations: [...new Set(limitations)], kindCounts,
    activeAdvisories, expiredAdvisories, withdrawnAdvisories, unknownTimeCount, unknownCountryCount, cachedCount,
    sourceWarnings: warnings };
}

export function formatSituationTime(event: EventDetail, basis: TimeBasis): string {
  if (basis === "validity") return `${formatEventDate(event, "valid_from")} → ${formatEventDate(event, "valid_to")}`;
  return formatEventDate(event, basis === "occurred" ? "occurred_start" : basis === "published" ? "issued_at" : "last_changed_at");
}
const oneLine = (value: string, limit = 500): string => {
  const clean = value.replace(/[\u0000-\u001f\u007f\u2028-\u202e\u2066-\u2069]+/g, " ").replace(/\s+/g, " ").trim();
  return clean.length > limit ? `${clean.slice(0, limit - 1)}…` : clean;
};

/** Local text export only. No AI, sending, private data access or reconstructed timestamps. */
export function createBriefingText(situation: Situation, snapshot: PublicSnapshot, selectedIds?: string[], siteUrl?: string): string {
  if (instant(situation.until) !== instant(snapshot.generated_at)) throw new Error("Briefing i zestaw muszą pochodzić z tego samego czasu przygotowania.");
  const wanted = selectedIds === undefined ? situation.highlights.map(({ event }) => event.id) : selectedIds;
  const eventsById = new Map(situation.events.map((event) => [event.id, event]));
  const uniqueIds = [...new Set(wanted)], selected = uniqueIds.map((id) => eventsById.get(id)).filter((event): event is EventDetail => Boolean(event));
  const included = selected.slice(0, 12), unknown = uniqueIds.length - selected.length;
  const sources = new Map(snapshot.sources.map((source) => [source.id, source]));
  const lines = [
    `SEXTET MONITOR · ${situation.scopeLabel}`,
    `Stan zestawu: ${formatDate(snapshot.generated_at)} (Europe/Warsaw).`,
    `Okno: ${formatDate(situation.since)} → ${formatDate(situation.until)} · ${situation.hours} h.`,
    `W zestawie dla tego okna i zakresu: ${situation.events.length} rekordów; ${situation.mapped} z geometrią, ${situation.unlocated} bez lokalizacji na mapie.`,
    `Wybrano do briefingu: ${included.length}${selected.length > included.length ? ` z ${selected.length}; limit 12` : ""}. To wycinek danych, nie lista wszystkich zagrożeń.`,
    "Daty źródłowe z dokładnością do dnia nie oznaczają znanej godziny. Godziny poniżej: Europe/Warsaw.", "",
  ];
  if (unknown > 0) lines.push(`Pominięto ${unknown} wybranych identyfikatorów spoza bieżącego zakresu.`, "");
  if (included.length === 0) lines.push("Nie wybrano rekordów do briefingu.", "");
  const states: Record<AdvisoryState, string> = { active: "ważne w chwili zestawu", expired: "wygasłe", withdrawn: "odwołane", upcoming: "przed początkiem ważności", unknown: "ważności nie ustalono" };
  included.forEach((event, index) => {
    const basis = PUBLIC_TIME_BASIS[event.category];
    lines.push(`${index + 1}. ${oneLine(event.title)} [${KIND_LABELS[event.kind] || oneLine(event.kind)}]`,
      `Kategoria: ${SITUATION_CATEGORY_LABELS[event.category]}. ${SITUATION_TIME_LABELS[basis]}: ${formatSituationTime(event, basis)}.`,
      `Kraje według źródła: ${event.countries.length ? event.countries.map(countryName).join(", ") : "nie ustalono"}.`);
    if (basis === "validity") lines.push(`Status: ${states[advisoryState(event, situation.until)]}.`);
    else lines.push(`Stan rekordu w zestawie: ${{active:"aktywne",expired:event.kind==="incident" ? "zakończone" : "wygasłe",withdrawn:"odwołane",unknown:"nie ustalono"}[event.lifecycle_status]}.`);
    if (basis === "published") lines.push(`Czas wystąpienia: ${formatEventDate(event, "occurred_start")}; data publikacji nie jest czasem incydentu.`);
    const reason = situation.highlights.find((item) => item.event.id === event.id)?.reason;
    if (reason) lines.push(`Powód wyróżnienia: ${reason}`);
    lines.push(`Kanały źródłowe: ${event.source_ids.map((id) => oneLine(sources.get(id)?.name || id, 200)).join(", ")}.`);
    const linkedEvidence = event.evidence.filter((evidence) => event.source_ids.includes(evidence.source_id) && sources.has(evidence.source_id));
    const links = [...new Set([event.source_url, ...linkedEvidence.map((evidence) => evidence.source_url)].map(safeHttpUrl).filter((url): url is string => url !== null))].slice(0, 4);
    lines.push(links.length ? `Oryginalne źródło: ${links.join(" | ")}` : "Oryginalny adres: nie ustalono.");
    const clocks = linkedEvidence.map((evidence) => instant(evidence.retrieved_at)).filter((clock): clock is number => clock !== null);
    lines.push(clocks.length ? `Najstarszy / najnowszy odczyt dowodów: ${formatDate(new Date(Math.min(...clocks)).toISOString())} / ${formatDate(new Date(Math.max(...clocks)).toISOString())}.` : "Czas odczytu dowodów: nie ustalono.");
    if (event.tags.includes("cached_public_data")) lines.push("Uwaga: dane z wcześniejszego publicznego odczytu; nowy zestaw nie odświeża dowodów.");
    lines.push("");
  });
  lines.push("JAK CZYTAĆ TEN BRIEF", "Trzęsienia i katastrofy liczone według początku zdarzenia; pogoda i lotnictwo według ważności; cyber, internet i pogoda kosmiczna według publikacji.",
    "Kanały i adresy mogą powielać jedno źródło pierwotne. Nie są niezależnymi potwierdzeniami. Nie wyliczono prawdopodobieństwa, ryzyka ani przyczyn.");
  if (getScopeCountries(situation.scope) !== null) lines.push("Zakres obejmuje wyłącznie jawne kody krajów w źródle. Rekordów bez kraju i usług globalnych nie przypisano automatycznie do regionu.");
  lines.push(...situation.limitations.filter((value) => value.startsWith("Zestaw ma ") || value.startsWith("Zegar urządzenia")));
  lines.push("", "ODCZYTY ŹRÓDEŁ (nie stan usług w tej chwili)");
  for (const source of snapshot.sources) lines.push(`${oneLine(source.name, 200)}: ${STATE_LABELS[source.enabled ? source.status : "disabled"]}; ostatni udany odczyt ${formatDate(source.last_success_at)}.`);
  if (situation.sourceWarnings.length) lines.push("", ...situation.sourceWarnings.map((warning) => oneLine(warning, 700)));
  if (snapshot.limitations.length) lines.push("", "Ograniczenia opublikowanego zestawu:", ...snapshot.limitations.slice(0, 12).map((line) => oneLine(line, 700)));
  const url = safeHttpUrl(siteUrl);
  if (url) lines.push("", `Pełny monitor: ${url}`);
  return lines.join("\n");
}
