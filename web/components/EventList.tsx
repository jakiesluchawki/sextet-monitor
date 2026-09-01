import type { EventSummary, SourceStatus, TimeBasis } from "@/lib/contracts";
import { CATEGORY_SHORT, SEVERITY_LABELS } from "@/lib/filters";
import { countryName, coverageWarnings, eventDateField, eventTime, formatDay, formatEventDate, KIND_LABELS } from "@/lib/format";

export default function EventList({events,selectedId,basis,mode,loading,error,sources,onSelect,onRetry,publicMode=false}:{publicMode?:boolean;events:EventSummary[];selectedId:string|null;basis:TimeBasis;mode:"list"|"timeline";loading:boolean;error:string|null;sources:SourceStatus[];onSelect:(id:string)=>void;onRetry:()=>void}) {
  if (loading) return <div className="list-skeleton" role="status" aria-label="Pobieranie zdarzeń"><p>{publicMode ? "Odczyt zapisanego zestawu…" : "Pobieranie aktualnego wyniku…"}</p>{[0,1,2,3].map((index)=><div key={index} className="skeleton-row"><i/><i/><i/></div>)}</div>;
  if (error) return <div className="empty-state error-state" role="alert"><strong>Nie można odczytać zdarzeń</strong><p>{error}</p><button onClick={onRetry}>Spróbuj ponownie</button></div>;
  if (!events.length) {
    const pending = sources.some((source)=>source.enabled && source.status==="pending");
    const incomplete = coverageWarnings(sources).length > 0;
    return <div className="empty-state"><strong>{pending ? "Oczekiwanie na dane źródłowe" : "Brak rekordów dla tych filtrów"}</strong><p>{pending ? publicMode ? "Zestaw nie zawiera zakończonego odczytu części źródeł." : "Worker nie zakończył jeszcze pierwszego pobrania części źródeł." : "Zmień zakres czasu, obszar lub kategorię."}</p><p className="coverage-note">{incomplete ? "Pokrycie jest niepełne. Sprawdź zakładkę Źródła." : "Brak wyników nie potwierdza braku zdarzeń. Monitor obejmuje tylko podłączone źródła."}</p></div>;
  }
  const row=(event:EventSummary)=><button key={event.id} className={`event-row ${selectedId===event.id ? "selected" : ""}`} onClick={()=>onSelect(event.id)} aria-pressed={selectedId===event.id}>
    <span className="event-time">{formatEventDate(event,eventDateField(basis),true)}</span>
    <span className="event-copy"><span className="event-title">{event.title}</span><span className="event-meta"><span className={`category-dot category-${event.category}`}/>{CATEGORY_SHORT[event.category] || event.category}<span>·</span>{KIND_LABELS[event.kind] || event.kind}<span>·</span>{event.independent_source_count} {event.independent_source_count===1 ? "źródło niezależne" : "niezależne źródła"}{!publicMode && event.change_type==="initial_import" && <span className="import-tag">Import</span>}{publicMode && event.tags.includes("cached_public_data") && <span className="import-tag">Poprzedni odczyt</span>}</span></span>
    <span className="event-location">{event.countries.length ? event.countries.map(countryName).join(", ") : "Kraj nieustalony"}{!event.geometry && <small>Bez pozycji na mapie</small>}</span>
    <span className={`severity severity-${event.severity}`}>{SEVERITY_LABELS[event.severity] || "Nieokreślona"}</span>
  </button>;
  if (mode==="list") return <div className="event-list"><div className="event-table-head" aria-hidden="true"><span>{{occurred:"Wystąpienie",changed:"Zmiana",published:"Publikacja",validity:"Ważne od"}[basis]}</span><span>Zdarzenie / komunikat</span><span>Obszar</span><span>Waga</span></div>{events.map(row)}</div>;
  const ordered = [...events].sort((a,b)=>(Date.parse(eventTime(b,basis) || "") || 0) - (Date.parse(eventTime(a,basis) || "") || 0));
  const groups = new Map<string,EventSummary[]>();
  for (const event of ordered) { const day=formatDay(eventTime(event,basis)); groups.set(day,[...(groups.get(day)||[]),event]); }
  return <div className="timeline-list">{[...groups].map(([day,items])=><section key={day} className="timeline-group"><h3>{day}<span>{items.length} rekordów w wyniku</span></h3>{items.map(row)}</section>)}</div>;
}
