import type { Category, EventDetail } from "./contracts";
import { countryName, eventTime } from "./format";
import { PUBLIC_SOURCE_INFO, PUBLIC_TIME_BASIS } from "./public-snapshot";

export interface ViewFilters {category?:Category;sourceId?:string;search?:string;onlyIds?:readonly string[]}
const normalize=(value:string)=>value.normalize("NFD").replace(/[\u0300-\u036f]/g,"").replace(/ł/g,"l").replace(/Ł/g,"L").toLocaleLowerCase("pl");
const sourceTime=(event:EventDetail)=>{const value=Date.parse(eventTime(event,PUBLIC_TIME_BASIS[event.category]) || "");return Number.isFinite(value) ? value : -Infinity;};

/** Search only public fields, with literal words; never interpret queries as code or regex. */
export function filterViewEvents(events:readonly EventDetail[],filters:ViewFilters):EventDetail[]{
  const words=normalize((filters.search || "").slice(0,200)).split(/\s+/).filter(Boolean);
  const ids=filters.onlyIds ? new Set(filters.onlyIds) : null;
  return events.filter(event=>{
    if(filters.category && event.category!==filters.category)return false;
    if(filters.sourceId && !event.source_ids.includes(filters.sourceId))return false;
    if(ids && !ids.has(event.id))return false;
    if(!words.length)return true;
    const text=normalize([event.title,event.description,...event.countries.flatMap(code=>[code,countryName(code)]),...event.source_ids.map(id=>PUBLIC_SOURCE_INFO[id as keyof typeof PUBLIC_SOURCE_INFO]?.name || id)].join(" "));
    return words.every(word=>text.includes(word));
  }).sort((a,b)=>sourceTime(b)-sourceTime(a) || a.id.localeCompare(b.id));
}

export function eventSourceNames(event:EventDetail):string{
  return event.source_ids.map(id=>PUBLIC_SOURCE_INFO[id as keyof typeof PUBLIC_SOURCE_INFO]?.name || id).join(" · ");
}

/** Calendar label for a snapshot instant, never for a date-only source field. */
export function snapshotCalendarDay(value:string|null|undefined):string|null{
  if(!value || !Number.isFinite(Date.parse(value)))return null;
  const parts=new Intl.DateTimeFormat("en-CA",{timeZone:"Europe/Warsaw",year:"numeric",month:"2-digit",day:"2-digit"}).formatToParts(new Date(value));
  const part=(type:string)=>parts.find(item=>item.type===type)?.value;
  return `${part("year")}-${part("month")}-${part("day")}`;
}
