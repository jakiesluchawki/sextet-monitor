import type { EventDetail } from "@/lib/contracts";
import { CATEGORY_SHORT, SEVERITY_LABELS } from "@/lib/filters";
import { countryName, eventDateField, formatEventDate, KIND_LABELS, LIFECYCLE_LABELS } from "@/lib/format";
import { PUBLIC_TIME_BASIS } from "@/lib/public-snapshot";
import { eventSourceNames } from "@/lib/public-view";

export function PinButton({event,pinned,onPin}:{event:EventDetail;pinned:boolean;onPin:(id:string)=>void}){
  return <button className="pin-button" aria-label={`${pinned ? "Odepnij" : "Przypnij"}: ${event.title}`} title={pinned ? "Usuń z przypiętych" : "Zapisz na tym urządzeniu; briefing uwzględnia obszar i czas"} aria-pressed={pinned} onClick={()=>onPin(event.id)}><svg width="17" height="17" viewBox="0 0 24 24" fill={pinned ? "currentColor" : "none"} stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="m12 3 2.8 5.8 6.4.9-4.6 4.5 1.1 6.3-5.7-3-5.7 3 1.1-6.3L2.8 9.7l6.4-.9Z"/></svg></button>;
}

export default function SignalRows({events,selectedId,pinnedIds,onSelect,onPin,reasons,compact=false,changedIds=[],addedIds=[]}:{events:EventDetail[];selectedId:string|null;pinnedIds:readonly string[];onSelect:(id:string)=>void;onPin:(id:string)=>void;reasons?:Record<string,string>;compact?:boolean;changedIds?:readonly string[];addedIds?:readonly string[]}){
  if(!events.length)return <div className="signal-empty"><strong>Brak pasujących zapisów</strong><p>Poszerz obszar lub czas. Brak zapisu w podłączonych źródłach nie oznacza braku zdarzenia.</p></div>;
  return <div className={`signal-rows ${compact ? "compact" : ""}`}>{events.map((event,index)=>{
    const basis=PUBLIC_TIME_BASIS[event.category];
    return <article key={event.id} className={`signal-row ${selectedId===event.id ? "is-selected" : ""}`} data-category={event.category}>
      {compact && <span className="signal-index" aria-hidden="true">{String(index+1).padStart(2,"0")}</span>}
      <button className="signal-open" onClick={()=>onSelect(event.id)} aria-pressed={selectedId===event.id}>
        <span className="signal-meta"><i className={`category-dot category-${event.category}`}/>{CATEGORY_SHORT[event.category]}<span className="signal-kind">{KIND_LABELS[event.kind] || event.kind}</span>{addedIds.includes(event.id) ? <em>Nowy w zestawie</em> : changedIds.includes(event.id) ? <em>Zmieniony zapis</em> : null}</span>
        <strong className="signal-title">{event.title}</strong>
        {reasons?.[event.id] && <span className="signal-reason">{reasons[event.id]}</span>}
        <span className="signal-provenance">{eventSourceNames(event)}{event.tags.includes("cached_public_data") && <em> · starszy odczyt</em>}</span>
        <span className="signal-lifecycle">{event.lifecycle_status==="expired" && event.kind==="incident" ? "Zakończone" : LIFECYCLE_LABELS[event.lifecycle_status]} · stan w zestawie</span>
        <span className="signal-place">{event.countries.length ? event.countries.map(countryName).join(", ") : "Obszar nieustalony"}{!event.geometry ? " · bez pozycji" : ""}</span>
      </button>
      <div className="signal-side"><PinButton event={event} pinned={pinnedIds.includes(event.id)} onPin={onPin}/><span className={`severity severity-${event.severity}`}>{SEVERITY_LABELS[event.severity]}</span><time dateTime={event[eventDateField(basis)] || undefined}>{formatEventDate(event,eventDateField(basis),true)}</time><span className="signal-time-basis">{{occurred:"wystąpienie",validity:"ważne od",published:"publikacja"}[basis]}</span></div>
    </article>;
  })}</div>;
}
