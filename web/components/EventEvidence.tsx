import { useState } from "react";
import type { EventDetail, Evidence } from "@/lib/contracts";
import { CATEGORY_LABELS, SEVERITY_LABELS } from "@/lib/filters";
import { CHANGE_LABELS, countryName, formatDate, formatEventDate, KIND_LABELS, LIFECYCLE_LABELS, PRECISION_LABELS, readableUnknown, safeHttpUrl } from "@/lib/format";
import { Icon } from "./Icon";

/** Only a matching, completed user selection may move focus on a small screen. */
export function shouldRevealEvidence(requestedId:string|null,selectedId:string|null,detailId:string|null,loading:boolean):boolean {
  return Boolean(requestedId && requestedId===selectedId && detailId===selectedId && !loading);
}

function OriginalLink({url,children}:{url:string|null|undefined;children:React.ReactNode}) {
  const safe=safeHttpUrl(url);
  return safe ? <a className="external-link" href={safe} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer">{children}<Icon name="link" size={13}/></a> : <span className="field-help">Brak poprawnego odnośnika HTTP(S)</span>;
}
function RawRecord({record}:{record:Evidence}) {
  const [expanded,setExpanded]=useState(false);
  const raw=expanded ? readableUnknown(record.raw) : "";
  return <details className="raw-record" onToggle={(event)=>setExpanded(event.currentTarget.open)}><summary>Surowy rekord źródłowy</summary>{expanded && <><pre tabIndex={0}>{raw.slice(0,120000)}</pre>{raw.length>120000 && <p className="field-help">Podgląd ograniczono do 120 000 znaków. Pełny rekord pozostaje w lokalnej bazie.</p>}</>}</details>;
}
export default function EventEvidence({detail,readAt,selected,loading,error,outsideFilter,onSelect,onRetry,publicMode=false}:{detail:EventDetail|null;readAt?:string|null;publicMode?:boolean;selected:boolean;loading:boolean;error:string|null;outsideFilter:boolean;onSelect:(id:string)=>void;onRetry:()=>void}) {
  if (!selected) return <div className="evidence-empty"><span className="eyebrow">Najpierw źródło</span><h2>Wybierz rekord</h2><p>Kliknij pozycję na liście lub obszar na mapie, aby zobaczyć oryginalne źródła i daty{publicMode ? " zestawu." : " oraz historię zmian."}</p><div className="evidence-principles"><p>Komunikat nie musi oznaczać incydentu.</p><p>Dwa odnośniki nie muszą oznaczać dwóch niezależnych źródeł.</p><p>Nieznana lokalizacja pozostaje poza mapą.</p></div></div>;
  if (loading && !detail) return <div className="evidence-loading" role="status"><p>Ładowanie dowodów…</p><div className="skeleton-block"/><div className="skeleton-block"/></div>;
  if (!detail) return <div className="empty-state error-state" role="alert"><strong>Nie można odczytać rekordu</strong><p>{error || "Serwer nie zwrócił szczegółów."}</p><button onClick={onRetry}>Spróbuj ponownie</button></div>;
  const dates: Array<[string,keyof EventDetail]> = [
    ["Wystąpienie od","occurred_start"],["Wystąpienie do","occurred_end"],["Opublikowano","issued_at"],
    ["Aktualizacja źródła","source_updated_at"],["Ważne od","valid_from"],["Ważne do","valid_to"],
    [publicMode ? "Przygotowano rekord zestawu" : "Pierwszy odczyt","first_seen_at"],["Ostatnia zmiana w monitorze","last_changed_at"],[publicMode ? "Odczyt źródła dla zestawu" : "Ostatnio widziane","last_seen_at"],
  ];
  return <article className="evidence-detail" data-event-id={detail.id} aria-label="Szczegóły i dowody" aria-busy={loading}>
    <p className="field-help" role="status">{loading ? "Odświeżanie dowodów. Poniżej poprzedni odczyt." : publicMode ? `Zestaw przygotowany: ${formatDate(readAt)}` : readAt ? `Odczyt panelu: ${formatDate(readAt)}` : "Dowody z lokalnej bazy."}</p>
    {error && <div className="inline-error" role="alert"><p>Nie potwierdzono aktualności dowodów. Widoczny jest poprzedni odczyt. {error}</p><button onClick={onRetry}>Ponów odczyt dowodów</button></div>}
    <div className="detail-heading"><div className="detail-kicker"><span>{CATEGORY_LABELS[detail.category] || detail.category}</span><span>{KIND_LABELS[detail.kind] || detail.kind}</span></div><h2>{detail.title}</h2><div className="detail-chips"><span className={`severity severity-${detail.severity}`}>{SEVERITY_LABELS[detail.severity] || "Nieokreślona"}</span><span className="lifecycle">{LIFECYCLE_LABELS[detail.lifecycle_status] || detail.lifecycle_status}</span></div></div>
    {outsideFilter && <p className="context-notice">Otwarty rekord pochodzi spoza bieżącego wyniku filtrów.</p>}
    {publicMode && detail.tags.includes("cached_public_data") && <p className="context-notice">Poprzedni publiczny odczyt; źródło nie odpowiedziało. Daty dowodów nie są bieżące. Status rekordu pochodzi z tamtego odczytu.</p>}
    {!publicMode && detail.change_type==="initial_import" && <p className="context-notice">Import początkowy. Pierwsze pojawienie się w bazie nie oznacza, że zdarzenie właśnie wystąpiło.</p>}
    {detail.tags.includes("country_geometry_not_fir") && <p className="context-notice">Mapa pokazuje granice kraju jako kontekst. Nie przedstawia granic FIR ani dokładnego obszaru ostrzeżenia lotniczego.</p>}
    {detail.tags.includes("representative_point_not_extent") && <p className="context-notice">Pozycja jest orientacyjnym punktem źródłowym. Nie opisuje zasięgu zdarzenia lub zagrożenia.</p>}
    <p className="event-description">{detail.description || "Źródło nie podało dodatkowego opisu."}</p>
    <OriginalLink url={detail.source_url}>{detail.source_url==="https://hydro.imgw.pl/#/warnings/hydro" ? "Otwórz listę ostrzeżeń IMGW" : "Otwórz oryginalny komunikat"}</OriginalLink>
    <section className="detail-section"><h3>Co wiadomo</h3><dl className="data-pairs">
      <dt>Obszar</dt><dd>{detail.countries.length ? detail.countries.map(countryName).join(", ") : "Nie ustalono"}</dd>
      <dt>Lokalizacja</dt><dd>{detail.geometry ? PRECISION_LABELS[detail.location_precision] || detail.location_precision : "Brak geometrii źródłowej"}</dd>
      <dt>Dokładność czasu</dt><dd>{PRECISION_LABELS[detail.time_precision] || detail.time_precision || "Nieustalona"}</dd>
      <dt>Niezależne źródła</dt><dd>{detail.independent_source_count}</dd>
      <dt>Weryfikacja</dt><dd>{({single_source:"Jedno źródło",corroborated:"Potwierdzenie niezależne",unverified:"Niezweryfikowane",source_reported:"Zapis źródłowy",reported:"Zgłoszone przez źródło",official_warning:"Oficjalny komunikat ostrzegawczy",published_by_cert_pl:"Publikacja CERT Polska",unknown:"Nieustalona"} as Record<string,string>)[detail.verification_status] || detail.verification_status}</dd>
      <dt>Anomalia</dt><dd>Nie wyznaczono</dd>
    </dl><p className="field-help">Brak wyniku anomalii nie oznacza stanu normalnego.</p></section>
    <section className="detail-section"><h3>Waga i jej pochodzenie</h3><p>{detail.severity_reason || "Źródło nie podało uzasadnienia wagi."}</p><dl className="data-pairs"><dt>Wartość źródłowa</dt><dd className="raw-value">{readableUnknown(detail.original_severity)}</dd></dl></section>
    <section className="detail-section"><h3>Czas i ważność</h3><dl className="data-pairs">{dates.filter(([,key])=>!publicMode || key!=="last_changed_at").map(([label,key])=><div className="data-pair" key={key}><dt>{label}</dt><dd>{key==="valid_to" && detail.valid_to===null && detail.tags.includes("until_revoked") ? "Do odwołania według źródła" : formatEventDate(detail,key as Parameters<typeof formatEventDate>[1])}</dd></div>)}</dl><p className="field-help">Godziny: Europe/Warsaw. Daty dzienne zachowują datę źródłową, bez dopisywania godziny.</p></section>
    <section className="detail-section"><h3>Dowody <span>{detail.evidence.length}</span></h3>
      {publicMode && <p className="field-help">Zestaw zawiera przetworzone pola. Pełny komunikat pod odnośnikiem źródła; surowe payloady i historia prywatnego monitora nie są publikowane. Każdy dowód ma własną datę pobrania. Nowy plik zestawu nie zmienia dat starszych odczytów.</p>}
      {detail.evidence.length===0 && <p>Brak dowodów w odpowiedzi API. Nie dopisujemy potwierdzeń.</p>}
      {detail.evidence.map((record,index)=><div className="evidence-record" key={`${record.source_id}:${record.provider_record_id}`}>
        <div className="evidence-record-title"><span className="record-number">{String(index+1).padStart(2,"0")}</span><strong>{record.source_name}</strong></div>
        <OriginalLink url={record.source_url}>{record.source_id==="imgw_hydro" ? "Lista ostrzeżeń IMGW · ID komunikatu poniżej" : "Źródło pierwotne"}</OriginalLink>
        <dl className="data-pairs"><dt>Pobrano</dt><dd>{formatDate(record.retrieved_at)}</dd>{record.source_snapshot_at && <><dt>{record.source_id==="cisa_kev" ? "Snapshot katalogu u źródła" : "Snapshot u źródła"}</dt><dd>{formatDate(record.source_snapshot_at)}</dd></>}<dt>ID dostawcy</dt><dd className="mono">{record.provider_record_id}</dd><dt>Pochodzenie</dt><dd>{record.origins.length ? record.origins.join(", ") : "Nie ustalono"}</dd></dl>
        <p className="attribution">{record.attribution}</p>
        {record.license_url && <OriginalLink url={record.license_url}>Warunki użycia danych</OriginalLink>}
        {!publicMode && <RawRecord record={record}/>}<p className="payload-hash" title={record.payload_hash}>SHA-256 {record.payload_hash}</p>
      </div>)}
    </section>
    {!publicMode && <section className="detail-section"><h3>Historia zmian <span>{detail.revisions.length}</span></h3>{detail.revisions.length ? <ol className="revision-list">{detail.revisions.map((revision)=><li key={revision.id}><time dateTime={revision.recorded_at}>{formatDate(revision.recorded_at)}</time><strong>{CHANGE_LABELS[revision.change_type] || revision.change_type}</strong><p>{revision.summary}</p></li>)}</ol> : <p>API nie zwróciło zapisanej historii.</p>}</section>}
    {detail.relations.length>0 && <section className="detail-section"><h3>Powiązane zapisy</h3><p className="field-help">Powiązanie tematyczne lub czasowe nie potwierdza wspólnej przyczyny.</p>{detail.relations.map((relation)=><div className="relation-row" key={relation.event_id}><span className="relation-type">{relation.relation_type==="same_event" ? "To samo zdarzenie" : "Zapis powiązany"}</span><button className="text-button" onClick={()=>onSelect(relation.event_id)}>{relation.title}</button><p>{relation.reason}</p>{relation.distance_km!=null && <small>{relation.distance_km.toFixed(1)} km między zapisami</small>}</div>)}</section>}
  </article>;
}
