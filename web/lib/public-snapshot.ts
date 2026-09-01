import type { Geometry } from "geojson";
import type { EventDetail, EventQuery, EventsResponse, SourceStatus } from "./contracts";
import { assetPath } from "./assets";
import { changeQuery, DEFAULT_QUERY } from "./filters";
import { eventTime, safeHttpUrl } from "./format";

export const MAX_SNAPSHOT_BYTES=16*1024*1024;
export const PUBLIC_SOURCE_IDS=["usgs","meteoalarm","cisa_kev"] as const;
export const PUBLIC_DEFAULT_QUERY:EventQuery={...DEFAULT_QUERY,include_inactive:true};
export interface PublicSnapshot {format:1;version:string;generated_at:string;sources:SourceStatus[];events:EventDetail[];limitations:string[]}
const sourceIds=new Set<string>(PUBLIC_SOURCE_IDS);
const sourceStates=new Set(["pending","ok","ok_empty","partial","error","stale","disabled"]);
const categories=new Set(["earthquake","weather","cyber"]);
const lifecycleStates=new Set(["active","expired","withdrawn","unknown"]);
const iso=/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})$/;
const PUBLIC_FIELDS={
  source:["id","name","status","enabled","requires_key","last_attempt_at","last_success_at","newest_content_at","next_due_at","record_count","error","poll_interval_seconds","coverage","license_name","license_url","attribution"],
  event:["id","kind","category","title","description","occurred_start","occurred_end","issued_at","source_updated_at","first_seen_at","last_seen_at","last_changed_at","valid_from","valid_to","countries","geometry","location_precision","time_precision","severity","severity_label","severity_reason","original_severity","lifecycle_status","verification_status","anomaly_score","source_ids","source_count","independent_source_count","source_url","tags","change_type","evidence","revisions","relations"],
  evidence:["id","source_id","source_name","provider_record_id","source_url","retrieved_at","issued_at","source_updated_at","source_snapshot_at","origins","payload_hash","raw","raw_retained","attribution","license_url"],
  relation:["event_id","title","relation_type","reason","distance_km","time_delta_hours"],
} as const;
function requirePublicFields(value:Record<string,unknown>,kind:keyof typeof PUBLIC_FIELDS){
  const allowed:readonly string[]=PUBLIC_FIELDS[kind];
  requireValue(Object.keys(value).every((key)=>allowed.includes(key)),`nieoczekiwane pola ${kind}.`);
}
const uuid=/^[a-f0-9]{8}-[a-f0-9]{4}-[1-8][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/i;
const isObject=(value:unknown):value is Record<string,unknown>=>Boolean(value) && typeof value==="object" && !Array.isArray(value);
const string=(value:unknown,max=10000):value is string=>typeof value==="string" && value.length<=max;
const strings=(value:unknown,max=200):value is string[]=>Array.isArray(value) && value.length<=max && value.every((item)=>string(item));
const integer=(value:unknown,min:number,max:number):value is number=>Number.isInteger(value) && typeof value==="number" && value>=min && value<=max;
function timestamp(value:unknown,nullable=true):boolean {
  if(nullable && value===null)return true;
  if(typeof value!=="string" || !iso.test(value) || !Number.isFinite(Date.parse(value)))return false;
  const [year,month,day,hour,minute,second]=value.slice(0,19).split(/[-T:]/).map(Number);
  return month>=1 && month<=12 && day>=1 && day<=new Date(Date.UTC(year,month,0)).getUTCDate() && hour<24 && minute<60 && second<60;
}
function publicUrl(value:unknown,nullable=true):boolean {
  if(nullable && value===null)return true;
  if(typeof value!=="string" || !safeHttpUrl(value))return false;
  const host=new URL(value).hostname.toLowerCase();
  return host!=="localhost" && !host.endsWith(".localhost") && !host.endsWith(".local") && !/^\[|^[\d.]+$/.test(host);
}
function requireValue(condition:unknown,message:string):asserts condition {
  if(!condition)throw new Error("Nieprawidłowy zestaw publiczny: "+message);
}
function validGeometry(value:unknown,budget:{positions:number},depth=0):value is Geometry|null {
  if(value===null)return true;
  if(!isObject(value) || depth>8)return false;
  const position=(part:unknown):boolean=>{
    budget.positions+=1;
    return budget.positions<=1_000_000 && Array.isArray(part) && part.length>=2 && part.length<=3
      && part.every((n)=>typeof n==="number" && Number.isFinite(n)) && Math.abs(part[0])<=180 && Math.abs(part[1])<=90;
  };
  const nested=(part:unknown,levels:number):boolean=>levels===0 ? position(part) : Array.isArray(part) && part.length>0 && part.every((item)=>nested(item,levels-1));
  if(value.type==="GeometryCollection")return Array.isArray(value.geometries) && value.geometries.length>0 && value.geometries.length<=200 && value.geometries.every((part)=>validGeometry(part,budget,depth+1));
  const levels=({Point:0,MultiPoint:1,LineString:1,MultiLineString:2,Polygon:2,MultiPolygon:3} as Record<string,number>)[String(value.type)];
  return levels!==undefined && nested(value.coordinates,levels);
}

/** Fail closed on private sources, raw payloads, history, invalid dates and broken evidence links. */
export function validatePublicSnapshot(value:unknown,nowMs=Date.now()):PublicSnapshot {
  requireValue(isObject(value) && value.format===1,"nieobsługiwany format.");
  requireValue(Object.keys(value).every((key)=>["format","version","generated_at","sources","events","limitations"].includes(key)),"nieoczekiwane pola manifestu.");
  requireValue(string(value.version,120) && value.version.length>0,"brak wersji.");
  requireValue(timestamp(value.generated_at,false) && Date.parse(value.generated_at as string)<=nowMs+300_000,"niepoprawny lub przyszły czas przygotowania.");
  requireValue(strings(value.limitations,100),"niepoprawne ograniczenia.");
  requireValue(Array.isArray(value.sources) && value.sources.length>0 && value.sources.length<=3,"niepoprawna lista źródeł.");
  const includedSources=new Set<string>();
  for(const item of value.sources){
    requireValue(isObject(item) && typeof item.id==="string" && sourceIds.has(item.id) && !includedSources.has(item.id),"źródło spoza zatwierdzonej listy lub duplikat.");
    requirePublicFields(item,"source");
    includedSources.add(item.id);
    requireValue(string(item.name,200) && string(item.status) && sourceStates.has(item.status) && item.requires_key===false && typeof item.enabled==="boolean","niepoprawny stan źródła.");
    for(const key of ["last_attempt_at","last_success_at","newest_content_at","next_due_at"])requireValue(timestamp(item[key]),"niepoprawna data źródła.");
    requireValue(integer(item.record_count,0,10000000) && integer(item.poll_interval_seconds,0,86400) && (item.error===null || string(item.error)),"niepoprawne metadane źródła.");
    requireValue((string(item.coverage) || isObject(item.coverage)) && string(item.license_name) && string(item.attribution) && publicUrl(item.license_url),"brak praw lub atrybucji źródła.");
  }
  requireValue(Array.isArray(value.events) && value.events.length<=10000,"zbyt duża lista rekordów.");
  const eventIds=new Set<string>(),budget={positions:0};
  for(const item of value.events){
    requireValue(isObject(item) && typeof item.id==="string" && uuid.test(item.id) && !eventIds.has(item.id),"niepoprawny lub powtórzony identyfikator.");
    requirePublicFields(item,"event");
    eventIds.add(item.id);
    for(const key of ["kind","category","title","description","location_precision","time_precision","severity_label","severity_reason","verification_status","change_type"])requireValue(string(item[key],key==="description"?100000:10000),"niepoprawny opis rekordu.");
    requireValue(categories.has(item.category as string) && ["incident","advisory","vulnerability_notice","measurement"].includes(item.kind as string) && integer(item.severity,0,4) && lifecycleStates.has(item.lifecycle_status as string) && item.anomaly_score===null,"niepoprawny typ lub waga rekordu.");
    for(const key of ["occurred_start","occurred_end","issued_at","source_updated_at","valid_from","valid_to","last_changed_at"])requireValue(timestamp(item[key]),"niepoprawna data rekordu.");
    for(const key of ["first_seen_at","last_seen_at"])requireValue(timestamp(item[key],false),"brak czasu przygotowania rekordu.");
    requireValue(strings(item.countries,250) && item.countries.every((code)=>/^[A-Z]{2}$/.test(code)) && strings(item.tags) && validGeometry(item.geometry,budget),"niepoprawna geometria, kraj lub tagi.");
    requireValue(strings(item.source_ids,3) && item.source_ids.length>0 && item.source_ids.every((id)=>includedSources.has(id)) && new Set(item.source_ids).size===item.source_ids.length && item.source_count===item.source_ids.length && integer(item.independent_source_count,0,10) && publicUrl(item.source_url),"niepoprawne pochodzenie rekordu.");
    requireValue(Array.isArray(item.revisions) && item.revisions.length===0,"historia prywatnego monitora nie jest częścią zestawu.");
    requireValue(Array.isArray(item.evidence) && item.evidence.length>0 && item.evidence.length<=20,"brak lub nadmiar dowodów.");
    for(const evidence of item.evidence){
      requireValue(isObject(evidence) && typeof evidence.source_id==="string" && (item.source_ids as string[]).includes(evidence.source_id) && evidence.raw===null && evidence.raw_retained===false,"niedozwolony payload lub źródło dowodu.");
      requirePublicFields(evidence,"evidence");
      for(const key of ["id","source_name","provider_record_id","payload_hash","attribution"])requireValue(string(evidence[key]),"niepoprawny opis dowodu.");
      requireValue(timestamp(evidence.retrieved_at,false) && timestamp(evidence.issued_at) && timestamp(evidence.source_updated_at) && (evidence.source_snapshot_at==null || timestamp(evidence.source_snapshot_at)) && strings(evidence.origins) && publicUrl(evidence.source_url) && publicUrl(evidence.license_url),"niepoprawne daty lub adresy dowodu.");
    }
    requireValue(Array.isArray(item.relations) && item.relations.length<=30,"niepoprawna lista relacji.");
    for(const relation of item.relations){
      requireValue(isObject(relation) && string(relation.event_id) && string(relation.title) && string(relation.relation_type) && string(relation.reason),"niepoprawna relacja.");
      requirePublicFields(relation,"relation");
      for(const key of ["distance_km","time_delta_hours"])requireValue(relation[key]===null || (typeof relation[key]==="number" && Number.isFinite(relation[key]) && (relation[key] as number)>=0),"niepoprawna miara relacji.");
    }
  }
  for(const item of value.events)for(const relation of item.relations)requireValue(eventIds.has(relation.event_id),"relacja wskazuje rekord poza publicznym zestawem.");
  return value as unknown as PublicSnapshot;
}

export async function loadPublicSnapshot(signal?:AbortSignal,fetcher:typeof fetch=fetch):Promise<PublicSnapshot> {
  const controller=new AbortController();
  const abort=()=>controller.abort();
  signal?.addEventListener("abort",abort,{once:true});
  if(signal?.aborted)abort();
  const timeout=setTimeout(abort,20000);
  try {
    const response=await fetcher(assetPath("/snapshot.json"),{signal:controller.signal,cache:"no-store",credentials:"omit",redirect:"error",headers:{Accept:"application/json"}});
    if(!response.ok)throw new Error(response.status===404 ? "Publiczny zestaw nie został jeszcze opublikowany." : "Nie udało się pobrać opublikowanego zestawu.");
    if(!response.headers.get("content-type")?.toLowerCase().includes("application/json"))throw new Error("Serwer nie zwrócił zestawu JSON.");
    if(Number(response.headers.get("content-length"))>MAX_SNAPSHOT_BYTES)throw new Error("Zestaw przekracza limit 16 MiB.");
    const reader=response.body?.getReader();
    if(!reader)throw new Error("Zestaw jest pusty.");
    const chunks:Uint8Array[]=[];let bytes=0;
    while(true){
      const part=await reader.read();
      if(part.done)break;
      bytes+=part.value.byteLength;
      if(bytes>MAX_SNAPSHOT_BYTES){await reader.cancel();throw new Error("Zestaw przekracza limit 16 MiB.");}
      chunks.push(part.value);
    }
    const joined=new Uint8Array(bytes);let offset=0;
    for(const chunk of chunks){joined.set(chunk,offset);offset+=chunk.byteLength;}
    let value:unknown;
    try{value=JSON.parse(new TextDecoder().decode(joined));}catch{throw new Error("Niepoprawny JSON publicznego zestawu.");}
    return validatePublicSnapshot(value);
  }catch(error){
    if(signal?.aborted)throw error;
    if(controller.signal.aborted)throw new Error("Przekroczono czas odczytu zestawu. Spróbuj ponownie.");
    throw error instanceof Error ? error : new Error("Nie można odczytać opublikowanego zestawu.");
  }finally{clearTimeout(timeout);signal?.removeEventListener("abort",abort);}
}

export function snapshotAge(generatedAt:string,nowMs:number):string {
  const minutes=Math.floor((nowMs-Date.parse(generatedAt))/60000);
  if(!Number.isFinite(minutes))return "Wiek zestawu nieustalony";
  if(minutes< -5)return "Sprawdź zegar urządzenia";
  if(minutes<1)return "Zestaw sprzed mniej niż minuty";
  if(minutes<60)return `Zestaw sprzed ${minutes} min`;
  if(minutes<1440)return `Zestaw sprzed ${Math.floor(minutes/60)} h`;
  return `Zestaw sprzed ${Math.floor(minutes/1440)} dni`;
}

export function changePublicQuery(query:EventQuery,patch:Partial<EventQuery>):EventQuery {
  const next=changeQuery(query,patch);
  if(patch.category==="cyber")next.time_basis="published";
  if(patch.category==="weather")next.time_basis="validity";
  if(patch.category==="earthquake")next.time_basis="occurred";
  return next;
}

/** The non-geospatial backend predicates, evaluated at the snapshot clock. */
export function filterPublicSnapshot(snapshot:PublicSnapshot,query:EventQuery):EventsResponse {
  if(query.time_basis==="changed" || query.region || query.radius_km!=null || query.lat!=null || query.lon!=null)throw new Error("Ten filtr wymaga prywatnego monitora i nie działa w publicznym podglądzie.");
  const now=Date.parse(snapshot.generated_at),until=query.until ? Date.parse(query.until) : now;
  const since=query.since ? Date.parse(query.since) : until-query.window_hours*3600000;
  if(!Number.isFinite(since) || !Number.isFinite(until) || since>=until || until>now || !integer(query.limit,1,1000))throw new Error("Nieprawidłowy zakres publicznego zestawu.");
  const matching=snapshot.events.filter((event)=>{
    if(query.category && event.category!==query.category)return false;
    if(query.country && !event.countries.includes(query.country))return false;
    if(event.severity<query.severity_min || event.independent_source_count<query.min_sources)return false;
    if(!query.include_inactive && (["expired","withdrawn"].includes(event.lifecycle_status) || (event.valid_to && Date.parse(event.valid_to)<=now)))return false;
    if(query.time_basis==="validity"){
      const start=event.valid_from ? Date.parse(event.valid_from) : NaN;
      const end=event.valid_to ? Date.parse(event.valid_to) : null;
      return Number.isFinite(start) && start<until && (end===null || (end>since && end>start));
    }
    const instant=Date.parse(eventTime(event,query.time_basis) || "");
    if(!Number.isFinite(instant))return false;
    if(query.time_basis==="published" && event.tags.includes("date_only_utc_anchor")){
      const day=Math.floor(instant/86400000)*86400000;
      return day<until && day+86400000>since;
    }
    return instant>=since && instant<until;
  });
  matching.sort((a,b)=>b.severity-a.severity || Date.parse(eventTime(b,query.time_basis) || "")-Date.parse(eventTime(a,query.time_basis) || "") || (a.id<b.id?-1:a.id>b.id?1:0));
  const items=matching.slice(0,query.limit),mapped=items.filter((event)=>event.geometry!==null).length;
  const limitations=[...snapshot.limitations,"To filtrowanie opublikowanego zestawu, bez połączenia z prywatnym monitorem. Statusy i odczyty dotyczą chwili jego przygotowania."];
  if(query.time_basis==="published")limitations.push("Data publikacji nie jest czasem incydentu. Dopasowanie publikacji dziennej oznacza przecięcie dnia z oknem, nie znaną godzinę.");
  if(query.time_basis==="validity")limitations.push("Źródłowy przedział ważności przecina okno. Status pochodzi z zestawu; nie jest odtworzonym stanem historycznym. Brak końca pozostaje nieznany.");
  return {items,total:matching.length,shown:items.length,mapped,unlocated:items.length-mapped,truncated:items.length<matching.length,query:{...query,since:new Date(since).toISOString(),until:new Date(until).toISOString()},source_health:snapshot.sources,generated_at:snapshot.generated_at,limitations};
}
