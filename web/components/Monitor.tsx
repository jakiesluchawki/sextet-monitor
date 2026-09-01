"use client";
import dynamic from "next/dynamic";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { BriefingResponse, EventDetail, EventQuery, EventsResponse, HealthResponse, QueryResponse, SourcesResponse } from "@/lib/contracts";
import { apiFetch } from "@/lib/api";
import { changeQuery, DEFAULT_QUERY, interpretationToQuery, queryLabel, serializeQuery } from "@/lib/filters";
import { countryName, coverageWarnings, formatDate, sourceOverview } from "@/lib/format";
import AnalysisPanel from "./AnalysisPanel";
import EventEvidence, { shouldRevealEvidence } from "./EventEvidence";
import EventList from "./EventList";
import FilterPanel from "./FilterPanel";
import SourcePanel from "./SourcePanel";
import { Icon } from "./Icon";

const EventMap=dynamic(()=>import("./EventMap"),{ssr:false,loading:()=> <div className="map-loading" role="status">Ładowanie silnika mapy…</div>});
const message=(error:unknown)=>error instanceof Error ? error.message : "Nie udało się odczytać danych.";

export default function Monitor(){
  const [query,setQuery]=useState<EventQuery>({...DEFAULT_QUERY});
  const [refresh,setRefresh]=useState(0);
  const [eventState,setEventState]=useState<{key:string;data:EventsResponse}|null>(null);
  const [eventsLoading,setEventsLoading]=useState(true);
  const [eventsError,setEventsError]=useState<string|null>(null);
  const [sourceResponse,setSourceResponse]=useState<SourcesResponse|null>(null);
  const [sourcesLoading,setSourcesLoading]=useState(true);
  const [sourcesError,setSourcesError]=useState<string|null>(null);
  const [health,setHealth]=useState<HealthResponse|null>(null);
  const [healthFailed,setHealthFailed]=useState(false);
  const [selectedId,setSelectedId]=useState<string|null>(null);
  const [detailState,setDetailState]=useState<{id:string;data:EventDetail;readAt:string}|null>(null);
  const [detailLoading,setDetailLoading]=useState(false);
  const [detailError,setDetailError]=useState<string|null>(null);
  const [detailRetry,setDetailRetry]=useState(0);
  const [revealRequest,setRevealRequest]=useState<{id:string}|null>(null);
  const evidencePanel=useRef<HTMLElement|null>(null);
  const evidenceScroll=useRef<HTMLDivElement|null>(null);
  const preservedDetailScroll=useRef<{id:string;top:number;windowX:number;windowY:number}|null>(null);
  const [listMode,setListMode]=useState<"list"|"timeline">("list");
  const [mobileView,setMobileView]=useState<"map"|"list">("list");
  const [rightTab,setRightTab]=useState<"evidence"|"analysis"|"sources">("evidence");
  const [question,setQuestion]=useState("");
  const [queryResult,setQueryResult]=useState<QueryResponse|null>(null);
  const [queryLoading,setQueryLoading]=useState(false);
  const [briefing,setBriefing]=useState<BriefingResponse|null>(null);
  const [latestKnown,setLatestKnown]=useState(false);
  const [briefLoading,setBriefLoading]=useState(false);
  const [analysisError,setAnalysisError]=useState<string|null>(null);
  const [queryNotice,setQueryNotice]=useState<string|null>(null);
  const queryController=useRef<AbortController|null>(null);
  const briefController=useRef<AbortController|null>(null);
  const briefingGeneration=useRef(0);
  const filterGeneration=useRef(0);
  const params=useMemo(()=>serializeQuery(query),[query]);
  const response=eventState?.key===params ? eventState.data : null;
  const events=response?.items || [];
  const sources=response?.source_health || sourceResponse?.items || [];
  const detail=detailState?.id===selectedId ? detailState.data : null;
  const selectedRevision=eventState?.data.items.find((event)=>event.id===selectedId)?.last_changed_at;
  const detailReadAt=detailState?.id===selectedId ? detailState.readAt : null;
  const preserveEvidencePosition=useCallback((id:string)=>{
    if(evidencePanel.current?.querySelector("[data-event-id]")?.getAttribute("data-event-id")!==id || !evidenceScroll.current)return;
    preservedDetailScroll.current={id,top:evidenceScroll.current.scrollTop,windowX:window.scrollX,windowY:window.scrollY};
  },[]);
  const overview=sourceOverview(sources);
  const warnings=coverageWarnings(sources);

  useEffect(()=>{
    const controller=new AbortController();
    setEventsLoading(true);setEventsError(null);
    void apiFetch<EventsResponse>("/api/events?"+params,{signal:controller.signal}).then((data)=>{
      if(!Array.isArray(data.items) || !Array.isArray(data.source_health) || typeof data.shown!=="number")throw new Error("Odpowiedź API nie zgadza się z kontraktem zdarzeń.");
      if(!controller.signal.aborted)setEventState({key:params,data});
    }).catch((error)=>{if(!controller.signal.aborted){setEventState(null);setEventsError(message(error));}}).finally(()=>{if(!controller.signal.aborted)setEventsLoading(false);});
    return()=>controller.abort();
  },[params,refresh]);

  useEffect(()=>{
    const controller=new AbortController();
    setSourcesLoading(true);setSourcesError(null);
    void apiFetch<SourcesResponse>("/api/sources",{signal:controller.signal}).then((data)=>{
      if(!Array.isArray(data.items))throw new Error("Odpowiedź API nie zawiera stanu źródeł.");
      if(!controller.signal.aborted)setSourceResponse(data);
    }).catch((error)=>{if(!controller.signal.aborted)setSourcesError(message(error));}).finally(()=>{if(!controller.signal.aborted)setSourcesLoading(false);});
    void apiFetch<HealthResponse>("/api/health",{signal:controller.signal}).then((data)=>{if(!controller.signal.aborted){setHealth(data);setHealthFailed(false);}}).catch(()=>{if(!controller.signal.aborted){setHealth(null);setHealthFailed(true);}});
    return()=>controller.abort();
  },[refresh]);

  useEffect(()=>{
    const timer=setInterval(()=>{if(document.visibilityState==="visible")setRefresh((value)=>value+1);},60000);
    return()=>clearInterval(timer);
  },[]);
  useEffect(()=>{
    const controller=new AbortController();
    const generation=briefingGeneration.current;
    void apiFetch<BriefingResponse|null>("/api/briefings/latest",{signal:controller.signal}).then((data)=>{
      if(!controller.signal.aborted && generation===briefingGeneration.current){setBriefing(data);setLatestKnown(true);}
    }).catch((error)=>{if(!controller.signal.aborted && generation===briefingGeneration.current)setAnalysisError("Historia briefingu: "+message(error));});
    return()=>controller.abort();
  },[]);
  useEffect(()=>()=>{queryController.current?.abort();briefController.current?.abort();},[]);

  useEffect(()=>{
    if(!selectedId){setDetailState(null);setDetailError(null);setDetailLoading(false);return;}
    const controller=new AbortController();
    preserveEvidencePosition(selectedId);
    setDetailLoading(true);setDetailError(null);
    void apiFetch<EventDetail>("/api/events/"+encodeURIComponent(selectedId),{signal:controller.signal}).then((data)=>{
      if(data.id!==selectedId || !Array.isArray(data.evidence) || !Array.isArray(data.revisions) || !Array.isArray(data.relations))throw new Error("API nie zwróciło pełnego kontraktu wybranego rekordu.");
      if(!controller.signal.aborted){
        preserveEvidencePosition(selectedId);
        setDetailState({id:selectedId,data,readAt:new Date().toISOString()});
      }
    }).catch((error)=>{if(!controller.signal.aborted){preserveEvidencePosition(selectedId);setDetailError(message(error));}}).finally(()=>{if(!controller.signal.aborted)setDetailLoading(false);});
    return()=>controller.abort();
  },[selectedId,detailRetry,refresh,selectedRevision,preserveEvidencePosition]);

  useLayoutEffect(()=>{
    const previous=preservedDetailScroll.current;
    preservedDetailScroll.current=null;
    if(!previous || previous.id!==selectedId || !evidenceScroll.current)return;
    evidenceScroll.current.scrollTop=previous.top;
    if(window.scrollX!==previous.windowX || window.scrollY!==previous.windowY)window.scrollTo(previous.windowX,previous.windowY);
    // Existing detail nodes stay mounted; background refresh never moves focus.
  },[detailState,detailError,detailLoading,selectedId]);

  useEffect(()=>{
    if(!revealRequest)return;
    if(rightTab!=="evidence"){setRevealRequest(null);return;}
    if(!shouldRevealEvidence(revealRequest.id,selectedId,detail?.id || null,detailLoading))return;
    // Wait for the full detail DOM, not the shorter loading placeholder. Consume
    // this user request once so later data refreshes never move their viewport.
    const frame=requestAnimationFrame(()=>{
      const panel=evidencePanel.current;
      if(panel && window.matchMedia("(max-width: 900px)").matches){
        panel.focus({preventScroll:true});
        panel.scrollIntoView({behavior:"auto",block:"start"});
      }
      setRevealRequest((current)=>current===revealRequest ? null : current);
    });
    return()=>cancelAnimationFrame(frame);
  },[revealRequest,selectedId,detail?.id,detailLoading,rightTab]);

  const select=useCallback((id:string)=>{
    setRevealRequest(window.matchMedia("(max-width: 900px)").matches ? {id} : null);
    setDetailError(null);setDetailRetry((value)=>value+1);setSelectedId(id);setRightTab("evidence");
  },[]);
  const updateFilters=useCallback((patch:Partial<EventQuery>)=>{filterGeneration.current+=1;setQuery((current)=>changeQuery(current,patch));setQueryNotice(null);},[]);
  const resetFilters=useCallback(()=>{filterGeneration.current+=1;setQuery({...DEFAULT_QUERY});setQueryNotice(null);},[]);
  const retry=useCallback(()=>setRefresh((value)=>value+1),[]);
  const listFallback=useCallback(()=>{setMobileView("list");requestAnimationFrame(()=>document.getElementById("events-list")?.focus({preventScroll:false}));},[]);

  const submitQuestion=async(event:React.FormEvent)=>{
    event.preventDefault();
    if(!question.trim() || queryLoading)return;
    queryController.current?.abort();
    const controller=new AbortController();queryController.current=controller;
    const generation=filterGeneration.current;
    setQueryLoading(true);setAnalysisError(null);setRightTab("analysis");setQueryNotice(null);
    try{
      const result=await apiFetch<QueryResponse>("/api/query",{body:{question:question.trim()},signal:controller.signal});
      if(controller.signal.aborted)return;
      setQueryResult(result);
      if(result.supported){
        const next=interpretationToQuery(result.interpretation);
        if(next && generation===filterGeneration.current){setQuery(next);setQueryNotice("Filtry z zapytania zastosowano do mapy, listy i osi czasu.");}
        else if(next)setQueryNotice("Odpowiedź jest gotowa. Zachowano filtry zmienione ręcznie po wysłaniu pytania.");
        else setAnalysisError("Odpowiedź nie zawiera poprawnego zestawu filtrów. Bieżący widok pozostał bez zmian.");
      }else setQueryNotice("Zapytanie poza zakresem. Filtry pozostały bez zmian.");
    }catch(error){if(!controller.signal.aborted)setAnalysisError(message(error));}
    finally{if(!controller.signal.aborted)setQueryLoading(false);}
  };
  const createBriefing=async()=>{
    if(briefLoading)return;
    briefController.current?.abort();const controller=new AbortController();briefController.current=controller;
    briefingGeneration.current+=1;
    setBriefLoading(true);setAnalysisError(null);setRightTab("analysis");
    try{
      const data=await apiFetch<BriefingResponse>("/api/briefings",{body:{window_hours:24,...(query.country ? {country:query.country} : {})},signal:controller.signal});
      if(!controller.signal.aborted){setBriefing(data);setLatestKnown(true);}
    }catch(error){if(!controller.signal.aborted)setAnalysisError(message(error));}
    finally{if(!controller.signal.aborted)setBriefLoading(false);}
  };
  return <div className="monitor-app">
    <a className="skip-link" href="#events-list">Przejdź do listy zdarzeń</a>
    <header className="app-header"><div className="brand"><span className="brand-mark" aria-hidden="true">m<span>·</span></span><div><h1>Mieszko Monitor</h1><p>Prywatny odczyt sytuacji</p></div></div><div className="header-status"><span className={`connection-state ${health ? "connected" : healthFailed ? "unavailable" : ""}`}><i/>{health ? "Lokalne API połączone" : healthFailed ? "Brak połączenia z API" : "Łączenie z API…"}</span><span className="ai-state">AI wyłączone</span></div><div className="header-actions"><button className="quiet-button refresh-button" onClick={retry} disabled={eventsLoading} title="Odśwież widok z lokalnej bazy; pobieranie źródeł wykonuje worker"><Icon name="refresh"/><span>Odśwież</span></button><div className="briefing-actions"><button className="primary-button" aria-describedby="briefing-action-scope" onClick={()=>void createBriefing()} disabled={briefLoading} title={`Zmiany od poprzedniego briefingu dla obszaru: ${query.country ? countryName(query.country) : "cały świat"}. Pierwszy briefing obejmuje 24 godziny. Pozostałe filtry widoku nie ograniczają briefingu.`}>{briefLoading ? "Tworzenie briefingu…" : "Od poprzedniego briefingu"}<Icon name="arrow" size={14}/></button><p id="briefing-action-scope">{query.country ? countryName(query.country) : "Cały świat"} · pierwszy: 24 h</p></div></div></header>
    <main className="workspace">
      <aside className="control-rail" aria-label="Filtry i pytania"><FilterPanel query={query} onChange={updateFilters} onReset={resetFilters}/><section className="query-widget"><h2>Zapytaj o dane</h2><p>Prosty parser po polsku. Bez modelu AI.</p><form onSubmit={(event)=>void submitQuestion(event)}><label htmlFor="question">Pytanie</label><textarea id="question" name="question" rows={3} maxLength={500} value={question} onChange={(event)=>setQuestion(event.target.value)} placeholder="Pokaż pogodę w Polsce z ostatnich 12 godzin" aria-describedby="query-help"/><button className="query-submit" type="submit" disabled={!question.trim() || queryLoading}>{queryLoading ? "Sprawdzanie…" : "Sprawdź w danych"}<Icon name="arrow" size={14}/></button></form><p id="query-help" className="field-help">Czas, kraj, kategoria lub promień. Przyczyny zdarzeń wymagają dowodów.</p>{queryNotice && <p className="query-notice" role="status">{queryNotice}</p>}</section><footer className="rail-footer"><span>Europe/Warsaw</span><span>Widok odświeżany co 60 s</span></footer></aside>
      <section className="center-workspace" aria-label="Mapa i wyniki">
        <div className="view-summary"><div><h2>Obserwowane zdarzenia</h2><p>{queryLabel(query)}</p></div><button className={`coverage-button ${overview.state==="partial" ? "has-gaps" : overview.state==="ok" ? "all-fresh" : ""}`} onClick={()=>setRightTab("sources")}><span className="state-dot"/>{overview.active ? `Źródła: ${overview.responding}/${overview.active}` : overview.state==="disabled" ? "Źródła nieaktywne" : "Źródła"}<Icon name="arrow" size={12}/></button></div>
        <div className="mobile-view-tabs" role="group" aria-label="Widok wyników"><button aria-pressed={mobileView==="list"} onClick={()=>setMobileView("list")}><Icon name="list"/>Lista</button><button aria-pressed={mobileView==="map"} onClick={()=>setMobileView("map")}><Icon name="map"/>Mapa</button></div>
        <section className={`map-section ${mobileView==="map" ? "mobile-visible" : ""}`} aria-label="Mapa tego samego wyniku filtrów"><EventMap events={events} query={query} selectedId={selectedId} onSelect={select} onFallback={listFallback}/></section>
        <section id="events-list" className={`results-section ${mobileView==="list" ? "mobile-visible" : ""}`} aria-label="Lista i oś czasu" tabIndex={-1}>
          <div className="results-toolbar"><div className="view-switch" role="group" aria-label="Sposób prezentacji rekordów"><button aria-pressed={listMode==="list"} onClick={()=>setListMode("list")}><Icon name="list" size={14}/>Lista</button><button aria-pressed={listMode==="timeline"} onClick={()=>setListMode("timeline")}><Icon name="clock" size={14}/>Oś czasu</button></div><div className="result-count" aria-live="polite">{response && !eventsLoading ? <><strong>{response.shown}</strong> z {response.total} rekordów<span>{response.mapped} na mapie · {response.unlocated} bez pozycji</span></> : eventsLoading ? "Odczyt wyniku…" : "Brak odczytu"}</div></div>
          {response?.truncated && <p className="result-warning">Wynik ograniczony do {response.shown} rekordów. Mapa i oś czasu pokazują ten sam podzbiór. Zawęź filtry.</p>}
          {!eventsLoading && response && warnings.length>0 && <button className="coverage-warning" onClick={()=>setRightTab("sources")}>Niepełne pokrycie: {warnings.map((source)=>source.name).join(", ")}. Sprawdź źródła.<Icon name="arrow" size={12}/></button>}
          {Boolean(response?.limitations?.length) && <details className="result-limitations" open={Boolean(query.radius_km)}><summary>Ograniczenia tego wyniku</summary><ul>{response?.limitations?.map((limitation,index)=><li key={index}>{limitation}</li>)}</ul></details>}
          <div className="results-scroll"><EventList events={events} selectedId={selectedId} basis={query.time_basis} mode={listMode} loading={eventsLoading || (!response && !eventsError)} error={eventsError} sources={sources} onSelect={select} onRetry={retry}/></div>
          <footer className="results-footer"><span>{response ? `Wynik z bazy: ${formatDate(response.generated_at,true)}` : "Brak aktualnego wyniku z bazy"}</span><span>Lista obejmuje także rekordy bez geometrii</span></footer>
        </section>
      </section>
      <aside ref={evidencePanel} className="evidence-workspace" aria-label="Dowody, briefing i źródła" tabIndex={-1}><nav className="evidence-tabs" aria-label="Panel szczegółów">{([["evidence","Dowody"],["analysis","Briefing"],["sources","Źródła"]] as const).map(([key,label])=><button key={key} aria-pressed={rightTab===key} onClick={()=>setRightTab(key)}>{label}</button>)}</nav><div ref={evidenceScroll} className="evidence-scroll">
        {rightTab==="evidence" && <EventEvidence detail={detail} readAt={detailReadAt} selected={Boolean(selectedId)} loading={detailLoading || Boolean(selectedId && !detail && !detailError)} error={detailError} outsideFilter={Boolean(selectedId && response && !events.some((event)=>event.id===selectedId))} onSelect={select} onRetry={()=>setDetailRetry((value)=>value+1)}/>}
        {rightTab==="sources" && <SourcePanel sources={sources} loading={sourcesLoading && !sources.length} error={sourcesError} onRetry={retry}/>}
        {rightTab==="analysis" && <AnalysisPanel query={queryResult} briefing={briefing} latestKnown={latestKnown} loading={queryLoading} briefLoading={briefLoading} error={analysisError} onSelect={select} briefingCountry={query.country}/>}
      </div></aside>
    </main>
  </div>;
}
