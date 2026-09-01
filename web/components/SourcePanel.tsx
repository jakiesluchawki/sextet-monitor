import type { SourceStatus } from "@/lib/contracts";
import { formatDate, readableUnknown, safeHttpUrl, sourceTone, STATE_LABELS } from "@/lib/format";
import { Icon } from "./Icon";
export default function SourcePanel({sources,loading,error,onRetry,snapshotAt}:{snapshotAt?:string;sources:SourceStatus[];loading:boolean;error:string|null;onRetry:()=>void}) {
  return <section className="source-panel" aria-label="Stan źródeł">
    <div className="panel-intro"><h2>{snapshotAt ? "Źródła zestawu" : "Stan źródeł"}</h2><p>{snapshotAt ? `Odczyty podczas przygotowania zestawu ${formatDate(snapshotAt)}. To nie sprawdzenie bieżącej dostępności źródeł. Strona nie uruchamia workera.` : "Odczyt źródła to nie ocena bezpieczeństwa. Czas pobrania i wiek treści są odrębne."}</p></div>
    {loading && <p className="loading-note" role="status">Sprawdzanie źródeł…</p>}
    {error && <div className="inline-error" role="alert"><p>{error}</p><button onClick={onRetry}>Ponów odczyt</button></div>}
    {!loading && !error && !sources.length && <p className="empty-state">API nie zwróciło konfiguracji źródeł.</p>}
    {sources.map((source)=>{
      const license=safeHttpUrl(source.license_url);
      return <details key={source.id} className="source-row" open={source.status==="error" || source.status==="needs_credentials"}>
        <summary><span className="source-name">{source.name}</span><span className={`source-state tone-${sourceTone(source.status)}`}><span className="state-dot"/>{snapshotAt && source.status==="ok" ? "Odczyt w zestawie: OK" : STATE_LABELS[source.status] || "Nieustalony"}</span></summary>
        <div className="source-body">
          {source.status==="disabled" && <p>Źródło jest wyłączone. Nie uczestniczy w tym wyniku.</p>}
          {source.status==="needs_credentials" && <p>Wymagany token tylko do odczytu, ustawiany po stronie serwera. Interfejs nie zbiera kluczy.</p>}
          {source.status==="ok_empty" && <p>Udane pobranie nie zwróciło rekordów. To nie oznacza braku zagrożeń.</p>}
          {source.status==="pending" && <p>Brak zakończonego pierwszego pobrania.</p>}
          {source.error && <p className="source-error">{source.error}</p>}
          <dl className="data-pairs">
            <dt>Ostatnia próba</dt><dd>{formatDate(source.last_attempt_at)}</dd>
            <dt>Udane pobranie</dt><dd>{formatDate(source.last_success_at)}</dd>
            <dt>Najnowsza treść</dt><dd>{formatDate(source.newest_content_at)}</dd>
            {!snapshotAt && <><dt>Następna próba</dt><dd>{!source.enabled || source.status==="disabled" ? "Nie zaplanowano (źródło wyłączone)" : source.status==="needs_credentials" ? "Nie zaplanowano (brak tokenu)" : formatDate(source.next_due_at)}</dd></>}
            <dt>Rekordy źródłowe</dt><dd>{source.last_success_at ? source.record_count : "Jeszcze nie pobrano"}</dd>
            {!snapshotAt && <><dt>Planowany odstęp</dt><dd>{source.poll_interval_seconds > 0 ? `${Math.round(source.poll_interval_seconds/60)} min` : "Nie podano"}</dd></>}
          </dl>
          <div className="source-coverage"><strong>Pokrycie</strong><p>{readableUnknown(source.coverage)}</p></div>
          <p className="attribution">{source.attribution}</p>
          {license ? <a className="external-link" href={license} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer">{source.license_name || "Warunki źródła"}<Icon name="link" size={13}/></a> : <span className="field-help">{source.license_name || "Warunki nie zostały podane przez API"}</span>}
        </div>
      </details>;
    })}
  </section>;
}
