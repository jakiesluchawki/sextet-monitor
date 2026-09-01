"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import type { FeatureCollection, Geometry, Point, Polygon } from "geojson";
import type { ExpressionSpecification, GeoJSONSource, Map as LibreMap, Marker, Popup, StyleSpecification } from "maplibre-gl";
import type { EventQuery, EventSummary } from "@/lib/contracts";
import { eventsToGeoJson, positionsOf } from "@/lib/map-data";
import { CATEGORY_SHORT } from "@/lib/filters";
import { assetPath } from "@/lib/assets";
import { Icon } from "./Icon";

const EMPTY: FeatureCollection = {type:"FeatureCollection",features:[]};
const CATEGORY_COLOR: ExpressionSpecification = ["match",["get","category"],"earthquake","#d4af76","disaster","#cd967d","weather","#7db7ce","aviation","#a7b2d9","cyber","#b3a1c9","internet","#8cbcac","#a4b3a5"];
const STYLE: StyleSpecification = {
  version:8,
  sources:{countries:{type:"geojson",data:assetPath("/maps/countries.geojson"),attribution:"Natural Earth"}},
  layers:[
    {id:"background",type:"background",paint:{"background-color":"#1a2427"}},
    {id:"countries-fill",type:"fill",source:"countries",paint:{"fill-color":"#2b3432"}},
    {id:"countries-lines",type:"line",source:"countries",paint:{"line-color":"#46524b","line-width":0.65}},
  ],
};
function radiusFeature(query:EventQuery): FeatureCollection<Polygon> {
  if (query.lat==null || query.lon==null || !query.radius_km) return {type:"FeatureCollection",features:[]};
  const radians=Math.PI/180, latitude=query.lat*radians, longitude=query.lon*radians, distance=query.radius_km/6371.0088;
  const ring:number[][]=[];
  for(let i=0;i<=96;i++){
    const bearing=i*2*Math.PI/96;
    const lat=Math.asin(Math.sin(latitude)*Math.cos(distance)+Math.cos(latitude)*Math.sin(distance)*Math.cos(bearing));
    const lon=longitude+Math.atan2(Math.sin(bearing)*Math.sin(distance)*Math.cos(latitude),Math.cos(distance)-Math.sin(latitude)*Math.sin(lat));
    ring.push([((lon/radians+540)%360)-180,lat/radians]);
  }
  return {type:"FeatureCollection",features:[{type:"Feature",properties:{},geometry:{type:"Polygon",coordinates:[ring]}}]};
}
function updateData(map:LibreMap, events:EventSummary[], query:EventQuery){
  const data=eventsToGeoJson(events);
  (map.getSource("event-points") as GeoJSONSource | undefined)?.setData(data.points);
  (map.getSource("event-areas") as GeoJSONSource | undefined)?.setData(data.areas);
  (map.getSource("query-radius") as GeoJSONSource | undefined)?.setData(radiusFeature(query));
}
function updateSelection(map:LibreMap, selected:string|null){
  if(!map.getLayer("event-dots"))return;
  const selectedExpression:ExpressionSpecification=["==",["get","eventId"],selected || ""];
  map.setPaintProperty("event-dots","circle-stroke-width",["case",selectedExpression,3,1]);
  map.setPaintProperty("event-dots","circle-stroke-color",["case",selectedExpression,"#e2e9d5","#1c2727"]);
  map.setPaintProperty("event-area-lines","line-width",["case",selectedExpression,2.5,1.1]);
  map.setPaintProperty("event-area-fill","fill-opacity",["case",selectedExpression,0.24,0.10]);
}
export function mappedCategories(events:EventSummary[]) {
  return [...new Set(events.filter((event)=>positionsOf(event.geometry).length>0).map((event)=>event.category))];
}
export default function EventMap({events,query,selectedId,onSelect,onFallback}:{events:EventSummary[];query:EventQuery;selectedId:string|null;onSelect:(id:string)=>void;onFallback:()=>void}){
  const container=useRef<HTMLDivElement>(null);
  const mapRef=useRef<LibreMap|null>(null);
  const latest=useRef({events,query,selectedId,onSelect});
  latest.current={events,query,selectedId,onSelect};
  const [ready,setReady]=useState(false);
  const [failure,setFailure]=useState<string|null>(null);
  const [warning,setWarning]=useState<string|null>(null);
  const categories=useMemo(()=>mappedCategories(events),[events]);
  const mappedPositions=useMemo(()=>events.flatMap((event)=>positionsOf(event.geometry)),[events]);

  useEffect(()=>{
    let disposed=false;
    let observer:ResizeObserver|undefined;
    let popup:Popup|undefined;
    const clusterMarkers=new Map<number,Marker>();
    let loaded=false;
    let failed=false;
    const releaseMap=()=>{
      observer?.disconnect();popup?.remove();
      clusterMarkers.forEach((marker)=>marker.remove());clusterMarkers.clear();
      mapRef.current?.remove();mapRef.current=null;
    };
    const fail=(message:string)=>{
      if(disposed || failed)return;
      failed=true;window.clearTimeout(loadDeadline);
      setReady(false);setFailure(message);releaseMap();
    };
    const loadDeadline=window.setTimeout(()=>fail("Mapa nie uruchomiła się w ciągu 20 sekund. Sprawdź lokalne pliki mapy i moduły MapLibre. Te same rekordy są dostępne na liście."),20_000);
    // Load the unmodified ESM from this origin. Bundling import.meta.url would
    // embed the build machine's file:// path in the public JavaScript chunk.
    const libraryUrl=new URL(assetPath("/maplibre/maplibre-gl.mjs"),window.location.href).href;
    void import(/* webpackIgnore: true */ libraryUrl).then((lib:typeof import("maplibre-gl"))=>{
      if(disposed || failed || !container.current)return;
      let map:LibreMap;
      try{
        // Main, worker and shared ESM remain unchanged and share this origin.
        lib.setWorkerUrl(new URL(assetPath("/maplibre/maplibre-gl-worker.mjs"),window.location.href).href);
        map=new lib.Map({
          container:container.current,style:STYLE,center:[24,45],zoom:2.2,minZoom:-2,maxZoom:16,
          attributionControl:false,renderWorldCopies:false,
          locale:{"AttributionControl.ToggleAttribution":"Informacja o mapie"},
        });
      }catch{
        fail("Przeglądarka nie uruchomiła WebGL. Wszystkie rekordy pozostają dostępne na liście.");
        return;
      }
      mapRef.current=map;
      map.addControl(new lib.AttributionControl({compact:true,customAttribution:"Granice orientacyjne"}),"bottom-right");
      map.getCanvas().setAttribute("aria-label","Mapa zdarzeń. Strzałki przesuwają mapę, plus i minus zmieniają skalę. Pełna alternatywa klawiaturowa znajduje się na liście zdarzeń.");
      popup=new lib.Popup({closeButton:false,closeOnClick:true,offset:12,maxWidth:"300px"});
      const expand=async(id:number,coordinates:number[])=>{
        try{
          const source=map.getSource("event-points") as GeoJSONSource;
          const zoom=await source.getClusterExpansionZoom(id);
          if(disposed || failed)return;
          map.easeTo({center:[coordinates[0],coordinates[1]],zoom,duration:window.matchMedia("(prefers-reduced-motion: reduce)").matches?0:220});
        }catch{/* A cluster can disappear between a refresh and a click. */}
      };
      const renderClusters=()=>{
        if(disposed || failed || !map.getLayer("clusters"))return;
        const visible=new Set<number>();
        for(const feature of map.queryRenderedFeatures({layers:["clusters"]})){
          if(feature.geometry.type!=="Point")continue;
          const id=Number(feature.properties.cluster_id);
          if(!Number.isFinite(id) || visible.has(id))continue;
          visible.add(id);
          const count=Number(feature.properties.point_count);
          if(!Number.isFinite(count))continue;
          const existing=clusterMarkers.get(id);
          const element=existing?.getElement() as HTMLButtonElement | undefined || document.createElement("button");
          const coordinates=feature.geometry.coordinates;
          element.classList.add("cluster-count");
          element.type="button";
          element.textContent=String(count);
          element.dataset.lon=String(coordinates[0]);
          element.dataset.lat=String(coordinates[1]);
          element.setAttribute("aria-label",`${count} punktów źródłowych. Przybliż klaster.`);
          element.title=`${count} punktów źródłowych, nie liczba niezależnych potwierdzeń`;
          if(existing){
            existing.setLngLat([coordinates[0],coordinates[1]]);
          }else{
            element.addEventListener("click",(event)=>{event.stopPropagation();void expand(id,[Number(element.dataset.lon),Number(element.dataset.lat)]);});
            clusterMarkers.set(id,new lib.Marker({element}).setLngLat([coordinates[0],coordinates[1]]).addTo(map));
          }
        }
        for(const [id,marker] of clusterMarkers){if(!visible.has(id)){marker.remove();clusterMarkers.delete(id);}}
      };
      map.on("load",()=>{
        if(disposed || failed)return;
        map.addSource("event-points",{type:"geojson",data:EMPTY,cluster:true,clusterRadius:48,clusterMaxZoom:12});
        map.addSource("event-areas",{type:"geojson",data:EMPTY});
        map.addSource("query-radius",{type:"geojson",data:EMPTY});
        map.addLayer({id:"query-radius-fill",type:"fill",source:"query-radius",paint:{"fill-color":"#bfd09e","fill-opacity":0.035}});
        map.addLayer({id:"query-radius-line",type:"line",source:"query-radius",paint:{"line-color":"#bfd09e","line-width":1.5,"line-dasharray":[3,3]}});
        map.addLayer({id:"event-area-fill",type:"fill",source:"event-areas",filter:["==",["geometry-type"],"Polygon"],paint:{"fill-color":CATEGORY_COLOR,"fill-opacity":0.10}});
        map.addLayer({id:"event-area-lines",type:"line",source:"event-areas",paint:{"line-color":CATEGORY_COLOR,"line-width":1.1,"line-opacity":0.9}});
        map.addLayer({id:"clusters",type:"circle",source:"event-points",filter:["has","point_count"],paint:{"circle-color":"#384942","circle-radius":["step",["get","point_count"],17,10,20,50,24],"circle-stroke-width":1.2,"circle-stroke-color":"#a0b4a0"}});
        map.addLayer({id:"event-dots",type:"circle",source:"event-points",filter:["!",["has","point_count"]],paint:{"circle-color":CATEGORY_COLOR,"circle-radius":["interpolate",["linear"],["get","severity"],0,5,4,8],"circle-stroke-color":"#1c2727","circle-stroke-width":1}});
        updateData(map,latest.current.events,latest.current.query);
        updateSelection(map,latest.current.selectedId);
        map.on("click","clusters",(event)=>{
          const feature=event.features?.[0];
          if(feature?.geometry.type==="Point")void expand(Number(feature.properties.cluster_id),feature.geometry.coordinates);
        });
        for(const layer of ["event-dots","event-area-fill","event-area-lines"]){
          map.on("click",layer,(event)=>{
            const id=event.features?.[0]?.properties?.eventId;
            if(typeof id==="string"){latest.current.onSelect(id);popup?.remove();}
          });
          map.on("mouseenter",layer,()=>{map.getCanvas().style.cursor="pointer";});
          map.on("mouseleave",layer,()=>{map.getCanvas().style.cursor="";popup?.remove();});
          map.on("mousemove",layer,(event)=>{
            const feature=event.features?.[0];
            if(!feature)return;
            const text=document.createElement("div");
            const title=document.createElement("strong");title.textContent=String(feature.properties?.title || "");
            const hint=document.createElement("span");hint.textContent="Wybierz, aby sprawdzić dowody i dokładność pozycji.";
            text.append(title,hint);
            popup?.setLngLat(event.lngLat).setDOMContent(text).addTo(map);
          });
        }
        map.on("idle",renderClusters);
        map.on("moveend",renderClusters);
        loaded=true;window.clearTimeout(loadDeadline);
        setReady(true);
      });
      map.on("error",(event)=>{
        if(disposed || failed)return;
        const message=String(event.error?.message || "").toLowerCase();
        if(message.includes("webgl") || message.includes("context lost")){
          fail("Kontekst WebGL jest niedostępny. Użyj pełnej listy zdarzeń.");
        }else if(!loaded){
          fail("Nie udało się wczytać lokalnej mapy. Sprawdź dostępność jej plików; lista pozostaje dostępna.");
        }else{
          setWarning("Nie udało się wczytać części lokalnych warstw mapy. Lista pozostaje dostępna.");
        }
      });
      observer=new ResizeObserver(()=>{if(!disposed)map.resize();});
      observer.observe(container.current);
    }).catch(()=>fail("Nie udało się załadować silnika mapy. Lista zawiera ten sam wynik filtrów."));
    return()=>{
      disposed=true;window.clearTimeout(loadDeadline);releaseMap();
    };
  },[]);
  useEffect(()=>{
    const map=mapRef.current;
    if(ready && map)updateData(map,events,query);
  },[ready,events,query]);
  useEffect(()=>{const map=mapRef.current;if(ready && map)updateSelection(map,selectedId);},[ready,selectedId]);

  const fit=()=>{
    const map=mapRef.current;
    if(!map || !mappedPositions.length)return;
    let west=Infinity,east=-Infinity,south=Infinity,north=-Infinity;
    for(const [lon,lat] of mappedPositions){west=Math.min(west,lon);east=Math.max(east,lon);south=Math.min(south,lat);north=Math.max(north,lat);}
    map.fitBounds([[west,south],[east,north]],{padding:54,maxZoom:9,duration:window.matchMedia("(prefers-reduced-motion: reduce)").matches?0:220});
  };
  return <div className="event-map" data-map-status={failure ? "failed" : ready ? "ready" : "loading"}>
    <div ref={container} className="map-canvas"/>
    {!ready && !failure && <div className="map-loading" role="status">Ładowanie lokalnej mapy…</div>}
    {failure ? <div className="map-fallback" role="alert"><Icon name="map" size={25}/><strong>Mapa niedostępna</strong><p>{failure}</p><button onClick={onFallback}>Przejdź do listy</button></div> : <>
      <div className="map-toolbar"><button onClick={fit} disabled={!ready || !mappedPositions.length}><Icon name="map" size={14}/>Pokaż wynik</button><div className="map-zoom"><button aria-label="Przybliż mapę" disabled={!ready} onClick={()=>mapRef.current?.zoomIn({duration:150})}>+</button><button aria-label="Oddal mapę" disabled={!ready} onClick={()=>mapRef.current?.zoomOut({duration:150})}>−</button></div></div>
      <div className="map-caption">Podkład lokalny · Natural Earth</div>
      {warning && <p className="map-warning" role="status">{warning}</p>}
      <div className="map-legend"><span>Dane na mapie</span>{categories.map((category)=><span key={category}><i className={`category-dot category-${category}`}/>{CATEGORY_SHORT[category]}</span>)}{query.radius_km && <span>Linia przerywana: filtr promienia</span>}</div>
    </>}
  </div>;
}
