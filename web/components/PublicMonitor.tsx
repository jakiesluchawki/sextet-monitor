"use client";
import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Category } from "@/lib/contracts";
import { assetPath } from "@/lib/assets";
import { CATEGORY_LABELS, DEFAULT_QUERY } from "@/lib/filters";
import { formatDate } from "@/lib/format";
import { loadPublicSnapshot, publicSourceCoverage, PUBLIC_SOURCE_INFO, snapshotAge, type PublicSnapshot, type PublicSourceId } from "@/lib/public-snapshot";
import { buildSituation, createBriefingText, scopeLabel, type ScopeId } from "@/lib/situation";
import { BASELINE_STORAGE_KEY, WATCH_STORAGE_KEY, buildShareUrl, compareSnapshots, makeBaseline, parseSharedView, readBaseline, readWatchState, serializeSharedView, writeBaseline, writeWatchState, type SnapshotBaseline, type SharedView, type PublicStorage } from "@/lib/public-session";
import { filterViewEvents, snapshotCalendarDay } from "@/lib/public-view";
import EventEvidence from "./EventEvidence";
import SourcePanel from "./SourcePanel";
import SignalRows, { PinButton } from "./SignalRows";
import SituationOverview from "./SituationOverview";
import { Icon } from "./Icon";

const EventMap=dynamic(()=>import("./EventMap"),{ssr:false,loading:()=> <div className="map-loading" role="status">Odczyt mapy…</div>});
const DetailedExplorer=dynamic(()=>import("./DetailedExplorer"),{ssr:false});
const PAGE_SIZE=60,MAP_LIMIT=500;
const tabs=[{id:"overview",label:"Przegląd",icon:"layers"},{id:"explore",label:"Mapa i dane",icon:"map"},{id:"briefing",label:"Briefing",icon:"list"}] as const;
const scopes:ScopeId[]=["world","europe","poland","turkey"];
const number=new Intl.NumberFormat("pl-PL");
function localStore():PublicStorage|null {try{return window.localStorage;}catch{return null;}}

/** Public-only cockpit: no private API or cloud inference. */
export default function PublicMonitor(){
  const [snapshot,setSnapshot]=useState<PublicSnapshot|null>(null);
  const [loading,setLoading]=useState(true),[error,setError]=useState<string|null>(null);
  const [refresh,setRefresh]=useState(0),[now,setNow]=useState<number|null>(null);
  const [view,setView]=useState<SharedView["view"]>("overview"),[scope,setScope]=useState<ScopeId>("world");
  const [hours,setHours]=useState<24|72|168>(24),[category,setCategory]=useState<Category|undefined>();
  const [sourceId,setSourceId]=useState<PublicSourceId|undefined>(),[search,setSearch]=useState("");
  const [selectedId,setSelectedId]=useState<string|null>(null),[panel,setPanel]=useState<"evidence"|"sources"|null>(null);
  const [pinnedIds,setPinnedIds]=useState<string[]>([]),[baseline,setBaseline]=useState<SnapshotBaseline|null>(null);
  const [sessionReady,setSessionReady]=useState(false),[storageAvailable,setStorageAvailable]=useState(true);
  const [toast,setToast]=useState(""),[copyFallback,setCopyFallback]=useState("");
  const [briefingMode,setBriefingMode]=useState<"suggested"|"pinned">("suggested");
  const [pageSize,setPageSize]=useState(PAGE_SIZE),[onlyPinned,setOnlyPinned]=useState(false),[onlyChanges,setOnlyChanges]=useState(false);
  const [legacy,setLegacy]=useState(false),[mobileMap,setMobileMap]=useState(false);
  const detailPanel=useRef<HTMLElement|null>(null),searchInput=useRef<HTMLInputElement|null>(null),returnFocus=useRef<HTMLElement|null>(null);
  const closePanel=useCallback(()=>{setPanel(null);setSelectedId(null);requestAnimationFrame(()=>{const target=returnFocus.current; if(target?.isConnected && target.offsetParent!==null)target.focus({preventScroll:true});else document.getElementById("sextet-content")?.focus({preventScroll:true});});},[]);

  useEffect(()=>{
    const restore=()=>{
      const state=parseSharedView(window.location.hash);if(!Object.keys(state).length)return;
      setView(state.view || "overview");setScope(state.scope || "world");setHours(state.hours || 24);
      setCategory(state.category);setSourceId(state.sourceId);setSearch(state.search || "");
      setSelectedId(state.eventId || null);setPanel(state.eventId ? "evidence" : null);
      setOnlyPinned(false);setOnlyChanges(false);setLegacy(false);
    };
    restore();const storage=localStore();setStorageAvailable(Boolean(storage));setPinnedIds(readWatchState(storage).ids);setSessionReady(true);
    const sync=(event:StorageEvent)=>{
      if(event.key===WATCH_STORAGE_KEY || event.key===null)setPinnedIds(readWatchState(localStore()).ids);
      if(event.key===BASELINE_STORAGE_KEY || event.key===null)setBaseline(readBaseline(localStore()));
    };
    window.addEventListener("hashchange",restore);window.addEventListener("storage",sync);
    return()=>{window.removeEventListener("hashchange",restore);window.removeEventListener("storage",sync);};
  },[]);
  useEffect(()=>{
    const controller=new AbortController();setLoading(true);setError(null);
    void loadPublicSnapshot(controller.signal).then(data=>{
      if(controller.signal.aborted)return;setSnapshot(data);
      const storage=localStore(),previous=readBaseline(storage);setBaseline(previous);
      if(!previous && !writeBaseline(storage,makeBaseline(data)))setStorageAvailable(false);
    }).catch(reason=>{if(!controller.signal.aborted)setError(reason instanceof Error ? reason.message : "Nie udało się odczytać danych.");}).finally(()=>{if(!controller.signal.aborted)setLoading(false);});
    return()=>controller.abort();
  },[refresh]);
  useEffect(()=>{setNow(Date.now());const timer=setInterval(()=>setNow(Date.now()),60000);return()=>clearInterval(timer);},[]);
  useEffect(()=>{if(!toast)return;const timer=setTimeout(()=>setToast(""),6000);return()=>clearTimeout(timer);},[toast]);
  useEffect(()=>{
    if(!sessionReady)return;
    const hash=serializeSharedView({view,scope,hours,category,sourceId,search:search || undefined,eventId:selectedId || undefined});
    if(window.location.hash!==hash)window.history.replaceState(null,"",`${window.location.pathname}${window.location.search}${hash}`);
  },[view,scope,hours,category,sourceId,search,selectedId,sessionReady]);
  useEffect(()=>{setPageSize(PAGE_SIZE);},[scope,hours,category,sourceId,search,onlyPinned,onlyChanges,snapshot]);
  useEffect(()=>{
    const keyboard=(event:KeyboardEvent)=>{
      if(event.key==="Escape"){closePanel();setCopyFallback("");return;}
      const target=event.target as HTMLElement;
      if(event.key==="/" && !event.ctrlKey && !event.metaKey && !event.altKey && !["INPUT","TEXTAREA","SELECT"].includes(target.tagName) && !target.isContentEditable){event.preventDefault();setView("explore");setLegacy(false);requestAnimationFrame(()=>searchInput.current?.focus());}
    };
    window.addEventListener("keydown",keyboard);return()=>window.removeEventListener("keydown",keyboard);
  },[]);

  const situation=useMemo(()=>snapshot ? buildSituation(snapshot,{scope,hours,...(now!==null ? {now} : {})}) : null,[snapshot,scope,hours,now]);
  const coverage=useMemo(()=>snapshot ? publicSourceCoverage(snapshot) : null,[snapshot]);
  const delta=useMemo(()=>snapshot ? compareSnapshots(snapshot,baseline) : null,[snapshot,baseline]);
  const changedIds=useMemo(()=>[...(delta?.addedIds || []),...(delta?.changedIds || [])],[delta]);
  const records=useMemo(()=>filterViewEvents(situation?.events || [],{category,sourceId,search,onlyIds:onlyPinned ? pinnedIds : onlyChanges ? changedIds : undefined}),[situation,category,sourceId,search,onlyPinned,onlyChanges,pinnedIds,changedIds]);
  const mapRecords=useMemo(()=>records.slice(0,MAP_LIMIT),[records]);
  const detail=useMemo(()=>snapshot?.events.find(event=>event.id===selectedId) || null,[snapshot,selectedId]);
  const pinnedHere=useMemo(()=>situation?.events.filter(event=>pinnedIds.includes(event.id)) || [],[situation,pinnedIds]);
  const briefIds=useMemo(()=>briefingMode==="pinned" ? pinnedHere.map(event=>event.id) : undefined,[briefingMode,pinnedHere]);
  const briefText=useMemo(()=>snapshot && situation ? createBriefingText(situation,snapshot,briefIds,sessionReady ? buildShareUrl(window.location.href,{view:"overview",scope,hours}) : undefined) : "",[snapshot,situation,briefIds,sessionReady,scope,hours]);
  const mapQuery=useMemo(()=>({...DEFAULT_QUERY,window_hours:hours,include_inactive:true,country:scope==="poland" ? "PL" : scope==="turkey" ? "TR" : undefined,region:scope==="europe" ? "europe" as const : undefined}),[scope,hours]);
  const scopedChanges=situation?.events.filter(event=>changedIds.includes(event.id)) || [];
  const stale=Boolean(snapshot && now!==null && now-Date.parse(snapshot.generated_at)>3*3600000);

  const navigate=useCallback((next:SharedView["view"])=>{setView(next);setLegacy(false);setOnlyPinned(false);setOnlyChanges(false);},[]);
  const reveal=useCallback(()=>{const active=document.activeElement;if(active instanceof HTMLElement && !detailPanel.current?.contains(active))returnFocus.current=active;requestAnimationFrame(()=>{detailPanel.current?.focus({preventScroll:true});if(window.matchMedia("(max-width: 1050px)").matches)detailPanel.current?.scrollIntoView({behavior:"auto",block:"start"});});},[]);
  const select=useCallback((id:string)=>{setSelectedId(id);setPanel("evidence");reveal();},[reveal]);
  const showSources=useCallback(()=>{setPanel("sources");reveal();},[reveal]);
  const togglePin=useCallback((id:string)=>{
    const stored=readWatchState(localStore()),current=stored.updatedAt ? stored.ids : pinnedIds;
    const next=current.includes(id) ? current.filter(value=>value!==id) : [...current,id];
    if(next.length>30){setToast("Możesz przypiąć do 30 zapisów. Odepnij któryś, aby dodać następny.");return;}
    const saved=writeWatchState(localStore(),next);setPinnedIds(next);
    if(!saved){setStorageAvailable(false);setToast("Wybór działa w tej karcie. Przeglądarka nie pozwala go zapisać.");}
    else setToast(next.includes(id) ? "Przypięto. Zapis znajdziesz w swoim briefingu." : "Usunięto z przypiętych.");
  },[pinnedIds]);
  const copy=useCallback(async(text:string,success:string)=>{
    try{await navigator.clipboard.writeText(text);setToast(success);setCopyFallback("");}
    catch{setCopyFallback(text);setToast("Automatyczne kopiowanie jest niedostępne. Zaznacz tekst w polu poniżej.");}
  },[]);
  const copyLink=useCallback(()=>{
    try{void copy(buildShareUrl(window.location.href,{view,scope,hours,category,sourceId,search:search || undefined,eventId:selectedId || undefined}),"Skopiowano link do tego widoku w najnowszym dostępnym zestawie.");}
    catch{setToast("Nie udało się przygotować poprawnego linku.");}
  },[copy,view,scope,hours,category,sourceId,search,selectedId]);
  const markBaseline=()=>{if(!snapshot)return;if(delta?.status==="out_of_order"){setToast("Punkt porównania jest nowszy od tego zestawu. Nie cofnięto go.");return;}const next=makeBaseline(snapshot);if(writeBaseline(localStore(),next)){setBaseline(next);setToast("Zapamiętano zestaw. Kolejne odczyty porównamy z nim na tym urządzeniu.");}else{setToast("Nie zapisano punktu: pamięć jest niedostępna albo zawiera już nowszy lub sprzeczny zapis. Nie nadpisano go.");}};
  const inspectCategory=(value:Category)=>{setCategory(value);setSourceId(undefined);setSearch("");navigate("explore");};
  const inspectSource=(id:string)=>{setSourceId(id as PublicSourceId);setCategory(undefined);setSearch("");navigate("explore");setPanel(null);};
  const downloadBriefing=()=>{const blob=new Blob([briefText],{type:"text/markdown;charset=utf-8"}),url=URL.createObjectURL(blob),a=document.createElement("a");a.href=url;a.download=`sextet-briefing-${snapshotCalendarDay(snapshot?.generated_at) || "zestaw"}.md`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};

  return <div className={`sextet-app ${panel ? "panel-open" : ""}`}>
    <a className="skip-link" href="#sextet-content">Przejdź do treści</a>
    <header className="sextet-header">
      <button className="sextet-brand" onClick={()=>navigate("overview")} aria-label="Sextet Monitor, przegląd"><svg viewBox="0 0 32 32" width="32" height="32" fill="none" aria-hidden="true"><path d="M16 2 28.1 9v14L16 30 3.9 23V9Z" stroke="currentColor"/><path d="m16 8 6.9 4v8L16 24l-6.9-4v-8Z" stroke="currentColor"/><path d="M16 2v6m12.1 1-5.2 3m5.2 11-5.2-3M16 30v-6M3.9 23l5.2-3M3.9 9l5.2 3" stroke="currentColor"/></svg><span>Sextet<span className="brand-secondary">Monitor</span></span><sup>02</sup></button>
      <nav className="sextet-tabs" aria-label="Główne widoki">{tabs.map(tab=><button key={tab.id} aria-pressed={view===tab.id && !legacy} onClick={()=>navigate(tab.id)}><Icon name={tab.icon}/>{tab.label}{tab.id==="briefing" && pinnedIds.length>0 && <span className="tab-count">{pinnedIds.length}</span>}</button>)}</nav>
      <div className="sextet-header-actions"><button className="quiet-button source-header" onClick={showSources} aria-controls="public-details-panel">Źródła<span>{coverage ? `${coverage.present}/${coverage.expected}` : "…"}</span></button><button className="quiet-button" onClick={copyLink} title="Kopiuj link do widoku" aria-label="Kopiuj link do widoku"><Icon name="link"/></button><button className="quiet-button" onClick={()=>setRefresh(value=>value+1)} disabled={loading} title="Sprawdź opublikowany plik. Nie uruchamia pobierania u dostawców." aria-label="Sprawdź nowy zestaw"><Icon name="refresh"/><span className="refresh-label">{loading ? "Odczyt…" : "Odśwież"}</span></button></div>
    </header>
    <div className="sextet-statusbar"><span className={stale ? "stale-label" : ""}><i className="status-square"/>{snapshot ? snapshotAge(snapshot.generated_at,now ?? Date.parse(snapshot.generated_at)) : loading ? "Odczyt publicznych danych…" : "Brak zestawu"}</span><span>{snapshot ? `${formatDate(snapshot.generated_at)} · Europe/Warsaw` : "Bez danych zastępczych"}</span><button onClick={showSources}>{coverage ? `${coverage.healthy} odczytów bez ostrzeżeń · ${coverage.incomplete.length} z ograniczeniami` : "Stan źródeł"}<Icon name="arrow" size={11}/></button></div>
    {error && <div className="sextet-error" role="alert"><strong>{snapshot ? "Nowego zestawu nie odczytano. Zachowano poprzedni." : "Nie można odczytać zestawu."}</strong><p>{error}</p><button onClick={()=>setRefresh(value=>value+1)}>Spróbuj ponownie</button></div>}
    <div className="sextet-body">
      <aside className="sextet-rail" aria-label="Obszar i czas">
        <div className="scope-navigation"><p className="rail-label">Obserwowany obszar</p><div role="group" aria-label="Wybierz obszar">{scopes.map((id,index)=><button key={id} aria-pressed={scope===id} onClick={()=>{setScope(id);setOnlyChanges(false);setOnlyPinned(false);}}><span className="scope-symbol" aria-hidden="true">{["◎","◒","PL","TR"][index]}</span>{scopeLabel[id]}<Icon name="arrow" size={12}/></button>)}</div></div>
        <fieldset className="scope-time"><legend>Zakres zestawu</legend><div role="group" aria-label="Zakres czasu">{([24,72,168] as const).map(value=><button key={value} aria-pressed={hours===value} onClick={()=>setHours(value)}>{value===168 ? "7 dni" : `${value} h`}</button>)}</div><p>Koniec okna: czas przygotowania zestawu.</p></fieldset>
        <div className="rail-categories"><p className="rail-label">Warstwy danych</p>{Object.entries(CATEGORY_LABELS).map(([id,label])=><button key={id} onClick={()=>inspectCategory(id as Category)} aria-pressed={view==="explore" && category===id}><i className={`category-dot category-${id}`}/><span>{label}</span><span className="layer-count">{situation ? number.format(situation.categoryCounts.find(item=>item.category===id)?.count || 0) : "·"}</span></button>)}</div>
        <div className="rail-personal"><button onClick={()=>{navigate("explore");setOnlyPinned(true);setCategory(undefined);setSourceId(undefined);setSearch("");}}><span aria-hidden="true">☆</span>Przypięte<span>{pinnedIds.length}</span></button><p>Zapisane tylko w tej przeglądarce.</p></div>
        <div className="rail-bottom"><span>Sextet / publiczne źródła</span><p>Automatyczny przegląd bez AI. Nie służy do decyzji o bezpieczeństwie.</p><button onClick={()=>{setLegacy(value=>!value);setPanel(null);}}>{legacy ? "Wróć do przeglądu" : "Filtry szczegółowe"}<Icon name="arrow" size={11}/></button><a href={assetPath("/THIRD_PARTY_NOTICES.txt")} target="_blank" rel="noopener noreferrer">Licencje</a></div>
      </aside>
      <main id="sextet-content" className="sextet-content" tabIndex={-1}>
        {!snapshot && !error && <section className="cockpit-loading" role="status"><span className="section-eyebrow">Sextet Monitor</span><h1>Odczyt źródeł</h1><p>Przygotowuję przegląd na podstawie publicznego zestawu.</p><div className="skeleton-block"/><div className="skeleton-block"/></section>}
        {snapshot && situation && !legacy && <>
          <div className="cockpit-heading"><div><span className="section-eyebrow">{scopeLabel[scope]} / {hours===168 ? "ostatnie 7 dni zestawu" : `ostatnie ${hours} h zestawu`}</span><h1>{view==="overview" ? "Przegląd sytuacji" : view==="explore" ? onlyPinned ? "Twoje przypięte zapisy" : onlyChanges ? "Nowe i zmienione zapisy" : "Mapa i dane" : "Briefing dla grupy"}</h1><p>{view==="overview" ? "Wybrane zapisy, kontekst i źródła. Bez dopisywania pewności." : view==="explore" ? "Szukaj w tytułach, opisach, krajach i nazwach źródeł." : "Zestawienie do skopiowania do Signala, zapisania lub wydruku."}</p></div><span className="edition-stamp">SEXTET<span>{snapshotCalendarDay(snapshot.generated_at)?.replaceAll("-",".")}</span><small>PUBLICZNY ZESTAW</small></span></div>
          {stale && <p className="snapshot-caution">Dane mają ponad 3 godziny. To zapis z podanej chwili, nie obraz na żywo. Harmonogram publikacji może się opóźniać.</p>}
          {!storageAvailable && <p className="snapshot-caution">Pamięć przeglądarki jest niedostępna. Przypięcia i porównanie mogą nie przetrwać zamknięcia karty.</p>}
          {view==="overview" && <><SituationOverview situation={situation} snapshot={snapshot} selectedId={selectedId} pinnedIds={pinnedIds} onSelect={select} onPin={togglePin} onExplore={()=>navigate("explore")} onBriefing={()=>{setBriefingMode("suggested");navigate("briefing");}} query={mapQuery}/><section className="changes-block"><div className="section-heading"><h2>Od Twojego punktu porównania</h2><Icon name="clock"/></div>{delta?.status==="newer_snapshot" ? <><p className="changes-lead"><strong>{delta.addedIds.length} nowych</strong> i <strong>{delta.changedIds.length} zmienionych</strong> zapisów w całym zestawie od {formatDate(delta.comparedAt)}.</p><p className="section-note">W wybranym obszarze i czasie: {scopedChanges.length}. Nowy zapis może opisywać starsze zdarzenie.</p><button onClick={()=>{navigate("explore");setOnlyChanges(true);setCategory(undefined);setSourceId(undefined);setSearch("");}}>Pokaż zmiany w tym zakresie<Icon name="arrow" size={12}/></button>{delta.missingCount>0 && <p className="section-note">{delta.missingCount} wcześniejszych zapisów nie ma w obecnym zestawie. To nie oznacza zakończenia zdarzeń.</p>}</> : <><p className="changes-lead">{delta?.status==="out_of_order" ? "Odczytany zestaw jest starszy od punktu porównania." : delta?.status==="same_snapshot" ? "Zestaw ma ten sam czas publikacji co punkt porównania." : "Pierwszy odczyt na tym urządzeniu."}</p><p className="section-note">Porównanie pojawi się po publikacji nowszego zestawu. Pamiętamy identyfikatory i odciski publicznych rekordów, nie prywatną historię.</p></>}<button className="text-button" disabled={delta?.status==="out_of_order"} onClick={markBaseline}>Zapamiętaj obecny zestaw</button><details className="comparison-notes"><summary>Zakres porównania</summary><ul>{delta?.limitations.map((note,index)=><li key={index}>{note}</li>)}</ul></details></section></>}
          {view==="explore" && <>
            <div className="explore-filters"><label className="search-field"><Icon name="filter"/><input ref={searchInput} value={search} maxLength={200} onChange={event=>setSearch(event.target.value)} placeholder="Szukaj: kraj, komunikat, CVE…" aria-label="Szukaj w publicznych zapisach"/><kbd>/</kbd></label><label>Źródło<select aria-label="Filtr źródła" value={sourceId || ""} onChange={event=>setSourceId((event.target.value || undefined) as PublicSourceId|undefined)}><option value="">Wszystkie źródła</option>{coverage?.entries.map(source=><option key={source.id} value={source.id}>{PUBLIC_SOURCE_INFO[source.id].name} · {source.label}</option>)}</select></label><label>Kategoria<select aria-label="Filtr kategorii" value={category || ""} onChange={event=>setCategory((event.target.value || undefined) as Category|undefined)}><option value="">Wszystkie kategorie</option>{Object.entries(CATEGORY_LABELS).map(([id,label])=><option key={id} value={id}>{label}</option>)}</select></label><button className="quiet-button" onClick={()=>{setSearch("");setCategory(undefined);setSourceId(undefined);setOnlyPinned(false);setOnlyChanges(false);}}>Wyczyść</button></div>
            <div className="explore-layout"><section className="explore-records" id="events-list" tabIndex={-1} aria-label="Lista pasujących zapisów"><div className="section-heading"><h2>Zapisy: {number.format(records.length)}</h2><button className="mobile-map-toggle" onClick={()=>setMobileMap(value=>!value)}>{mobileMap ? "Ukryj mapę" : "Pokaż mapę"}<Icon name="map" size={14}/></button><span>{records.filter(event=>!event.geometry).length} bez pozycji</span></div>{(onlyPinned || onlyChanges) && <p className="section-note">{onlyPinned ? `${pinnedHere.length} przypiętych w tym obszarze i czasie. Przypięcia są lokalne, nie są częścią udostępnianego linku.` : "Zmiany względem lokalnego punktu porównania. Link nie przenosi tego punktu na inne urządzenie."}</p>}<p className="section-note">Czas każdego wiersza ma podpisaną podstawę. Starsze aktywne ostrzeżenie nie jest nowym incydentem.</p><SignalRows events={records.slice(0,pageSize)} selectedId={selectedId} pinnedIds={pinnedIds} onSelect={select} onPin={togglePin} addedIds={delta?.addedIds} changedIds={delta?.changedIds}/>{pageSize<records.length && <button className="load-more" onClick={()=>setPageSize(value=>value+PAGE_SIZE)}>Pokaż kolejne {Math.min(PAGE_SIZE,records.length-pageSize)} · wyświetlono {pageSize} z {records.length}</button>}</section><section className={`explore-map-block ${mobileMap ? "mobile-map-open" : ""}`} aria-label="Mapa pasujących zapisów"><div className="cockpit-map"><EventMap events={mapRecords} query={mapQuery} cameraScope={scope} selectedId={selectedId} onSelect={select} onFallback={()=>document.getElementById("events-list")?.focus()}/></div><p className="section-note">Mapa: {mapRecords.length} z {records.length} pasujących zapisów, maks. {MAP_LIMIT}. Lista udostępnia cały wynik. Brak geometrii pozostaje poza mapą.</p></section></div>
          </>}
          {view==="briefing" && <section className="briefing-workspace"><div className="briefing-config"><div className="briefing-mode" role="group" aria-label="Zawartość briefingu"><button aria-pressed={briefingMode==="suggested"} onClick={()=>setBriefingMode("suggested")}>Wybór monitora</button><button aria-pressed={briefingMode==="pinned"} onClick={()=>setBriefingMode("pinned")}>Moje przypięte · {pinnedHere.length}</button></div><p>Obszar: {scopeLabel[scope]}. Zakres: {hours} h przed przygotowaniem zestawu. Maksymalnie 12 pozycji.</p>{briefingMode==="pinned" && <p>{pinnedIds.length-pinnedHere.length} przypiętych zapisów jest poza zakresem lub nie ma ich w tym zestawie.</p>}<details className="pin-manager"><summary>Zarządzaj przypiętymi ({pinnedIds.length}/30)</summary>{pinnedIds.length===0 && <p>Przypnij zapis gwiazdką przy jego tytule.</p>}{pinnedIds.map(id=><div key={id}><span>{snapshot.events.find(event=>event.id===id)?.title || `Zapis niedostępny w zestawie (${id.slice(0,8)})`}</span><button onClick={()=>togglePin(id)} aria-label={`Usuń przypięcie ${id}`}>Usuń</button></div>)}</details><div className="briefing-buttons"><button className="primary-button" onClick={()=>void copy(briefText,"Skopiowano briefing z datami i linkami. Możesz wkleić go do Signala.")}>Kopiuj do Signala<Icon name="arrow" size={14}/></button><button onClick={downloadBriefing}>Zapisz .md</button><button onClick={()=>window.print()}>Drukuj / PDF</button></div><p className="section-note">Nic nie jest wysyłane automatycznie. Tekst powstaje z reguł i pól źródłowych, bez modelu AI.</p></div><article className="briefing-paper"><div className="paper-masthead"><span>SEXTET</span><span>BRIEFING / {scopeLabel[scope].toUpperCase()}</span></div><pre>{briefText}</pre><div className="paper-end">Publiczne źródła · jawne ograniczenia · {snapshot.generated_at.slice(0,10)}</div></article></section>}
          <section className="source-ribbon" aria-label="Stan wszystkich źródeł"><div className="section-heading"><h2>Łańcuch źródeł</h2><button onClick={showSources}>Szczegóły odczytów<Icon name="arrow" size={12}/></button></div><div className="source-ribbon-items">{coverage?.entries.map(entry=><button key={entry.id} onClick={()=>inspectSource(entry.id)} title={`${entry.name}: ${entry.label}. Odczyt: ${formatDate(entry.source?.last_success_at)}.`}><i className={`feed-dot ${entry.tone}`}/><span>{PUBLIC_SOURCE_INFO[entry.id].name}<small>{entry.label}</small></span><strong>{number.format(entry.records)}</strong></button>)}</div><p>{snapshot.events.length} zapisów w całym zestawie. Liczniki źródeł mogą obejmować ten sam zapis. Dziewięć kanałów nie oznacza dziewięciu niezależnych potwierdzeń.</p></section>
          <details className="cockpit-limitations"><summary>Co ten przegląd może, a czego nie potrafi</summary><ul>{situation.limitations.map((limitation,index)=><li key={index}>{limitation}</li>)}</ul><p>Brak pełnych danych wojskowych, AIS, śledzenia lotów i lokalnych pomiarów zakłóceń GNSS. Cloudflare i GitHub opisują własne usługi, nie cały internet. Publiczny podgląd nie łączy się z prywatną bazą.</p></details>
        </>}
        {legacy && <section className="legacy-workspace"><div className="legacy-return"><button onClick={()=>setLegacy(false)}>Wróć do nowego przeglądu</button><p>Dotychczasowe filtry, dowolna podstawa czasu i przesuwanie okna.</p></div><DetailedExplorer/></section>}
      </main>
      {panel && <aside id="public-details-panel" ref={detailPanel} className="sextet-detail-panel" aria-label={panel==="sources" ? "Źródła i świeżość" : "Szczegóły wybranego zapisu"} tabIndex={-1}><div className="detail-panel-header"><h2>{panel==="sources" ? "Źródła i świeżość" : "Zapis i dowody"}</h2><button onClick={closePanel} aria-label="Zamknij szczegóły"><Icon name="close"/></button></div>{panel==="sources" ? snapshot && <SourcePanel snapshotAt={snapshot.generated_at} publicCoverage={coverage || undefined} selectedSourceId={sourceId} onSelectSource={inspectSource} sources={snapshot.sources} loading={false} error={null} onRetry={()=>setRefresh(value=>value+1)}/> : <>{detail && <div className="detail-quick-actions"><PinButton event={detail} pinned={pinnedIds.includes(detail.id)} onPin={togglePin}/><span>{pinnedIds.includes(detail.id) ? "W Twoim briefingu" : "Przypnij do briefingu"}</span><button onClick={copyLink}><Icon name="link" size={13}/>Link do zapisu</button></div>}<EventEvidence publicMode detail={detail} readAt={snapshot?.generated_at} selected={Boolean(selectedId)} loading={loading && !snapshot} error={selectedId && !detail ? "Tego identyfikatora nie ma w obecnym publicznym zestawie. Link nie jest archiwum; wybierz inny zapis." : null} outsideFilter={Boolean(detail && !(view==="explore" ? records : situation?.events)?.some(event=>event.id===detail.id))} onSelect={select} onRetry={closePanel}/></>}</aside>}
    </div>
    {copyFallback && <section className="copy-fallback"><label htmlFor="manual-copy">Tekst do ręcznego skopiowania</label><textarea id="manual-copy" readOnly value={copyFallback} onFocus={event=>event.target.select()}/><button onClick={()=>setCopyFallback("")}>Zamknij pole</button></section>}
    <div className={`sextet-toast ${toast ? "visible" : ""}`} role="status" aria-live="polite">{toast}</div>
  </div>;
}
