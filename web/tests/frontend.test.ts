import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import type { BriefingResponse, EventDetail, EventSummary, QueryResponse, SourceStatus } from "../lib/contracts";
import { changeQuery, DEFAULT_QUERY, interpretationToQuery, serializeQuery, timeWindowPatch } from "../lib/filters";
import { countryName, coverageWarnings, eventTime, formatEventDate, safeHttpUrl, sourceOverview } from "../lib/format";
import { eventsToGeoJson } from "../lib/map-data";
import { buildBackendUrl, isAllowedRoute, requestPolicyError } from "../lib/proxy-policy";
import SourcePanel from "../components/SourcePanel";
import Monitor from "../components/Monitor";
import PublicMonitor from "../components/PublicMonitor";
import EventEvidence, { shouldRevealEvidence } from "../components/EventEvidence";
import AnalysisPanel from "../components/AnalysisPanel";
import FilterPanel from "../components/FilterPanel";
import EventList from "../components/EventList";
import { mappedCategories } from "../components/EventMap";
import { assetPath } from "../lib/assets";
import { changePublicFilters, changePublicQuery, filterPublicSnapshot, loadPublicSnapshot, MAX_SNAPSHOT_BYTES, publicSourceCoverage, PUBLIC_DEFAULT_QUERY, PUBLIC_SOURCE_IDS, PUBLIC_SOURCE_INFO, PUBLIC_TIME_BASIS, selectPublicSource, snapshotAge, validatePublicSnapshot, type PublicSnapshot } from "../lib/public-snapshot";

function eventFixture(overrides:Partial<EventSummary>={}):EventSummary {
  return {
    id:"fixture:event",kind:"incident",category:"earthquake",title:"Rekord testowy",description:"Opis testowy.",
    occurred_start:"2026-08-27T10:00:00Z",occurred_end:null,issued_at:null,source_updated_at:null,
    first_seen_at:"2026-08-27T10:03:00Z",last_seen_at:"2026-08-27T10:05:00Z",last_changed_at:"2026-08-27T10:03:00Z",
    valid_from:null,valid_to:null,countries:[],geometry:null,location_precision:"unknown",time_precision:"second",
    severity:0,severity_label:"unknown",severity_reason:"",original_severity:null,lifecycle_status:"active",
    verification_status:"reported",anomaly_score:null,source_ids:["fixture"],source_count:1,independent_source_count:1,
    source_url:"https://example.invalid/fixture",tags:[],change_type:"initial_import",...overrides,
  };
}
function sourceFixture(overrides:Partial<SourceStatus>={}):SourceStatus {
  return {id:"fixture",name:"Źródło testowe",status:"pending",enabled:true,requires_key:false,last_attempt_at:null,last_success_at:null,newest_content_at:null,next_due_at:null,record_count:0,error:null,poll_interval_seconds:600,coverage:"Pokrycie testowe",license_name:"Licencja testowa",license_url:null,attribution:"Tylko fixture testowy",...overrides};
}

test("both editions use shared Sextet branding rather than a personal monitor name",()=>{
  for(const component of [Monitor,PublicMonitor]){
    const markup=renderToStaticMarkup(React.createElement(component));
    assert.match(markup,/Sextet Monitor/);
    assert.doesNotMatch(markup,/mieszko/i);
    assert.match(markup,component===Monitor ? /class="brand-mark"[^>]*>s<span/ : /aria-label="Sextet Monitor, przegląd"/);
  }
});

test("one serialization preserves a complete radius, temporal basis and independent-source filter",()=>{
  const query={...DEFAULT_QUERY,time_basis:"changed" as const,country:"PL",min_sources:2,lat:52.2297,lon:21.0122,radius_km:500};
  const params=new URLSearchParams(serializeQuery(query));
  assert.equal(params.get("min_sources"),"2");
  assert.equal(params.get("time_basis"),"changed");
  assert.equal(params.get("lat"),"52.2297");
  assert.equal(params.get("lon"),"21.0122");
  assert.equal(params.get("radius_km"),"500");
  assert.equal(params.has("category"),false);
});
test("changing the time window removes absolute bounds from a prior parsed query",()=>{
  const query=changeQuery({...DEFAULT_QUERY,since:"2026-08-26T00:00:00Z",until:"2026-08-27T00:00:00Z"},{window_hours:12});
  assert.equal(query.since,undefined);assert.equal(query.until,undefined);assert.equal(query.window_hours,12);
});
test("the confirmed flat query contract replaces old filters, accepts null optionals, and rejects malformed input",()=>{
  const interpreted=interpretationToQuery({window_hours:12,time_basis:"changed",country:"TR",category:"weather",region:null,lat:null,lon:null,radius_km:null,severity_min:2,min_sources:2});
  assert.equal(interpreted?.country,"TR");assert.equal(interpreted?.window_hours,12);assert.equal(interpreted?.category,"weather");
  for(const input of [{lat:52,radius_km:500},{window_hours:9999},{window_hours:1.5},{min_sources:0},{country:"Polska"},{category:"military"},{since:"2026-08-26T00:00:00"},{since:"2026-08-28T00:00:00Z",until:"2026-08-27T00:00:00Z"}]){
    assert.equal(interpretationToQuery(input),null);
  }
});
test("unknown coordinates never become map points; polygons stay polygons",()=>{
  const polygon={type:"Polygon" as const,coordinates:[[[20,50],[22,50],[22,53],[20,50]]]};
  const data=eventsToGeoJson([
    eventFixture(),
    eventFixture({id:"polygon",geometry:polygon,location_precision:"country"}),
    eventFixture({id:"point",geometry:{type:"Point",coordinates:[21,52]},location_precision:"point"}),
    eventFixture({id:"invalid",geometry:{type:"Point",coordinates:[NaN,52]}}),
  ]);
  assert.equal(data.points.features.length,1);
  assert.deepEqual(data.points.features[0].geometry.coordinates,[21,52]);
  assert.equal(data.areas.features.length,1);
  assert.deepEqual(data.areas.features[0].geometry,polygon);
});
test("publication and retrieval do not invent an occurrence date for a KEV entry",()=>{
  const event=eventFixture({kind:"vulnerability_notice",category:"cyber",occurred_start:null,issued_at:"2026-08-26T00:00:00Z",time_precision:"day",tags:["date_only_utc_anchor"]});
  assert.equal(eventTime(event,"occurred"),null);
  assert.equal(eventTime(event,"changed"),"2026-08-27T10:03:00Z");
  assert.equal(formatEventDate(event,"occurred_start"),"Nie ustalono");
  assert.match(formatEventDate(event,"issued_at"),/26.08.2026/);
  assert.doesNotMatch(formatEventDate(event,"issued_at"),/00:00|02:00/);
});
test("exclusive daily advisory expiry displays the preceding source day, including a year boundary",()=>{
  const event=eventFixture({valid_to:"2027-01-01T00:00:00Z",time_precision:"day",tags:["valid_to_exclusive_day_boundary"]});
  assert.equal(formatEventDate(event,"valid_to"),"31.12.2026 (koniec dnia)");
});
test("links reject executable schemes and credential-bearing URLs",()=>{
  for(const value of ["javascript:alert(1)","data:text/html,test","file:///etc/passwd","https://user:secret@example.invalid"]){
    assert.equal(safeHttpUrl(value),null);
  }
  assert.equal(safeHttpUrl("https://example.invalid/source"),"https://example.invalid/source");
});
test("a successful empty source stays distinct from disabled, error and pending",()=>{
  const sources=[sourceFixture(),sourceFixture({id:"empty",status:"ok_empty",last_success_at:"2026-08-27T10:00:00Z"}),sourceFixture({id:"off",status:"disabled",enabled:false})];
  assert.deepEqual(coverageWarnings(sources).map((source)=>source.id),["fixture"]);
  const html=renderToStaticMarkup(React.createElement(SourcePanel,{sources,loading:false,error:null,onRetry:()=>undefined}));
  assert.match(html,/Oczekuje/);assert.match(html,/Pobrano, bez rekordów/);assert.match(html,/Wyłączone/);
  assert.match(html,/To nie oznacza braku zagrożeń/);
});
test("evidence explains imported history and country geometry without trusting source HTML",()=>{
  const detail:EventDetail={...eventFixture({description:"<script>alert(1)</script>",source_url:"javascript:alert(1)",tags:["country_geometry_not_fir"]}),evidence:[],revisions:[],relations:[]};
  const html=renderToStaticMarkup(React.createElement(EventEvidence,{detail,selected:true,loading:false,error:null,outsideFilter:false,onSelect:()=>undefined,onRetry:()=>undefined}));
  assert.match(html,/Import początkowy/);assert.match(html,/Nie przedstawia granic FIR/);
  assert.match(html,/&lt;script&gt;/);assert.doesNotMatch(html,/<script|href="javascript:/);
});
test("the proxy only admits known read routes and two explicit JSON POSTs",()=>{
  assert.equal(isAllowedRoute("GET",["events","usgs:123"]),true);
  assert.equal(isAllowedRoute("POST",["query"]),true);
  for(const [method,path] of [["GET",["..","secrets"]],["POST",["events"]],["DELETE",["events","id"]],["GET",["fetch"]],["GET",["events","https://example.invalid"]]] as const){
    assert.equal(isAllowedRoute(method,[...path]),false);
  }
});
test("public-origin POST protection works behind the container port and rejects browser cross-site requests",()=>{
  const url=new URL("http://0.0.0.0:3000/api/query");
  const publicOrigin="http://localhost:3180";
  const headers=new Headers({host:"localhost:3180",origin:publicOrigin,"content-type":"application/json","x-monitor-request":"1"});
  assert.equal(requestPolicyError("POST",url,headers),null);
  headers.delete("x-monitor-request");assert.match(requestPolicyError("POST",url,headers) || "",/nagłówka/);
  headers.set("x-monitor-request","1");headers.set("origin","https://untrusted.invalid");assert.match(requestPolicyError("POST",url,headers) || "",/obcej/);
  headers.set("origin",publicOrigin);headers.set("host","rebind.invalid:3180");assert.match(requestPolicyError("POST",url,headers) || "",/lokalnie/);
});
test("browser query parameters cannot choose or redirect the upstream server",()=>{
  const result=buildBackendUrl("http://api:8000",["events","usgs:123"],new URLSearchParams());
  assert.equal(result.href,"http://api:8000/api/events/usgs%3A123");
  assert.throws(()=>buildBackendUrl("http://api:8000",["events"],new URLSearchParams("url=https://untrusted.invalid")));
  assert.throws(()=>buildBackendUrl("http://api:8000",["events"],new URLSearchParams("country=PL&country=TR")));
  assert.throws(()=>buildBackendUrl("http://secret:password@api:8000",["health"],new URLSearchParams()));
});

test("zero configured or enabled sources never becomes a healthy overview",()=>{
  assert.equal(sourceOverview([]).state,"unknown");
  assert.equal(sourceOverview([sourceFixture({enabled:false,status:"disabled"})]).state,"disabled");
  assert.equal(sourceOverview([sourceFixture({status:"error"})]).state,"partial");
  assert.deepEqual(sourceOverview([sourceFixture({status:"ok_empty"})]),{active:1,responding:1,state:"ok"});
});

test("query ISO dates accept positive, negative and zero UTC offsets and preserve them through the URL",()=>{
  for(const offset of ["+02:00","-05:30","+00:00","-00:00"]){
    const since="2026-08-26T12:00:00"+offset;
    const until="2026-08-26T13:00:00"+offset;
    const query=interpretationToQuery({since,until});
    assert.ok(query,offset);
    assert.equal(query.since,since);
    assert.equal(query.until,until);
    const roundtrip=new URLSearchParams(serializeQuery(query));
    assert.equal(roundtrip.get("since"),since);
    assert.equal(roundtrip.get("until"),until);
  }
});
test("absolute query ordering compares instants across offsets rather than local clock strings",()=>{
  assert.ok(interpretationToQuery({since:"2026-08-26T12:00:00+02:00",until:"2026-08-26T11:00:00Z"}));
  assert.equal(interpretationToQuery({since:"2026-08-26T12:00:00-02:00",until:"2026-08-26T13:00:00Z"}),null);
  assert.equal(interpretationToQuery({since:"2026-08-26T12:00:00+25:00"}),null);
});
test("briefing scope visibly names the country and filters that do not apply",()=>{
  const html=renderToStaticMarkup(React.createElement(AnalysisPanel,{query:null,briefing:null,latestKnown:true,loading:false,briefLoading:false,error:null,onSelect:()=>undefined,briefingCountry:"PL"}));
  assert.match(html,/Polska/);
  assert.match(html,/Zmiany od poprzedniego briefingu dla tego obszaru/);
  assert.match(html,/Pierwszy briefing obejmuje ostatnie 24 godziny/);
  assert.match(html,/Pomija bieżące filtry kategorii, promienia, regionu, wagi i liczby źródeł/);
});

test("public Host and Origin must match exactly; forwarded Host cannot authorize a POST",()=>{
  const url=new URL("http://0.0.0.0:3000/api/query");
  const headers=new Headers({host:"127.0.0.1:3180",origin:"http://127.0.0.1:3180","content-type":"application/json","x-monitor-request":"1"});
  assert.equal(requestPolicyError("POST",url,headers),null);
  for(const origin of ["http://localhost:3180","http://127.0.0.1:3000","https://untrusted.invalid","null"]){
    headers.set("origin",origin);
    assert.match(requestPolicyError("POST",url,headers) || "",/obcej/);
  }
  headers.set("origin","http://localhost:3180");
  headers.set("host","localhost:3000");
  headers.set("x-forwarded-host","localhost:3180");
  assert.notEqual(requestPolicyError("POST",url,headers),null);
  headers.delete("origin");
  assert.notEqual(requestPolicyError("POST",url,headers),null);
  headers.set("host","localhost:3180");
  assert.equal(requestPolicyError("POST",url,headers),null);
  headers.set("sec-fetch-site","cross-site");
  assert.notEqual(requestPolicyError("POST",url,headers),null);
  headers.delete("sec-fetch-site");headers.delete("host");
  assert.notEqual(requestPolicyError("POST",url,headers),null);
});
test("origin-less local GET remains available to a container health check",()=>{
  const url=new URL("http://0.0.0.0:3000/api/health");
  assert.equal(requestPolicyError("GET",url,new Headers({host:"127.0.0.1:3000"})),null);
  assert.equal(requestPolicyError("GET",url,new Headers({host:"localhost:3180"})),null);
  assert.notEqual(requestPolicyError("GET",url,new Headers({host:"localhost:3180",origin:"https://untrusted.invalid"})),null);
});
test("country option markup is independent of runtime Intl.DisplayNames data",()=>{
  const render=()=>renderToStaticMarkup(React.createElement(FilterPanel,{query:DEFAULT_QUERY,onChange:()=>undefined,onReset:()=>undefined}));
  const before=render();
  const original=Object.getOwnPropertyDescriptor(Intl.DisplayNames.prototype,"of")!;
  try {
    Object.defineProperty(Intl.DisplayNames.prototype,"of",{...original,value:()=> "Different ICU label"});
    assert.equal(render(),before);
    assert.equal(countryName("TR"),"Turcja");
    assert.equal(countryName("PL"),"Polska");
    assert.equal(countryName("ZZ"),"ZZ");
    assert.match(before,/Turcja \(TR\)/);
  } finally {
    Object.defineProperty(Intl.DisplayNames.prototype,"of",original);
  }
});
test("served main module, worker and relative dependency match the pinned MapLibre package byte for byte",()=>{
  const packageRoot=dirname(createRequire(import.meta.url).resolve("maplibre-gl/package.json"));
  for(const [source,target] of [
    ["dist/maplibre-gl.mjs","maplibre-gl.mjs"],
    ["dist/maplibre-gl-worker.mjs","maplibre-gl-worker.mjs"],
    ["dist/maplibre-gl-shared.mjs","maplibre-gl-shared.mjs"],
    ["LICENSE.txt","LICENSE.txt"],
  ]){
    const served=readFileSync(new URL("../public/maplibre/"+target,import.meta.url));
    assert.deepEqual(served,readFileSync(join(packageRoot,source)),target);
  }
  const worker=readFileSync(new URL("../public/maplibre/maplibre-gl-worker.mjs",import.meta.url),"utf8");
  assert.match(worker,/from"\.\/maplibre-gl-shared\.mjs"/);
});

test("moving the window end keeps its duration and every non-time filter on one UTC query",()=>{
  const now=Date.parse("2026-08-27T12:00:00Z");
  const original={...DEFAULT_QUERY,country:"PL",category:"weather" as const,min_sources:2,lat:52.2297,lon:21.0122,radius_km:500};
  const shifted=changeQuery(original,timeWindowPatch(original,6,now));
  assert.equal(shifted.until,"2026-08-27T06:00:00.000Z");
  assert.equal(shifted.since,"2026-08-26T06:00:00.000Z");
  assert.equal(shifted.country,"PL");assert.equal(shifted.radius_km,500);assert.equal(shifted.min_sources,2);assert.equal(shifted.category,"weather");
  const parameters=new URLSearchParams(serializeQuery(shifted));
  assert.equal(parameters.get("until"),shifted.until);
  assert.deepEqual(changeQuery(shifted,timeWindowPatch(shifted,0,now)),{...original,since:undefined,until:undefined});
});
test("time movement preserves an explicit window width across UTC offsets and validates stepper bounds",()=>{
  const now=Date.parse("2026-08-27T12:00:00Z");
  const query={...DEFAULT_QUERY,since:"2026-08-26T08:00:00+02:00",until:"2026-08-26T10:30:00+01:00"};
  const patch=timeWindowPatch(query,6,now);
  assert.equal(patch.until,"2026-08-27T06:00:00.000Z");
  assert.equal(patch.since,"2026-08-27T02:30:00.000Z");
  assert.equal(timeWindowPatch(DEFAULT_QUERY,168,now).until,"2026-08-20T12:00:00.000Z");
  for(const offset of [-1,169,0.5,NaN])assert.throws(()=>timeWindowPatch(query,offset,now),RangeError);
});
test("time stepper has a visible label and absolute ranges show Warsaw timestamps without inventing an offset",()=>{
  const query={...DEFAULT_QUERY,since:"2026-08-27T00:00:00Z",until:"2026-08-27T12:00:00Z"};
  const html=renderToStaticMarkup(React.createElement(FilterPanel,{query,onChange:()=>undefined,onReset:()=>undefined}));
  assert.match(html,/for="time-offset">Koniec okna/);
  assert.match(html,/min="0" max="168" step="1"/);
  assert.match(html,/Ustalony czas/);assert.match(html,/Europe\/Warsaw/);
  assert.match(html,/27.08.2026, 14:00/);
});

test("evidence navigation waits for the requested detail and cannot be triggered by a background update",()=>{
  assert.equal(shouldRevealEvidence("new","new","old",false),false);
  assert.equal(shouldRevealEvidence("new","new",null,true),false);
  assert.equal(shouldRevealEvidence("new","new","new",true),false);
  assert.equal(shouldRevealEvidence("new","new","new",false),true);
  assert.equal(shouldRevealEvidence(null,"new","new",false),false);
  assert.equal(shouldRevealEvidence("old","new","new",false),false);
});
test("disabled and token-blocked sources do not promise an attempt using an old due timestamp",()=>{
  const render=(source:SourceStatus)=>renderToStaticMarkup(React.createElement(SourcePanel,{sources:[source],loading:false,error:null,onRetry:()=>undefined}));
  const due="2026-08-27T10:06:00Z";
  const disabled=render(sourceFixture({enabled:false,status:"needs_credentials",next_due_at:due}));
  assert.match(disabled,/Nie zaplanowano \(źródło wyłączone\)/);
  assert.doesNotMatch(disabled,/12:06/);
  const credentials=render(sourceFixture({enabled:true,status:"needs_credentials",next_due_at:due}));
  assert.match(credentials,/Nie zaplanowano \(brak tokenu\)/);
  const active=render(sourceFixture({enabled:true,status:"ok",next_due_at:due}));
  assert.match(active,/12:06/);assert.doesNotMatch(active,/Nie zaplanowano/);
});
test("analysis omits a repeated explanation but keeps additional information",()=>{
  const explanation="Monitor nie ma danych GNSS.";
  const query:QueryResponse={supported:false,answer:explanation,query_explanation:explanation,interpretation:null,events:[],facts:[],inferences:[],limitations:[],source_health:[],generated_at:"2026-08-27T12:00:00Z"};
  const render=(value:QueryResponse)=>renderToStaticMarkup(React.createElement(AnalysisPanel,{query:value,briefing:null,latestKnown:true,loading:false,briefLoading:false,error:null,onSelect:()=>undefined}));
  const duplicate=render(query);
  assert.equal(duplicate.split(explanation).length-1,1);
  const contained=render({...query,supported:true,answer:"Wynik: "+explanation+" Sprawdź pokrycie źródeł."});
  assert.equal(contained.split(explanation).length-1,1);
  const distinct=render({...query,query_explanation:"Zastosowano zakres ostatnich 12 godzin."});
  assert.match(distinct,/Zastosowano zakres ostatnich 12 godzin\./);
  assert.equal(distinct.split(explanation).length-1,1);
});


test("switching the time basis keeps historical bounds and non-time filters",()=>{
  const original={...DEFAULT_QUERY,country:"PL",radius_km:800,lat:52.2297,lon:21.0122,since:"2026-08-31T08:00:00+02:00",until:"2026-08-31T20:00:00+02:00"};
  for(const time_basis of ["changed","published","validity"] as const){
    const changed=changeQuery(original,{time_basis});
    assert.equal(changed.since,original.since);assert.equal(changed.until,original.until);
    assert.equal(changed.country,"PL");assert.equal(changed.radius_km,800);
    const serialized=new URLSearchParams(serializeQuery(changed));
    assert.equal(serialized.get("time_basis"),time_basis);assert.equal(serialized.get("until"),original.until);
  }
});
test("publication and validity interpretations keep source clocks without invented incident times",()=>{
  const event=eventFixture({kind:"advisory",category:"aviation",occurred_start:null,issued_at:"2026-08-01T00:00:00Z",valid_from:"2026-08-02T00:00:00Z",valid_to:null,time_precision:"day"});
  for(const time_basis of ["published","validity"] as const){
    const query=interpretationToQuery({time_basis,include_inactive:true,window_hours:24});
    assert.ok(query);assert.equal(query.time_basis,time_basis);assert.equal(query.include_inactive,true);
  }
  assert.equal(eventTime(event,"occurred"),null);
  assert.equal(eventTime(event,"published"),event.issued_at);
  assert.equal(eventTime(event,"validity"),event.valid_from);
  assert.equal(eventTime({...event,valid_from:null},"validity"),null);
  for(const [basis,label,date] of [["published","Publikacja","01.08.2026"],["validity","Ważne od","02.08.2026"]] as const){
    const html=renderToStaticMarkup(React.createElement(EventList,{events:[event],selectedId:null,basis,mode:"list",loading:false,error:null,sources:[],onSelect:()=>undefined,onRetry:()=>undefined}));
    assert.match(html,new RegExp(label));assert.ok(html.includes(date));
    assert.doesNotMatch(html,/00:00|02:00/);
  }
});
test("validity controls distinguish source validity from a reconstructed historical state",()=>{
  const html=renderToStaticMarkup(React.createElement(FilterPanel,{query:{...DEFAULT_QUERY,time_basis:"validity",include_inactive:true},onChange:()=>undefined,onReset:()=>undefined}));
  assert.match(html,/Daty publikacji/);assert.match(html,/Okresu ważności/);
  assert.match(html,/Status jest bieżący; to nie odtworzony stan historyczny/);
  assert.match(html,/Brak końca ważności pozostaje nieznany/);
});
test("the map legend only names categories with valid rendered geometry",()=>{
  const events=[eventFixture({category:"cyber"}),eventFixture({id:"invalid",category:"weather",geometry:{type:"Point",coordinates:[NaN,10]}}),eventFixture({id:"located",geometry:{type:"Point",coordinates:[21,52]}})];
  assert.deepEqual(mappedCategories(events),["earthquake"]);
  assert.deepEqual(mappedCategories([eventFixture({geometry:null})]),[]);
});
test("background evidence refresh retains the article and marks previous data honestly",()=>{
  const detail:EventDetail={...eventFixture({title:"Śledzone ostrzeżenie"}),evidence:[],revisions:[],relations:[]};
  const render=(loading:boolean,error:string|null)=>renderToStaticMarkup(React.createElement(EventEvidence,{detail,selected:true,readAt:"2026-09-01T07:00:00Z",loading,error,outsideFilter:false,onSelect:()=>undefined,onRetry:()=>undefined}));
  const refreshing=render(true,null);
  assert.match(refreshing,/<article[^>]*data-event-id="fixture:event"[^>]*aria-busy="true"/);
  assert.match(refreshing,/Śledzone ostrzeżenie/);assert.match(refreshing,/Poniżej poprzedni odczyt/);
  assert.doesNotMatch(refreshing,/skeleton-block/);
  const failed=render(false,"Brak połączenia");
  assert.match(failed,/Śledzone ostrzeżenie/);assert.match(failed,/Nie potwierdzono aktualności dowodów/);
  assert.match(failed,/Ponów odczyt dowodów/);assert.match(failed,/01.09.2026, 09:00/);
});
test("saved briefing keeps its own country and actual period when filters change",()=>{
  const briefing:BriefingResponse={id:"briefing-fixture",answer:"Zapisana odpowiedź",since:"2026-08-31T08:00:00Z",until:"2026-09-01T07:00:00Z",generated_at:"2026-09-01T07:00:00Z",sections:[],facts:[],limitations:[],source_health:[],scope:{window_hours:24,country:"PL"},processed_count:12,omitted_fact_count:12};
  const render=(value:BriefingResponse)=>renderToStaticMarkup(React.createElement(AnalysisPanel,{query:null,briefing:value,latestKnown:true,loading:false,briefLoading:false,error:null,onSelect:()=>undefined,briefingCountry:"TR"}));
  const html=render(briefing);
  assert.match(html,/Zakres następnego briefingu: Turcja/);
  assert.match(html,/Zakres zapisanego briefingu: Polska/);
  assert.match(html,/31.08.2026, 10:00/);assert.match(html,/01.09.2026, 09:00/);
  assert.match(html,/Przetworzono 12 rekordów, opisano 0/);
  const legacy=render({...briefing,scope:undefined,processed_count:undefined,omitted_fact_count:undefined});
  assert.match(legacy,/Obszar nie został zapisany w tym briefingu/);assert.doesNotMatch(legacy,/Przetworzono/);
});

test("a catalog snapshot timestamp is not rendered with the vulnerability publication day precision",()=>{
  const detail:EventDetail={...eventFixture({kind:"vulnerability_notice",category:"cyber",occurred_start:null,time_precision:"day",issued_at:"2026-08-26T00:00:00Z"}),revisions:[],relations:[],evidence:[{id:"source-observation",source_id:"cisa_kev",source_name:"CISA KEV",provider_record_id:"CVE-FIXTURE",source_url:"https://example.invalid/cisa",retrieved_at:"2026-08-26T11:00:00Z",issued_at:"2026-08-26T00:00:00Z",source_updated_at:null,source_snapshot_at:"2026-08-26T10:12:00Z",origins:["cisa"],payload_hash:"test",raw:{fixture:true},attribution:"Test fixture",license_url:null}]};
  const html=renderToStaticMarkup(React.createElement(EventEvidence,{detail,selected:true,loading:false,error:null,outsideFilter:false,onSelect:()=>undefined,onRetry:()=>undefined}));
  assert.match(html,/Snapshot katalogu u źródła/);assert.match(html,/26.08.2026, 12:12/);
  assert.match(html,/26.08.2026 \(data źródłowa\)/);
});


// These deliberately synthetic fixtures exist only in tests; they are never exported to public/.
function publicFixture(overrides:Partial<EventDetail>={}):PublicSnapshot {
  const detail:EventDetail={...eventFixture({id:"11111111-1111-5111-8111-111111111111",source_ids:["usgs"],...overrides}),
    evidence:[{id:"22222222-2222-5222-8222-222222222222",source_id:"usgs",source_name:"USGS fixture",provider_record_id:"test-only",source_url:"https://example.invalid/record",retrieved_at:"2026-08-27T11:00:00Z",issued_at:null,source_updated_at:null,origins:["USGS"],payload_hash:"test-only",raw:null,raw_retained:false,attribution:"Fixture, not published",license_url:null}],revisions:[],relations:[],...overrides};
  return {format:1,version:"test-only",generated_at:"2026-08-27T12:00:00Z",events:[detail],sources:[sourceFixture({id:"usgs",status:"ok",record_count:1,last_success_at:"2026-08-27T11:00:00Z"})],limitations:["Fixture, not published"]};
}
test("public manifest accepts only explicit public sources and excludes raw payloads and private history",()=>{
  assert.equal(validatePublicSnapshot(publicFixture()).events.length,1);
  const collection=publicFixture({geometry:{type:"GeometryCollection",geometries:[{type:"Point",coordinates:[21,52]}]}});
  assert.equal(validatePublicSnapshot(collection).events[0].geometry?.type,"GeometryCollection");
  const mutations:Array<(value:PublicSnapshot)=>void>=[
    (value)=>{value.format=2 as 1;},
    (value)=>{value.sources[0].id="cloudflare_radar";},
    (value)=>{value.sources[0].requires_key=true;},
    (value)=>{value.events[0].evidence[0].raw={private:"payload"};},
    (value)=>{delete value.events[0].evidence[0].raw_retained;},
    (value)=>{value.events[0].revisions=[{id:"private",recorded_at:"2026-08-27T11:00:00Z",change_type:"new",summary:"Private history"}];},
    (value)=>{value.events.push(structuredClone(value.events[0]));},
    (value)=>{value.events[0].source_url="http://localhost:8000/private";},
    (value)=>{value.events[0].source_url="javascript:alert(1)";},
    (value)=>{value.events[0].relations=[{event_id:"outside",title:"Outside",relation_type:"same_event",reason:"Test",distance_km:null,time_delta_hours:null}];},
    (value)=>{value.events[0].geometry={type:"Point",coordinates:[181,52]};},
    (value)=>{value.events[0].geometry={type:"GeometryCollection",geometries:[]};},
    (value)=>{value.events[0].geometry=Object.assign({type:"Point" as const,coordinates:[21,52]},{raw:{private:"payload"}});},
    (value)=>{value.events[0].geometry=Object.assign({type:"GeometryCollection" as const,geometries:[{type:"Point" as const,coordinates:[21,52]}]},{raw:{private:"payload"}});},
    (value)=>{value.events[0].geometry={type:"GeometryCollection",geometries:[Object.assign({type:"Point" as const,coordinates:[21,52]},{private_field:"payload"})]};},
  ];
  for(const mutate of mutations){const fixture=publicFixture();mutate(fixture);assert.throws(()=>validatePublicSnapshot(fixture),/Nieprawidłowy zestaw/);}
  assert.throws(()=>validatePublicSnapshot({...publicFixture(),private_history:[]}),/pola manifestu/);
});
test("public dates must be valid aware timestamps, with no guessed future preparation time",()=>{
  for(const value of ["2026-02-30T12:00:00Z","2026-08-27T12:00:00","2026-08-27","not a date"]){
    assert.throws(()=>validatePublicSnapshot({...publicFixture(),generated_at:value}),/czas przygotowania/);
  }
  assert.throws(()=>validatePublicSnapshot(publicFixture(),Date.parse("2026-08-27T11:54:59Z")),/przyszły/);
  assert.equal(validatePublicSnapshot(publicFixture(),Date.parse("2026-08-27T12:00:00Z")).format,1);
});
test("public occurrence windows are half-open and do not substitute retrieval for unknown occurrence",()=>{
  const snapshot=publicFixture();
  const query={...PUBLIC_DEFAULT_QUERY,window_hours:2};
  assert.equal(filterPublicSnapshot(snapshot,query).total,1);
  snapshot.events[0].occurred_start=snapshot.generated_at;
  assert.equal(filterPublicSnapshot(snapshot,query).total,0);
  snapshot.events[0].occurred_start=null;
  assert.equal(filterPublicSnapshot(snapshot,query).total,0);
});
test("public daily publication overlaps the source day, without inventing an attack time",()=>{
  const snapshot=publicFixture({category:"cyber",kind:"vulnerability_notice",occurred_start:null,issued_at:"2026-08-27T00:00:00Z",time_precision:"day",tags:["date_only_utc_anchor"]});
  const query={...PUBLIC_DEFAULT_QUERY,time_basis:"published" as const,window_hours:1};
  assert.equal(filterPublicSnapshot(snapshot,query).total,1);
  snapshot.events[0].tags=[];
  assert.equal(filterPublicSnapshot(snapshot,query).total,0);
  snapshot.events[0].issued_at=null;
  assert.equal(filterPublicSnapshot(snapshot,query).total,0);
});
test("public validity matches interval overlap and uses status at preparation, never today's clock",()=>{
  const snapshot=publicFixture({category:"weather",kind:"advisory",occurred_start:null,valid_from:"2026-08-20T00:00:00Z",valid_to:"2026-08-28T00:00:00Z"});
  const query={...PUBLIC_DEFAULT_QUERY,time_basis:"validity" as const,window_hours:1,include_inactive:false};
  assert.equal(filterPublicSnapshot(snapshot,query).total,1);
  snapshot.events[0].valid_to=null;
  assert.equal(filterPublicSnapshot(snapshot,query).total,1);
  snapshot.events[0].valid_to="2026-08-27T11:00:00Z";
  assert.equal(filterPublicSnapshot(snapshot,{...query,include_inactive:true}).total,0);
  snapshot.events[0].valid_to="2026-08-27T11:30:00Z";
  assert.equal(filterPublicSnapshot(snapshot,query).total,0);
  assert.equal(filterPublicSnapshot(snapshot,{...query,include_inactive:true}).total,1);
  snapshot.events[0].valid_from=null;
  assert.equal(filterPublicSnapshot(snapshot,{...query,include_inactive:true}).total,0);
});
test("public category and country filters keep one sorted bounded map/list result and reject server-only filters",()=>{
  const snapshot=publicFixture({countries:["PL"],severity:3,geometry:{type:"Point",coordinates:[21,52]}});
  snapshot.events.push({...snapshot.events[0],id:"33333333-3333-5333-8333-333333333333",severity:1,geometry:null});
  const response=filterPublicSnapshot(snapshot,{...PUBLIC_DEFAULT_QUERY,country:"PL",limit:1});
  assert.equal(response.total,2);assert.equal(response.shown,1);assert.equal(response.mapped,1);assert.equal(response.unlocated,0);assert.equal(response.truncated,true);
  assert.equal(filterPublicSnapshot(snapshot,{...PUBLIC_DEFAULT_QUERY,severity_min:2}).total,1);
  assert.equal(filterPublicSnapshot(snapshot,{...PUBLIC_DEFAULT_QUERY,country:"TR"}).total,0);
  for(const patch of [{time_basis:"changed" as const},{radius_km:500},{region:"europe" as const}])assert.throws(()=>filterPublicSnapshot(snapshot,{...PUBLIC_DEFAULT_QUERY,...patch}),/wymaga prywatnego/);
  const anchored={...PUBLIC_DEFAULT_QUERY,since:"2026-08-26T01:00:00Z",until:"2026-08-26T02:00:00Z"};
  const next=changePublicQuery(anchored,{category:"cyber"});
  assert.equal(next.time_basis,"published");assert.equal(next.since,anchored.since);assert.equal(next.until,anchored.until);
  assert.equal(changePublicQuery(next,{category:"weather"}).time_basis,"validity");
});
test("public assets stay inside the declared Pages path and age is honest for old datasets",()=>{
  assert.equal(assetPath("/snapshot.json","/sextet-monitor"),"/sextet-monitor/snapshot.json");
  assert.equal(assetPath("/maplibre/maplibre-gl-worker.mjs",""),"/maplibre/maplibre-gl-worker.mjs");
  for(const path of ["https://example.invalid/snapshot.json","//example.invalid/snapshot.json","/../snapshot.json"]){assert.throws(()=>assetPath(path,"/sextet-monitor"));}
  assert.throws(()=>assetPath("/snapshot.json","/../private"));
  assert.match(snapshotAge("2026-08-27T12:00:00Z",Date.parse("2026-08-30T12:00:00Z")),/3 dni/);
  assert.match(snapshotAge("2026-08-27T12:00:00Z",Date.parse("2026-08-27T11:00:00Z")),/zegar/);
});
test("snapshot fetch is same-origin JSON only, credentialless and bounded before parsing",async()=>{
  let requestUrl="",requestInit:RequestInit|undefined;
  const fetcher:typeof fetch=async(input,init)=>{requestUrl=String(input);requestInit=init;return new Response(JSON.stringify(publicFixture()),{headers:{"Content-Type":"application/json"}});};
  assert.equal((await loadPublicSnapshot(undefined,fetcher)).format,1);
  assert.match(requestUrl,/^\/(?:[A-Za-z0-9_-]+\/)*snapshot.json$/);assert.doesNotMatch(requestUrl,/localhost|api/);
  assert.equal(requestInit?.credentials,"omit");assert.equal(requestInit?.redirect,"error");assert.equal(requestInit?.cache,"no-store");
  const responseFetcher=(response:Response):typeof fetch=>async()=>response;
  await assert.rejects(loadPublicSnapshot(undefined,responseFetcher(new Response("",{status:404}))),/jeszcze opublikowany/);
  await assert.rejects(loadPublicSnapshot(undefined,responseFetcher(new Response("<html>",{headers:{"Content-Type":"text/html"}}))),/JSON/);
  await assert.rejects(loadPublicSnapshot(undefined,responseFetcher(new Response("{}",{headers:{"Content-Type":"application/json","Content-Length":String(MAX_SNAPSHOT_BYTES+1)}}))),/16 MiB/);
  const oversized=new ReadableStream<Uint8Array>({start(controller){controller.enqueue(new Uint8Array(MAX_SNAPSHOT_BYTES+1));controller.close();}});
  await assert.rejects(loadPublicSnapshot(undefined,responseFetcher(new Response(oversized,{headers:{"Content-Type":"application/json"}}))),/16 MiB/);
});
test("public shared components omit unavailable controls, local history and worker promises",()=>{
  const snapshot=publicFixture();
  const filters=renderToStaticMarkup(React.createElement(FilterPanel,{query:PUBLIC_DEFAULT_QUERY,onChange:()=>undefined,onReset:()=>undefined,snapshot:{generatedAt:snapshot.generated_at,countries:["PL"]}}));
  assert.doesNotMatch(filters,/Filtr promienia|id="region"|value="changed"/);
  assert.match(filters,/Koniec zestawu/);assert.match(filters,/27.08.2026/);
  const detail=renderToStaticMarkup(React.createElement(EventEvidence,{detail:snapshot.events[0],publicMode:true,readAt:snapshot.generated_at,selected:true,loading:false,error:null,outsideFilter:false,onSelect:()=>undefined,onRetry:()=>undefined}));
  assert.doesNotMatch(detail,/Surowy rekord źródłowy|Historia zmian|Import początkowy|Ostatnia zmiana w monitorze/);
  assert.match(detail,/przetworzone pola/);assert.match(detail,/nie są publikowane/);
  const sources=renderToStaticMarkup(React.createElement(SourcePanel,{sources:snapshot.sources,snapshotAt:snapshot.generated_at,loading:false,error:null,onRetry:()=>undefined}));
  assert.match(sources,/Źródła zestawu/);assert.doesNotMatch(sources,/Następne pobranie|Interwał|>Działa</);
});

test("public schema rejects unpublished extra fields on sources, events, evidence and relations",()=>{
  const accessors:Array<(value:PublicSnapshot)=>object>=[
    (value)=>value.sources[0],
    (value)=>value.events[0],
    (value)=>value.events[0].evidence[0],
    (value)=>{value.events[0].relations=[{event_id:value.events[0].id,title:"Test only",relation_type:"same_event",reason:"Fixture",distance_km:null,time_delta_hours:null}];return value.events[0].relations[0];},
  ];
  for(const accessor of accessors){
    const snapshot=publicFixture();
    const target=accessor(snapshot) as Record<string,unknown>;
    target.private_field="must never be included in the public artifact";
    assert.throws(()=>validatePublicSnapshot(snapshot),/nieoczekiwane pola/);
  }
});


function nineSourceFixture():PublicSnapshot {
  const snapshot=publicFixture();
  const template=snapshot.events[0];
  snapshot.sources=PUBLIC_SOURCE_IDS.map((id)=>sourceFixture({id,name:PUBLIC_SOURCE_INFO[id].name,status:"ok",record_count:1,last_attempt_at:"2026-08-27T11:00:00Z",last_success_at:"2026-08-27T11:00:00Z"}));
  snapshot.events=PUBLIC_SOURCE_IDS.map((id,index)=>({
    ...structuredClone(template),id:`0000000${index+1}-1111-5111-8111-111111111111`,category:PUBLIC_SOURCE_INFO[id].categories[0],source_ids:[id],
    issued_at:"2026-08-27T10:00:00Z",valid_from:"2026-08-27T10:00:00Z",
    evidence:[{...structuredClone(template.evidence[0]),source_id:id,source_name:PUBLIC_SOURCE_INFO[id].name,origins:[`fixture:${id}`]}],
  }));
  return snapshot;
}

test("the public schema admits all nine approved feeds, seven categories and honest failed-source metadata",()=>{
  const snapshot=nineSourceFixture();
  const failed=snapshot.sources.find((source)=>source.id==="noaa_swpc")!;
  failed.status="error";failed.error="Latest fetch failed; retaining the earlier public reading.";
  failed.last_success_at="2026-08-26T11:00:00Z";
  const retained=snapshot.events.find((event)=>event.source_ids.includes(failed.id))!;
  retained.tags.push("cached_public_data");retained.evidence[0].retrieved_at=failed.last_success_at;
  const parsed=validatePublicSnapshot(snapshot);
  assert.equal(parsed.sources.length,9);
  assert.equal(new Set(parsed.events.map((event)=>event.category)).size,7);
  assert.equal(parsed.sources.find((source)=>source.id===failed.id)?.status,"error");
  assert.equal(parsed.events.find((event)=>event.id===retained.id)?.evidence[0].retrieved_at,"2026-08-26T11:00:00Z");
  // A failed source without a prior public reading is still represented, with no invented record.
  const missing=snapshot.sources.find((source)=>source.id==="cloudflare_status")!;
  missing.status="error";missing.error="No public data available.";missing.last_success_at=null;missing.record_count=0;
  snapshot.events=snapshot.events.filter((event)=>!event.source_ids.includes(missing.id));
  assert.equal(validatePublicSnapshot(snapshot).sources.find((source)=>source.id===missing.id)?.record_count,0);
  snapshot.sources.push(sourceFixture({id:"cloudflare_radar"}));
  assert.throws(()=>validatePublicSnapshot(snapshot),/Nieprawidłowy zestaw/);
});

test("public source selection chooses a source time basis without leaking source state into private queries",()=>{
  const query={...PUBLIC_DEFAULT_QUERY,country:"PL",severity_min:2,since:"2026-08-26T06:00:00Z",until:"2026-08-27T06:00:00Z"};
  const expected={usgs:"occurred",meteoalarm:"validity",cisa_kev:"published",gdacs:"occurred",easa_czib:"validity",nasa_eonet:"occurred",noaa_swpc:"published",github_status:"published",cloudflare_status:"published"};
  for(const id of PUBLIC_SOURCE_IDS){
    const selected=selectPublicSource({query},id);
    assert.equal(selected.sourceId,id);
    assert.equal(selected.query.time_basis,expected[id]);
    assert.equal(selected.query.since,query.since);assert.equal(selected.query.until,query.until);
    assert.equal(selected.query.country,"PL");assert.equal(selected.query.severity_min,2);
    assert.equal(Object.hasOwn(selected.query,"sourceId"),false);
    const params=new URLSearchParams(serializeQuery(selected.query));
    for(const key of ["source","source_id","sourceId"])assert.equal(params.has(key),false);
  }
  assert.throws(()=>selectPublicSource({query},"cloudflare_radar"),/spoza publicznego/);
  for(const [category,basis] of Object.entries(PUBLIC_TIME_BASIS)){
    assert.equal(changePublicQuery(query,{category:category as EventSummary["category"]}).time_basis,basis);
  }
});

test("a category change cannot silently keep an incompatible public source and GDACS retains both categories",()=>{
  const initial={query:{...PUBLIC_DEFAULT_QUERY,since:"2026-08-26T06:00:00Z",until:"2026-08-27T06:00:00Z"}};
  const gdacs=selectPublicSource(initial,"gdacs");
  assert.equal(gdacs.query.category,undefined);
  for(const category of ["earthquake","disaster"] as const){
    const next=changePublicFilters(gdacs,{category});
    assert.equal(next.sourceId,"gdacs");assert.equal(next.query.time_basis,"occurred");
    assert.equal(next.query.until,initial.query.until);
  }
  const weather=changePublicFilters(gdacs,{category:"weather"});
  assert.equal(weather.sourceId,undefined);assert.equal(weather.query.time_basis,"validity");
  assert.equal(weather.query.since,initial.query.since);
  const github=selectPublicSource(initial,"github_status");
  assert.equal(changePublicFilters(github,{category:undefined}).sourceId,undefined);
  const manual=changePublicFilters(github,{time_basis:"occurred"});
  assert.equal(manual.sourceId,"github_status");assert.equal(manual.query.until,initial.query.until);
  assert.equal(selectPublicSource(github).sourceId,undefined);
  assert.deepEqual(selectPublicSource(github).query,github.query);
});

test("source predicates feed one bounded list, timeline and map without fabricating locations or source independence",()=>{
  const snapshot=nineSourceFixture();
  const gdacs=snapshot.events.find((event)=>event.source_ids.includes("gdacs"))!;
  gdacs.geometry={type:"Point",coordinates:[21,52]};gdacs.severity=3;gdacs.countries=["PL"];
  snapshot.events.push({...structuredClone(gdacs),id:"aaaaaaaa-1111-5111-8111-111111111111",category:"disaster",geometry:null,severity:1});
  const selected=selectPublicSource({query:{...PUBLIC_DEFAULT_QUERY}},"gdacs");
  const all=filterPublicSnapshot(snapshot,selected.query,selected.sourceId);
  assert.deepEqual(new Set(all.items.map((event)=>event.category)),new Set(["earthquake","disaster"]));
  assert.equal(all.total,2);assert.equal(all.mapped,1);assert.equal(all.unlocated,1);
  const bounded=filterPublicSnapshot(snapshot,{...selected.query,limit:1},selected.sourceId);
  assert.equal(bounded.total,2);assert.equal(bounded.shown,1);assert.equal(bounded.truncated,true);
  assert.equal(bounded.items[0].id,gdacs.id);
  assert.equal(eventsToGeoJson(bounded.items).points.features.length,bounded.mapped);
  assert.equal(filterPublicSnapshot(snapshot,{...selected.query,country:"TR"},selected.sourceId).total,0);
  assert.equal(filterPublicSnapshot(snapshot,{...selected.query,min_sources:2},selected.sourceId).total,0);
  const noaa=selectPublicSource({query:{...PUBLIC_DEFAULT_QUERY}},"noaa_swpc");
  snapshot.events.find((event)=>event.source_ids.includes("noaa_swpc"))!.occurred_start=null;
  const noaaResult=filterPublicSnapshot(snapshot,noaa.query,noaa.sourceId);
  assert.equal(noaaResult.total,1);assert.equal(noaaResult.mapped,0);assert.equal(noaaResult.unlocated,1);
  assert.deepEqual(mappedCategories(noaaResult.items),[]);
  assert.throws(()=>filterPublicSnapshot(snapshot,selected.query,"cloudflare_radar"),/Brak metadanych/);
});

test("public health counts only enabled nonempty successful feeds and exposes missing, cached, empty and failed coverage",()=>{
  const snapshot=nineSourceFixture();
  const source=(id:string)=>snapshot.sources.find((item)=>item.id===id)!;
  source("usgs").record_count=20; // Provider count is not the count in the public artifact.
  source("meteoalarm").status="ok_empty";source("meteoalarm").record_count=0;
  source("cisa_kev").status="error";
  snapshot.events.find((event)=>event.source_ids.includes("cisa_kev"))!.tags.push("cached_public_data");
  source("gdacs").status="partial";
  source("easa_czib").status="pending";source("easa_czib").last_success_at=null;source("easa_czib").record_count=0;
  source("nasa_eonet").status="stale";
  source("noaa_swpc").last_success_at=null;
  source("github_status").enabled=false;source("github_status").status="disabled";
  snapshot.sources=snapshot.sources.filter((item)=>item.id!=="cloudflare_status");
  snapshot.events=snapshot.events.filter((event)=>!["meteoalarm","easa_czib","cloudflare_status"].includes(event.source_ids[0]));
  const overview=publicSourceCoverage(snapshot);
  assert.equal(overview.expected,9);assert.equal(overview.present,8);
  assert.equal(overview.healthy,1);assert.equal(overview.empty,1);assert.equal(overview.incomplete.length,7);
  assert.deepEqual(overview.missing.map((item)=>item.id),["cloudflare_status"]);
  assert.equal(overview.entries.find((item)=>item.id==="usgs")?.records,1);
  assert.equal(overview.entries.find((item)=>item.id==="cisa_kev")?.cached,1);
  assert.equal(overview.entries.find((item)=>item.id==="meteoalarm")?.tone,"muted");
  for(const item of overview.entries.filter((item)=>item.id!=="usgs"))assert.equal(item.healthy,false);
  const legacy=publicSourceCoverage(publicFixture());
  assert.equal(legacy.missing.length,8);assert.equal(legacy.healthy,1);
});

test("a new snapshot cannot make cached evidence current or replace its event time with a fetch time",()=>{
  const snapshot=publicFixture({tags:["cached_public_data"],occurred_start:null,issued_at:"2026-08-27T10:00:00Z"});
  snapshot.generated_at="2026-08-29T12:00:00Z";
  snapshot.sources[0].status="error";snapshot.sources[0].last_attempt_at=snapshot.generated_at;
  snapshot.sources[0].error="Source did not answer; earlier public data retained.";
  const retained=validatePublicSnapshot(snapshot).events[0];
  const query={...PUBLIC_DEFAULT_QUERY,time_basis:"published" as const};
  assert.equal(filterPublicSnapshot(snapshot,query,"usgs").total,0);
  const history=filterPublicSnapshot(snapshot,{...query,window_hours:168},"usgs");
  assert.equal(history.total,1);assert.equal(history.items[0].occurred_start,null);
  assert.match(history.limitations!.join(" "),/poprzedniego publicznego odczytu/);
  assert.equal(retained.evidence[0].retrieved_at,"2026-08-27T11:00:00Z");
  const markup=renderToStaticMarkup(React.createElement(EventEvidence,{detail:retained,publicMode:true,readAt:snapshot.generated_at,selected:true,loading:false,error:null,outsideFilter:false,onSelect:()=>undefined,onRetry:()=>undefined}));
  assert.match(markup,/Poprzedni publiczny odczyt; źródło nie odpowiedziało/);
  assert.match(markup,/Daty dowodów nie są bieżące/);
  assert.match(markup,/27.08.2026, 13:00/);assert.match(markup,/29.08.2026, 14:00/);
  const coverage=publicSourceCoverage(snapshot);
  const sources=renderToStaticMarkup(React.createElement(SourcePanel,{sources:snapshot.sources,snapshotAt:snapshot.generated_at,publicCoverage:coverage,loading:false,error:null,onRetry:()=>undefined,onSelectSource:()=>undefined}));
  assert.match(sources,/1 rekordów w zestawie.*1 z poprzedniego odczytu/);
  assert.match(sources,/tone-error/);assert.doesNotMatch(sources,/tone-ok/);
  assert.match(sources,/Pokaż rekordy źródła/);
});

test("source selection is a labelled public-only control, all categories are reachable, and the source panel is discoverable",()=>{
  const snapshot=nineSourceFixture(),overview=publicSourceCoverage(snapshot);
  const sourceFilter={value:"gdacs",options:overview.entries.map((item)=>({id:item.id,name:item.name,label:item.label,records:item.records,available:Boolean(item.source)})),onChange:()=>undefined,onShowSources:()=>undefined};
  const props={query:PUBLIC_DEFAULT_QUERY,onChange:()=>undefined,onReset:()=>undefined};
  const markup=renderToStaticMarkup(React.createElement(FilterPanel,{...props,snapshot:{generatedAt:snapshot.generated_at,countries:["PL"],sourceFilter}}));
  assert.match(markup,/label for="public-source">Źródło danych/);
  assert.match(markup,/aria-describedby="public-source-help"/);
  assert.match(markup,/Stan i pokrycie źródeł/);
  for(const id of PUBLIC_SOURCE_IDS)assert.ok(markup.includes(`value="${id}"`));
  for(const category of Object.keys(PUBLIC_TIME_BASIS))assert.ok(markup.includes(`value="${category}"`));
  assert.doesNotMatch(markup,/class="reset-filters" disabled/);
  const privateMarkup=renderToStaticMarkup(React.createElement(FilterPanel,props));
  assert.doesNotMatch(privateMarkup,/public-source|Źródło danych/);
  const shell=renderToStaticMarkup(React.createElement(PublicMonitor));
  assert.match(shell,/aria-controls="public-details-panel">Źródła/);
  assert.match(shell,/aria-label="Główne widoki"/);
  assert.match(shell,/Bez danych zastępczych/);
});
