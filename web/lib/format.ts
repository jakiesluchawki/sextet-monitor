import type { EventSummary, SourceState, SourceStatus, TimeBasis } from "./contracts";
import { COUNTRY_NAMES_PL } from "./countries";

const dates = new Intl.DateTimeFormat("pl-PL", {
  timeZone: "Europe/Warsaw", day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit",
});
const shortDates = new Intl.DateTimeFormat("pl-PL", {
  timeZone: "Europe/Warsaw", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
});
const days = new Intl.DateTimeFormat("pl-PL", {
  timeZone: "Europe/Warsaw", day: "numeric", month: "long", year: "numeric",
});
export function formatDate(value: string | null | undefined, short = false): string {
  if (!value || !Number.isFinite(Date.parse(value))) return "Nie ustalono";
  return (short ? shortDates : dates).format(new Date(value));
}
export function formatDay(value: string | null): string {
  return value && Number.isFinite(Date.parse(value)) ? days.format(new Date(value)) : "Czas nieustalony";
}
export function countryName(code: string): string {
  return COUNTRY_NAMES_PL[code.toUpperCase()] || code;
}
export function safeHttpUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    if (!["https:", "http:"].includes(url.protocol) || url.username || url.password) return null;
    return url.href;
  } catch { return null; }
}
export function eventDateField(basis: TimeBasis): "occurred_start" | "last_changed_at" | "issued_at" | "valid_from" {
  return ({occurred:"occurred_start", changed:"last_changed_at", published:"issued_at", validity:"valid_from"} as const)[basis];
}
export function eventTime(event: EventSummary, basis: TimeBasis): string | null {
  return event[eventDateField(basis)];
}
export const STATE_LABELS: Record<SourceState, string> = {
  pending: "Oczekuje", ok: "Działa", ok_empty: "Pobrano, bez rekordów", partial: "Częściowe dane",
  error: "Błąd pobierania", stale: "Dane nieaktualne", needs_credentials: "Brak tokenu", disabled: "Wyłączone",
};
export function sourceTone(state: SourceState): string {
  if (state === "ok" || state === "ok_empty") return "ok";
  if (state === "error") return "error";
  if (state === "stale" || state === "partial" || state === "needs_credentials") return "warning";
  return "muted";
}
/** Public health describes the saved fetch, not current availability or absence of hazards. */
export function publicSourceHealth(source:SourceStatus|null,records:number) {
  const healthy=Boolean(source?.enabled && source.status==="ok" && source.last_success_at && source.record_count>0 && records>0);
  const empty=Boolean(source?.enabled && source.last_success_at && records===0 && (source.status==="ok" || source.status==="ok_empty"));
  let label="Brak metadanych",tone="warning";
  if(source){
    label=STATE_LABELS[source.status];tone=sourceTone(source.status);
    if(!source.enabled || source.status==="disabled"){label="Wyłączone";tone="muted";}
    else if(healthy){label="Udany odczyt";tone="ok";}
    else if(empty){label="Odczyt bez rekordów";tone="muted";}
    else if(source.status==="ok" || source.status==="ok_empty"){label="Odczyt niepotwierdzony";tone="warning";}
  }
  return {healthy,empty,label,tone};
}
export function coverageWarnings(sources: SourceStatus[]): SourceStatus[] {
  return sources.filter((source) => source.enabled && source.status !== "ok" && source.status !== "ok_empty");
}
export const KIND_LABELS: Record<string, string> = {
  incident: "Zdarzenie", advisory: "Ostrzeżenie", vulnerability: "Podatność",
  vulnerability_notice: "Podatność", measurement: "Pomiar", observation: "Obserwacja",
};
export const LIFECYCLE_LABELS: Record<string, string> = {
  active: "Aktywne", expired: "Wygasłe", withdrawn: "Odwołane", unknown: "Status nieustalony",
};
export const CHANGE_LABELS: Record<string, string> = {
  initial_import: "Import początkowy", created: "Dodano", new: "Dodano", updated: "Zaktualizowano",
  revised: "Zmieniono", withdrawn: "Odwołano", expired: "Wygasło", unchanged: "Bez zmiany",
};
export const PRECISION_LABELS: Record<string, string> = {
  exact: "Dokładna", precise: "Dokładna", point: "Punkt źródłowy", approximate: "Przybliżona",
  area: "Obszar", polygon: "Obszar źródłowy", country: "Kraj", regional: "Region", region: "Region",
  unknown: "Nieustalona", none: "Brak", not_applicable: "Nie dotyczy", day: "Dzień", hour: "Godzina", minute: "Minuta", second: "Sekunda",
};
export function readableUnknown(value: unknown): string {
  if (value == null || value === "") return "Nie podano";
  if (typeof value === "string") return value;
  try { return JSON.stringify(value, null, 2); } catch { return "Nie można odczytać wartości"; }
}

type EventDateField = "occurred_start" | "occurred_end" | "issued_at" | "source_updated_at" | "valid_from" | "valid_to" | "first_seen_at" | "last_seen_at" | "last_changed_at";
const sourceDayFormatter = new Intl.DateTimeFormat("pl-PL", { timeZone:"UTC", year:"numeric", month:"2-digit", day:"2-digit" });
export function formatEventDate(event: EventSummary, field: EventDateField, short=false): string {
  const value=event[field];
  if (!value || !Number.isFinite(Date.parse(value))) return "Nie ustalono";
  if (field==="valid_to" && event.tags.includes("valid_to_exclusive_day_boundary")) {
    return sourceDayFormatter.format(new Date(Date.parse(value)-1)) + " (koniec dnia)";
  }
  const sourceField=!["first_seen_at","last_seen_at","last_changed_at"].includes(field);
  if (sourceField && event.time_precision==="day") return sourceDayFormatter.format(new Date(value)) + (short ? "" : " (data źródłowa)");
  return formatDate(value,short);
}

export function sourceOverview(sources: SourceStatus[]): {active:number;responding:number;state:"unknown"|"disabled"|"partial"|"ok"} {
  const active=sources.filter((source)=>source.enabled);
  const responding=active.filter((source)=>source.status==="ok" || source.status==="ok_empty");
  const state=sources.length===0 ? "unknown" : active.length===0 ? "disabled" : responding.length<active.length ? "partial" : "ok";
  return {active:active.length,responding:responding.length,state};
}
