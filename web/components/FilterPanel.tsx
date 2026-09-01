import { useEffect, useState } from "react";
import type { Category, EventQuery } from "@/lib/contracts";
import { CATEGORY_LABELS, DEFAULT_QUERY, SEVERITY_LABELS, TIME_BASIS_HELP, TIME_BASIS_LABELS, WARSAW, serializeQuery, timeWindowPatch } from "@/lib/filters";
import { countryName, formatDate } from "@/lib/format";
import { Icon } from "./Icon";

const COUNTRY_OPTIONS = ["PL","TR","UA","DE","CZ","SK","LT","LV","EE","BY","RU","FI","SE","NO","DK","GB","IE","FR","ES","PT","IT","AT","CH","HU","RO","BG","GR","CY","HR","SI","RS","BA","AL","ME","MK","MD","GE","AM","AZ","IS","US","CA","MX","BR","AR","CL","CO","IL","PS","IR","IQ","SY","LB","JO","SA","AE","YE","AF","PK","IN","BD","CN","TW","JP","KR","KP","TH","VN","MY","ID","PH","AU","NZ","EG","LY","TN","DZ","MA","SD","ET","KE","ZA","NG","CD"];
const WINDOWS = [6,12,24,48,168];
export default function FilterPanel({query,onChange,onReset,snapshot}:{query:EventQuery;onChange:(patch:Partial<EventQuery>)=>void;onReset:()=>void;snapshot?:{generatedAt:string;countries:string[]}}) {
  const [expanded,setExpanded]=useState(true);
  const [timePosition,setTimePosition]=useState<{hours:number;until?:string}>({hours:0});
  const hoursBack = !query.since && !query.until ? 0 : query.until === timePosition.until && query.until ? timePosition.hours : null;
  const endLabel = hoursBack === 0 ? snapshot ? "Koniec zestawu" : "Teraz" : hoursBack === null ? "Ustalony czas" : `${hoursBack} h temu`;
  const moveWindow=(hours:number)=>{
    const patch=timeWindowPatch(query,hours,snapshot ? Date.parse(snapshot.generatedAt) : Date.now());
    setTimePosition({hours,until:patch.until});
    onChange(patch);
  };
  useEffect(()=>{if(window.matchMedia("(max-width: 900px)").matches)setExpanded(false);},[]);
  const countries = [...new Set([...(query.country ? [query.country] : []), ...(snapshot?.countries || COUNTRY_OPTIONS)])];
  const isWarsaw = query.lat === WARSAW.lat && query.lon === WARSAW.lon;
  const radiusOptions = [...new Set([100,250,500,1000, ...(query.radius_km ? [query.radius_km] : [])])].sort((a,b)=>a-b);
  return <details className="filter-panel" open={expanded} onToggle={(event)=>setExpanded(event.currentTarget.open)}>
    <summary><span><Icon name="filter"/>Filtry</span><span className="mobile-hint">Rozwiń / zwiń</span></summary>
    <div className="filter-content">
      <fieldset><legend>Czas</legend>
        <label htmlFor="window">Zakres</label>
        <select id="window" value={query.window_hours} onChange={(event)=>onChange({window_hours:Number(event.target.value)})}>
          {[...new Set([...WINDOWS,query.window_hours])].sort((a,b)=>a-b).map((hours)=><option key={hours} value={hours}>{hours === 168 ? "Ostatnie 7 dni" : `Ostatnie ${hours} h`}</option>)}
        </select>
        <label htmlFor="time-basis">Według</label>
        <select id="time-basis" value={query.time_basis} onChange={(event)=>{
          const basis=event.target.value as EventQuery["time_basis"];
          onChange({time_basis:basis,...(basis==="published" || basis==="validity" ? {include_inactive:true} : {})});
        }}>
          {Object.entries(TIME_BASIS_LABELS).filter(([value])=>!snapshot || value!=="changed").map(([value,label])=><option key={value} value={value}>{label}</option>)}
        </select>
        <p className="field-help">{snapshot && query.time_basis==="validity" ? "Ważność źródłowa przecina okno. Status pochodzi z zestawu, nie z bieżącego odczytu ani odtworzenia historii." : TIME_BASIS_HELP[query.time_basis]}</p>
        <label htmlFor="time-offset">Koniec okna</label>
        <div className="time-shift">
          <input id="time-offset" type="number" min={0} max={168} step={1} value={hoursBack ?? ""} placeholder="—" aria-valuetext={endLabel} aria-describedby="time-shift-help" onChange={(event)=>{
            if(event.target.value==="")return;
            const hours=Number(event.target.value);
            if(Number.isInteger(hours) && hours>=0 && hours<=168)moveWindow(hours);
          }}/>
          <output htmlFor="time-offset">{endLabel}</output>
          <button type="button" className="text-button" disabled={!query.since && !query.until} onClick={()=>moveWindow(0)}>{snapshot ? "Zestaw" : "Teraz"}</button>
        </div>
        <p id="time-shift-help" className="field-help">{snapshot ? "Godziny przed końcem zestawu" : "Godziny temu"} · 0–168 · krok 1 h. Zachowuje szerokość okna.</p>
        {(query.since || query.until || snapshot) && <div className="absolute-range"><span>Zakres czasu · Europe/Warsaw</span><time>{formatDate(query.since || (snapshot ? new Date(Date.parse(query.until || snapshot.generatedAt)-query.window_hours*3600000).toISOString() : null))} → {formatDate(query.until || snapshot?.generatedAt)}</time></div>}
      </fieldset>
      <fieldset><legend>Obszar</legend>
        {!snapshot && <><label htmlFor="region">Region</label><select id="region" value={query.region || ""} onChange={(event)=>onChange({region:event.target.value==="europe" ? "europe" : undefined,country:undefined})}>
          <option value="">Cały świat</option><option value="europe">Europa</option>
        </select></>}
        <label htmlFor="country">Kraj</label><select id="country" value={query.country || ""} onChange={(event)=>onChange({country:event.target.value || undefined,region:undefined})}>
          <option value="">Wszystkie kraje</option>{countries.map((country)=><option key={country} value={country}>{countryName(country)} ({country})</option>)}
        </select>
        {!snapshot && <><label className="check-label"><input type="checkbox" checked={Boolean(query.radius_km)} onChange={(event)=>onChange(event.target.checked ? {...WARSAW,radius_km:500} : {lat:undefined,lon:undefined,radius_km:undefined})}/>Filtr promienia</label>
        {query.radius_km && <div className="radius-controls">
          <label htmlFor="radius">Promień w kilometrach</label><select id="radius" value={query.radius_km} onChange={(event)=>onChange({radius_km:Number(event.target.value)})}>{radiusOptions.map((km)=><option key={km} value={km}>{km} km</option>)}</select>
          <p className="field-help">{isWarsaw ? "Od centrum Warszawy. Bez granic krajów i punktów orientacyjnych." : `Punkt zapytania: ${query.lat?.toFixed(3)}, ${query.lon?.toFixed(3)}.`}</p>
          {!isWarsaw && <button className="text-button" onClick={()=>onChange({...WARSAW})}>Ustaw centrum Warszawy</button>}
        </div>}</>}
      </fieldset>
      <fieldset><legend>Treść i dowody</legend>
        <label htmlFor="category">Kategoria</label><select id="category" value={query.category || ""} onChange={(event)=>onChange({category:(event.target.value || undefined) as Category | undefined})}>
          <option value="">Wszystkie kategorie</option>{Object.entries(CATEGORY_LABELS).filter(([value])=>!snapshot || ["earthquake","weather","cyber"].includes(value)).map(([value,label])=><option key={value} value={value}>{label}</option>)}
        </select>
        <label htmlFor="severity">Minimalna waga</label><select id="severity" value={query.severity_min} onChange={(event)=>onChange({severity_min:Number(event.target.value)})}>
          <option value="0">Wszystkie, także nieokreślone</option>{SEVERITY_LABELS.slice(1).map((label,index)=><option key={label} value={index+1}>{label} i wyższa</option>)}
        </select>
        <p className="field-help">Waga źródłowa. Nie jest prawdopodobieństwem.</p>
        {!snapshot && <label className="check-label"><input type="checkbox" checked={query.min_sources >= 2} onChange={(event)=>onChange({min_sources:event.target.checked ? 2 : 1})}/><span>Min. {Math.max(2,query.min_sources)} niezależne źródła</span></label>}
        <label className="check-label"><input type="checkbox" checked={query.include_inactive} onChange={(event)=>onChange({include_inactive:event.target.checked})}/><span>Także wygasłe i odwołane</span></label>
      </fieldset>
      <button className="reset-filters" disabled={serializeQuery(query)===serializeQuery(snapshot ? {...DEFAULT_QUERY,include_inactive:true} : DEFAULT_QUERY)} onClick={onReset}>Wyczyść filtry</button>
    </div>
  </details>;
}
