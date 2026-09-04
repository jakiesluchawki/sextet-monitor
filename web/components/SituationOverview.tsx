"use client";

import { useMemo } from "react";
import dynamic from "next/dynamic";
import type { EventQuery } from "@/lib/contracts";
import { formatDate } from "@/lib/format";
import { mapGeometryCoverage } from "@/lib/map-camera";
import type { PublicSnapshot } from "@/lib/public-snapshot";
import type { Situation } from "@/lib/situation";
import { Icon } from "./Icon";
import SignalRows from "./SignalRows";

const EventMap=dynamic(()=>import("./EventMap"),{ssr:false,loading:()=> <div className="map-loading" role="status">Ładowanie silnika mapy…</div>});
const MAP_LIMIT=500;
const clockLabel=new Intl.DateTimeFormat("pl-PL",{hour:"2-digit",minute:"2-digit",timeZone:"Europe/Warsaw"});
const dayLabel=new Intl.DateTimeFormat("pl-PL",{day:"2-digit",month:"2-digit",timeZone:"Europe/Warsaw"});

export default function SituationOverview({situation,snapshot,selectedId,pinnedIds,onSelect,onPin,onExplore,onBriefing,query}:{
  situation:Situation;snapshot:PublicSnapshot;selectedId:string|null;pinnedIds:string[];
  onSelect:(id:string)=>void;onPin:(id:string)=>void;onExplore:()=>void;onBriefing:()=>void;query:EventQuery;
}){
  const mapEvents=useMemo(()=>situation.events.slice(0,MAP_LIMIT),[situation.events]);
  const mapCoverage=useMemo(()=>mapGeometryCoverage(mapEvents),[mapEvents]);
  const reasons=useMemo(()=>Object.fromEntries(situation.highlights.map(({event,reason})=>[event.id,reason])),[situation.highlights]);
  const peak=Math.max(1,...situation.timeline.map((bin)=>bin.count));
  const labelStep=Math.max(1,Math.ceil(situation.timeline.length/6));
  const intervalHours=situation.timeline.length?(Date.parse(situation.timeline[0].end)-Date.parse(situation.timeline[0].start))/3_600_000:0;
  return <>
    <div className="overview-layout">
      <section className="overview-map-block" aria-labelledby="overview-map-heading">
        <div className="section-heading"><h2 id="overview-map-heading">W zasięgu źródeł</h2><span>{situation.scopeLabel} · {situation.hours} h</span></div>
        <div className="cockpit-map"><EventMap events={mapEvents} query={query} selectedId={selectedId} onSelect={onSelect} onFallback={onExplore} initialProjection="globe" cameraScope={situation.scope}/></div>
        <div className="map-context"><span>W całym przeglądzie: <strong>{situation.mapped}</strong> z geometrią, <strong>{situation.unlocated}</strong> bez geometrii.</span>{situation.events.length>MAP_LIMIT && <span>Mapa obejmuje pierwsze {MAP_LIMIT} z {situation.events.length} rekordów według dat źródłowych, w tym {mapCoverage.mapped} z geometrią. Wyróżnienia i oś czasu uwzględniają cały przegląd.</span>}<button type="button" onClick={onExplore}>Przejdź do rekordów <Icon name="arrow" size={13}/></button></div>
      </section>
      <section className="highlights-block" aria-labelledby="highlights-heading">
        <div className="section-heading"><h2 id="highlights-heading">Warto sprawdzić</h2><span>{situation.highlights.length} wyróżnień</span></div>
        <p className="section-note">Przedstawiciele kategorii, świeże daty i waga podana przez źródło. Przy każdym rekordzie widać powód wyboru. To nie jest ranking zagrożeń.</p>
        <SignalRows events={situation.highlights.map(({event})=>event)} selectedId={selectedId} pinnedIds={pinnedIds} onSelect={onSelect} onPin={onPin} reasons={reasons} compact/>
        <button type="button" className="section-link" onClick={onBriefing}>Przygotuj briefing z odnośnikami <Icon name="arrow" size={14}/></button>
      </section>
    </div>
    <section className="activity-block" aria-labelledby="activity-heading">
      <div className="section-heading"><h2 id="activity-heading">Zapisy w czasie</h2><span>Przedziały {intervalHours} h · Europe/Warsaw</span></div>
      <p className="section-note">Trzęsienia i katastrofy według początku zdarzenia; pogoda i lotnictwo według ważności; pozostałe kategorie według publikacji. Ostrzeżenie lub data znana tylko co do dnia może wystąpić w kilku słupkach. Ich suma nie jest liczbą unikalnych zdarzeń.</p>
      <div className="activity-chart" role="img" aria-label={`Liczba rekordów przecinających kolejne przedziały w zakresie ${formatDate(situation.since)} do ${formatDate(situation.until)}. Największa liczba w przedziale: ${Math.max(0,...situation.timeline.map((bin)=>bin.count))}. Dokładne wartości w tabeli poniżej.`}>
        {situation.timeline.map((bin,index)=>{
          const label=situation.hours>72?dayLabel.format(new Date(bin.start)):clockLabel.format(new Date(bin.start));
          return <div key={bin.start} className="activity-column" title={`${formatDate(bin.start)} do ${formatDate(bin.end)}: ${bin.count} rekordów`} aria-hidden="true"><span className="activity-bar" style={{height:`${100*bin.count/peak}%`}}/><small>{index%labelStep===0 || index===situation.timeline.length-1?label:""}</small></div>;
        })}
      </div>
      <div className="activity-range"><time dateTime={situation.since}>{formatDate(situation.since)}</time><span>Stan zestawu: <time dateTime={snapshot.generated_at}>{formatDate(snapshot.generated_at)}</time></span></div>
      <details className="activity-data"><summary>Dokładne wartości i podstawy czasu</summary><p className="section-note">Przedziały zawierają początek i nie zawierają końca. Wartości liczą pasujące rekordy, a nie nowe potwierdzone incydenty. Odczyt źródła nie zastępuje brakującej daty zdarzenia.</p><div className="table-scroll"><table><caption>Rekordy przecinające przedziały; ten sam rekord może wystąpić w kilku wierszach.</caption><thead><tr><th scope="col">Początek</th><th scope="col">Koniec</th><th scope="col">Razem</th><th scope="col">Zdarzenie</th><th scope="col">Publikacja</th><th scope="col">Ważność</th></tr></thead><tbody>{situation.timeline.map((bin)=><tr key={bin.start}><th scope="row"><time dateTime={bin.start}>{formatDate(bin.start)}</time></th><td><time dateTime={bin.end}>{formatDate(bin.end)}</time></td><td>{bin.count}</td><td>{bin.byBasis.occurred}</td><td>{bin.byBasis.published}</td><td>{bin.byBasis.validity}</td></tr>)}</tbody></table></div></details>
    </section>
  </>;
}
