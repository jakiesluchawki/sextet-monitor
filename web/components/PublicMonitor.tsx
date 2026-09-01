"use client";
import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { EventQuery, EventsResponse } from "@/lib/contracts";
import { assetPath } from "@/lib/assets";
import { queryLabel } from "@/lib/filters";
import { formatDate } from "@/lib/format";
import { changePublicFilters, filterPublicSnapshot, loadPublicSnapshot, publicSourceCoverage, PUBLIC_DEFAULT_QUERY, selectPublicSource, snapshotAge, type PublicFilterState, type PublicSnapshot } from "@/lib/public-snapshot";
import EventEvidence from "./EventEvidence";
import EventList from "./EventList";
import FilterPanel from "./FilterPanel";
import SourcePanel from "./SourcePanel";
import { Icon } from "./Icon";

const EventMap=dynamic(()=>import("./EventMap"),{ssr:false,loading:()=> <div className="map-loading" role="status">Ładowanie silnika mapy…</div>});
const message=(error:unknown)=>error instanceof Error ? error.message : "Nie można odczytać publicznego zestawu.";

/** Static, same-origin reader. It never imports the private API client. */
export default function PublicMonitor(){
  const [snapshot,setSnapshot]=useState<PublicSnapshot|null>(null);
  const [loading,setLoading]=useState(true);
  const [error,setError]=useState<string|null>(null);
  const [refresh,setRefresh]=useState(0);
  const [now,setNow]=useState<number|null>(null);
  const [filters,setFilters]=useState<PublicFilterState>({query:{...PUBLIC_DEFAULT_QUERY}});
  const {query,sourceId}=filters;
  const [selectedId,setSelectedId]=useState<string|null>(null);
  const [listMode,setListMode]=useState<"list"|"timeline">("list");
  const [mobileView,setMobileView]=useState<"map"|"list">("list");
  const [rightTab,setRightTab]=useState<"evidence"|"sources">("evidence");
  const [revealRequest,setRevealRequest]=useState<{tab:"evidence";id:string}|{tab:"sources"}|null>(null);
  const evidencePanel=useRef<HTMLElement|null>(null);

  useEffect(()=>{
    const controller=new AbortController();
    setLoading(true);setError(null);
    void loadPublicSnapshot(controller.signal).then((data)=>{
      if(!controller.signal.aborted)setSnapshot(data);
    }).catch((reason)=>{if(!controller.signal.aborted)setError(message(reason));}).finally(()=>{if(!controller.signal.aborted)setLoading(false);});
    return()=>controller.abort();
  },[refresh]);
  useEffect(()=>{
    setNow(Date.now());
    // The clock only updates the age label; it never triggers a network request.
    const timer=setInterval(()=>setNow(Date.now()),60000);
    return()=>clearInterval(timer);
  },[]);
  const result=useMemo<{response:EventsResponse|null;error:string|null}>(()=>{
    if(!snapshot)return {response:null,error:null};
    try{return {response:filterPublicSnapshot(snapshot,query,sourceId),error:null};}
    catch(reason){return {response:null,error:message(reason)};}
  },[snapshot,query,sourceId]);
  const response=result.response,events=response?.items || [],sources=snapshot?.sources || [];
  const detail=useMemo(()=>snapshot?.events.find((event)=>event.id===selectedId) || null,[snapshot,selectedId]);
  const countries=useMemo(()=>[...new Set(snapshot?.events.flatMap((event)=>event.countries) || [])].sort(),[snapshot]);
  const coverage=useMemo(()=>snapshot ? publicSourceCoverage(snapshot) : null,[snapshot]);
  const selectedSource=coverage?.entries.find((source)=>source.id===sourceId);
  const visibleSources=sourceId ? sources.filter((source)=>source.id===sourceId) : sources;
  const retry=useCallback(()=>setRefresh((value)=>value+1),[]);
  const select=useCallback((id:string)=>{setSelectedId(id);setRightTab("evidence");setRevealRequest({tab:"evidence",id});},[]);
  const updateFilters=useCallback((patch:Partial<EventQuery>)=>setFilters((current)=>changePublicFilters(current,patch)),[]);
  const resetFilters=useCallback(()=>setFilters({query:{...PUBLIC_DEFAULT_QUERY}}),[]);
  const changeSource=useCallback((id:string)=>setFilters((current)=>selectPublicSource(current,id || undefined)),[]);
  const showSources=useCallback(()=>{setRightTab("sources");setRevealRequest({tab:"sources"});},[]);
  const inspectSource=useCallback((id:string)=>{
    changeSource(id);setMobileView("list");setRevealRequest(null);
    requestAnimationFrame(()=>{if(window.matchMedia("(max-width: 900px)").matches)document.getElementById("events-list")?.focus({preventScroll:false});});
  },[changeSource]);
  const listFallback=useCallback(()=>{setMobileView("list");requestAnimationFrame(()=>document.getElementById("events-list")?.focus({preventScroll:false}));},[]);
  useEffect(()=>{
    if(!revealRequest || revealRequest.tab!==rightTab)return;
    if(revealRequest.tab==="evidence" && (revealRequest.id!==selectedId || detail?.id!==selectedId))return;
    const frame=requestAnimationFrame(()=>{
      if(window.matchMedia("(max-width: 900px)").matches){
        evidencePanel.current?.focus({preventScroll:true});
        evidencePanel.current?.scrollIntoView({behavior:"auto",block:"start"});
      }
      setRevealRequest(null);
    });
    return()=>cancelAnimationFrame(frame);
  },[revealRequest,selectedId,detail?.id,rightTab]);

  return <div className="monitor-app">
    <a className="skip-link" href="#events-list">Przejdź do listy zdarzeń</a>
    <header className="app-header"><div className="brand"><span className="brand-mark" aria-hidden="true">s<span>·</span></span><div><h1>Sextet Monitor</h1><p>Publiczny podgląd · zapisany zestaw</p></div></div><div className="header-status"><span className="connection-state">{snapshot && now!==null ? snapshotAge(snapshot.generated_at,now) : loading ? "Odczyt zestawu…" : "Brak zestawu"}</span></div><div className="header-actions"><button className="quiet-button" onClick={showSources} aria-controls="public-details-panel">Źródła<Icon name="arrow" size={12}/></button><button className="quiet-button refresh-button" onClick={retry} disabled={loading} title="Pobierz ponownie publiczny plik zestawu. Nie odpytuje źródeł ani prywatnego monitora."><Icon name="refresh"/><span>{loading ? "Odczyt…" : "Sprawdź nowy zestaw"}</span></button></div></header>
    <main className="workspace">
      <aside className="control-rail" aria-label="Filtry publicznego zestawu">
        {snapshot && <FilterPanel query={query} onChange={updateFilters} onReset={resetFilters} snapshot={{generatedAt:snapshot.generated_at,countries,sourceFilter:{value:sourceId || "",options:coverage?.entries.map((entry)=>({id:entry.id,name:entry.name,label:entry.label,records:entry.records,available:Boolean(entry.source)})) || [],onChange:changeSource,onShowSources:showSources}}}/>}
        <section className="query-widget"><h2>O tym podglądzie</h2><p>Trzęsienia i katastrofy, pogoda, ostrzeżenia lotnicze, podatności, pogoda kosmiczna oraz komunikaty usług internetowych. Zakres każdego źródła jest ograniczony.</p><p>Wybór źródła dobiera datę: wystąpienie dla katastrof, ważność dla pogody i lotnictwa, publikację dla KEV, pogody kosmicznej i usług. Możesz zmienić ją w filtrach.</p><p>To zapis z określonej chwili. Komunikaty GitHub i Cloudflare opisują usługi dostawców, nie kondycję całego internetu. Nowy plik może zawierać starszy odczyt niedostępnego źródła.</p><p className="field-help">Pytania, briefing, historia zmian i filtry geograficzne wymagające bazy nie są dostępne. Podgląd nie łączy się z prywatnym monitorem.</p></section>
        <footer className="rail-footer"><span>Europe/Warsaw · bez automatycznego odświeżania danych</span><a href={assetPath("/THIRD_PARTY_NOTICES.txt")} target="_blank" rel="noopener noreferrer">Licencje komponentów</a></footer>
      </aside>
      <section className="center-workspace" aria-label="Mapa i wyniki zestawu">
        <div className="view-summary"><div><h2>Opublikowany zestaw</h2><p>{snapshot ? `Koniec zestawu: ${formatDate(snapshot.generated_at)}` : "Czekamy na poprawny publiczny plik danych"}</p><p>{queryLabel(query)}</p>{selectedSource && <><p className="public-source-context">{selectedSource.name} · {selectedSource.label} · {selectedSource.records} rekordów w zestawie</p><p>Udane pobranie: {formatDate(selectedSource.source?.last_success_at)}</p></>}</div><button className={`coverage-button ${coverage && coverage.healthy<coverage.expected ? "has-gaps" : ""}`} onClick={showSources} aria-controls="public-details-panel"><span>Źródła: {coverage ? `${coverage.present} / ${coverage.expected}` : "brak zestawu"}{coverage && <small>Udane, niepuste: {coverage.healthy}</small>}</span><Icon name="arrow" size={12}/></button></div>
        {error && snapshot && <p className="result-warning" role="alert">Nie udało się sprawdzić nowego zestawu. Nadal pokazujemy zapis z {formatDate(snapshot.generated_at)}. {error}</p>}
        <div className="mobile-view-tabs" role="group" aria-label="Widok wyników"><button aria-pressed={mobileView==="list"} onClick={()=>setMobileView("list")}><Icon name="list"/>Lista</button><button aria-pressed={mobileView==="map"} onClick={()=>setMobileView("map")}><Icon name="map"/>Mapa</button></div>
        <section className={`map-section ${mobileView==="map" ? "mobile-visible" : ""}`} aria-label="Mapa tego samego wyniku filtrów"><EventMap events={events} query={query} selectedId={selectedId} onSelect={select} onFallback={listFallback}/></section>
        <section id="events-list" className={`results-section ${mobileView==="list" ? "mobile-visible" : ""}`} aria-label="Lista i oś czasu" tabIndex={-1}>
          <div className="results-toolbar"><div className="view-switch" role="group" aria-label="Sposób prezentacji rekordów"><button aria-pressed={listMode==="list"} onClick={()=>setListMode("list")}><Icon name="list" size={14}/>Lista</button><button aria-pressed={listMode==="timeline"} onClick={()=>setListMode("timeline")}><Icon name="clock" size={14}/>Oś czasu</button></div><div className="result-count" aria-live="polite">{response ? <><strong>{response.shown}</strong> z {response.total} rekordów<span>{response.mapped} na mapie · {response.unlocated} bez pozycji</span></> : loading ? "Odczyt zestawu…" : "Brak wyniku"}</div></div>
          {response?.truncated && <p className="result-warning">Pokazano {response.shown} z {response.total} pasujących rekordów. Mapa i oś czasu pokazują ten sam podzbiór. Zawęź filtry.</p>}
          {coverage && coverage.incomplete.length>0 && <button className="coverage-warning" onClick={showSources}>Brak pełnego odczytu: {coverage.incomplete.map((source)=>source.name).join(", ")}. Sprawdź źródła i daty.<Icon name="arrow" size={12}/></button>}
          {coverage && coverage.empty>0 && <p className="public-coverage-note">Odczyty bez rekordów: {coverage.entries.filter((source)=>source.empty).map((source)=>source.name).join(", ")}. Nie oznacza to braku zdarzeń.</p>}
          {response && <details className="result-limitations"><summary>Zakres i ograniczenia zestawu</summary><p>{formatDate(response.query.since)} → {formatDate(response.query.until)}</p><ul>{response.limitations?.map((limitation,index)=><li key={index}>{limitation}</li>)}</ul></details>}
          <div className="results-scroll"><EventList publicMode events={events} selectedId={selectedId} basis={query.time_basis} mode={listMode} loading={loading && !snapshot} error={result.error || (!snapshot ? error : null)} sources={visibleSources} onSelect={select} onRetry={result.error ? resetFilters : retry}/></div>
          <footer className="results-footer"><span>{snapshot ? `Wersja zestawu: ${snapshot.version}` : "Nie używamy danych zastępczych"}</span><span>Lista zawiera też rekordy bez geometrii</span><a href={assetPath("/THIRD_PARTY_NOTICES.txt")} target="_blank" rel="noopener noreferrer">Licencje komponentów</a></footer>
        </section>
      </section>
      <aside id="public-details-panel" ref={evidencePanel} className="evidence-workspace" aria-label="Dowody i źródła zestawu" tabIndex={-1}><nav className="evidence-tabs" aria-label="Panel szczegółów">{([["evidence","Dowody"],["sources","Źródła"]] as const).map(([key,label])=><button key={key} aria-pressed={rightTab===key} onClick={()=>{setRightTab(key);setRevealRequest(null);}}>{label}</button>)}</nav><div className="evidence-scroll">
        {rightTab==="evidence" && <EventEvidence publicMode detail={detail} readAt={snapshot?.generated_at} selected={Boolean(selectedId)} loading={false} error={selectedId && !detail ? "Tego rekordu nie ma w odczytanej wersji publicznego zestawu. Wybierz pozycję z listy." : null} outsideFilter={Boolean(selectedId && response && !events.some((event)=>event.id===selectedId))} onSelect={select} onRetry={()=>setSelectedId(null)}/>}
        {rightTab==="sources" && (snapshot ? <SourcePanel snapshotAt={snapshot.generated_at} publicCoverage={coverage || undefined} selectedSourceId={sourceId} onSelectSource={inspectSource} sources={sources} loading={false} error={null} onRetry={retry}/> : <div className="empty-state" role={error ? "alert" : "status"}><strong>{loading ? "Odczyt źródeł zapisanego zestawu…" : "Brak publicznego zestawu"}</strong><p>{error || "Lista źródeł pojawi się po odczycie poprawnego pliku danych."}</p>{!loading && <button onClick={retry}>Spróbuj ponownie</button>}</div>)}
      </div></aside>
    </main>
  </div>;
}
