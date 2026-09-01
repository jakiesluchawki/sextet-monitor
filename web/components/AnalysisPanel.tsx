import type { BriefingResponse, Fact, QueryResponse } from "@/lib/contracts";
import { countryName, formatDate, safeHttpUrl } from "@/lib/format";
import { Icon } from "./Icon";

function Facts({facts,onSelect}:{facts:Fact[];onSelect:(id:string)=>void}) {
  return <ol className="fact-list">{facts.map((fact,index)=><li key={`${fact.event_id}:${index}`}><p>{fact.text}</p><div className="fact-actions">{fact.event_id && <button className="text-button" onClick={()=>onSelect(fact.event_id)}>Pokaż dowody<Icon name="arrow" size={13}/></button>}{fact.source_urls.map(safeHttpUrl).filter((url):url is string=>Boolean(url)).map((url,i)=><a key={url} href={url} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer">Źródło {i+1}<Icon name="link" size={12}/></a>)}</div></li>)}</ol>;
}
export default function AnalysisPanel({query,briefing,latestKnown,loading,briefLoading,error,onSelect,briefingCountry}:{query:QueryResponse|null;briefing:BriefingResponse|null;latestKnown:boolean;loading:boolean;briefLoading:boolean;error:string|null;onSelect:(id:string)=>void;briefingCountry?:string}) {
  const savedScope=briefing?.scope ? briefing.scope.country ? countryName(briefing.scope.country) : "Cały świat" : "Obszar nie został zapisany w tym briefingu";
  const explanation=query?.query_explanation?.trim();
  const extraExplanation=explanation && !query?.answer.replace(/\s+/g," ").includes(explanation.replace(/\s+/g," ")) ? explanation : null;
  return <section className="analysis-panel" aria-label="Zapytania i briefing">
    <div className="panel-intro"><h2>Zapytania i briefing</h2><p>Odpowiedzi z zapisanych obserwacji. Parser regułowy, AI wyłączone.</p></div>
    <p className="briefing-scope-note"><strong>Zakres następnego briefingu: {briefingCountry ? countryName(briefingCountry) : "cały świat"}.</strong> Zmiany od poprzedniego briefingu dla tego obszaru. Pierwszy briefing obejmuje ostatnie 24 godziny. Pomija bieżące filtry kategorii, promienia, regionu, wagi i liczby źródeł oraz zakres czasu widoku.</p>
    {error && <p className="inline-error" role="alert">{error}</p>}
    {loading && <p className="loading-note" role="status">Sprawdzanie zapytania w lokalnych danych…</p>}
    {query && <section className="analysis-section"><div className="analysis-title"><h3>{query.supported ? "Wynik zapytania" : "Poza zakresem monitora"}</h3><time dateTime={query.generated_at}>{formatDate(query.generated_at,true)}</time></div><p className="analysis-answer">{query.answer}</p>
      {extraExplanation && <p className="field-help">{extraExplanation}</p>}
      {query.supported && query.facts.length>0 && <><h4>Fakty ze źródeł</h4><Facts facts={query.facts} onSelect={onSelect}/></>}
      {query.inferences.length>0 && <><h4>Wnioski, nie fakty źródłowe</h4><ul className="plain-list">{query.inferences.map((item,i)=><li key={i}>{item}</li>)}</ul></>}
      {query.limitations.length>0 && <div className="limitations"><h4>Ograniczenia odpowiedzi</h4><ul>{query.limitations.map((item,i)=><li key={i}>{item}</li>)}</ul></div>}
    </section>}
    {briefLoading && <p className="loading-note" role="status">Przygotowanie briefingu z zapisanych danych…</p>}
    {briefing ? <section className="analysis-section"><div className="analysis-title"><h3>Ostatni briefing</h3><time dateTime={briefing.generated_at}>{formatDate(briefing.generated_at,true)}</time></div><p className="field-help"><strong>Zakres zapisanego briefingu: {savedScope}</strong></p><p className="field-help">{formatDate(briefing.since)} → {formatDate(briefing.until)} · Europe/Warsaw</p><p className="analysis-answer">{briefing.answer}</p>
      {typeof briefing.processed_count==="number" && <p className="field-help">Przetworzono {briefing.processed_count} rekordów, opisano {briefing.facts.length}.{Boolean(briefing.omitted_fact_count) && <> Pozostałe {briefing.omitted_fact_count} rekordów z odnośnikami pominięto w krótkiej narracji.</>}</p>}
      {briefing.sections.map((section,index)=><div className="brief-section" key={index}><h4>{section.title}</h4>{section.items.length>0 ? <ul>{section.items.map((item,i)=><li key={`${item.event_id}:${i}`}><p>{item.text}</p>{item.event_id && <button className="text-button" onClick={()=>onSelect(item.event_id)}>Pokaż dowody<Icon name="arrow" size={13}/></button>}</li>)}</ul> : <p className="field-help">Brak zapisów w tej sekcji.</p>}</div>)}
      {briefing.facts.length>0 && <><h4>Fakty i odnośniki</h4><Facts facts={briefing.facts} onSelect={onSelect}/></>}
      {(briefing.inferences?.length || 0)>0 && <><h4>Wnioski, nie fakty źródłowe</h4><ul className="plain-list">{briefing.inferences?.map((item,i)=><li key={i}>{item}</li>)}</ul></>}
      {briefing.limitations.length>0 && <div className="limitations"><h4>Ograniczenia</h4><ul>{briefing.limitations.map((item,i)=><li key={i}>{item}</li>)}</ul></div>}
    </section> : !briefLoading && <div className="empty-state compact"><strong>{latestKnown ? "Brak wcześniejszego briefingu" : "Historia briefingu nie została odczytana"}</strong><p>Przycisk „Od poprzedniego briefingu” tworzy zapis zmian dla wybranego kraju. Pierwszy obejmuje 24 godziny. Nie zleca pobierania nowego źródła.</p></div>}
    {!query && !loading && <div className="query-guide"><h3>Zakres pierwszej wersji</h3><p>Pytania o czas, kraj, kategorię i promień. Brak danych o bieżących operacjach wojskowych, GNSS ani przyczynach ruchów rynkowych.</p></div>}
  </section>;
}
