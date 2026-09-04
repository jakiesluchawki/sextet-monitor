"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { FeatureCollection, Polygon } from "geojson";
import type { ExpressionSpecification, GeoJSONSource, Map as LibreMap, Marker, Popup, StyleSpecification } from "maplibre-gl";
import type { EventQuery, EventSummary } from "@/lib/contracts";
import { eventsToGeoJson, positionsOf } from "@/lib/map-data";
import { CATEGORY_SHORT } from "@/lib/filters";
import { assetPath } from "@/lib/assets";
import { CAMERA_PRESETS, cameraAnimationDuration, geometryCameraBounds, mapGeometryCoverage, worldCameraResizeZoom, worldCameraZoom, type CameraBounds, type CameraScope, type MapProjection } from "@/lib/map-camera";
import { Icon } from "./Icon";

const EMPTY: FeatureCollection = {type:"FeatureCollection",features:[]};
const CATEGORY_COLOR: ExpressionSpecification = ["match",["get","category"],"earthquake","#d4af76","disaster","#cd967d","weather","#7db7ce","aviation","#a7b2d9","cyber","#b3a1c9","internet","#8cbcac","space_weather","#c6a17e","#a4b3a5"];
const STYLE: StyleSpecification = {
  version:8,
  projection:{type:"globe"},
  sky:{"sky-color":"#151d1b","horizon-color":"#151d1b","fog-color":"#1a2427","sky-horizon-blend":0,"horizon-fog-blend":0,"fog-ground-blend":0,"atmosphere-blend":0},
  sources:{countries:{type:"geojson",data:assetPath("/maps/countries.geojson"),attribution:"Natural Earth"}},
  layers:[
    {id:"background",type:"background",paint:{"background-color":"#1a2427"}},
    {id:"countries-fill",type:"fill",source:"countries",paint:{"fill-color":"#2b3432"}},
    {id:"countries-lines",type:"line",source:"countries",paint:{"line-color":"#46524b","line-width":0.65}},
  ],
};
function motionDuration(){return cameraAnimationDuration(window.matchMedia("(prefers-reduced-motion: reduce)").matches);}
function fitCamera(map:LibreMap,bounds:CameraBounds,maxZoom=8){
  const size=map.getContainer();
  const padding=Math.min(50,Math.max(20,Math.min(size.clientWidth,size.clientHeight)*0.13));
  map.fitBounds(bounds,{padding,maxZoom,bearing:0,pitch:0,duration:motionDuration(),linear:true});
}
function worldCamera(map:LibreMap){
  const size=map.getContainer();
  if(map.getProjection().type==="globe")map.easeTo({center:[24,27],zoom:worldCameraZoom(size.clientWidth,size.clientHeight),bearing:0,pitch:0,duration:motionDuration()});
  else fitCamera(map,[[-179,-60],[179,82]],1.4);
}
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
export default function EventMap({events,query,selectedId,onSelect,onFallback,initialProjection="mercator",cameraScope}:{events:EventSummary[];query:EventQuery;selectedId:string|null;onSelect:(id:string)=>void;onFallback:()=>void;initialProjection?:MapProjection;cameraScope?:CameraScope}){
  const container=useRef<HTMLDivElement>(null);
  const mapRef=useRef<LibreMap|null>(null);
  const latest=useRef({events,query,selectedId,onSelect});
  latest.current={events,query,selectedId,onSelect};
  const [ready,setReady]=useState(false);
  const [failure,setFailure]=useState<string|null>(null);
  const [warning,setWarning]=useState<string|null>(null);
  const [projection,setProjection]=useState<MapProjection>(initialProjection);
  const [cameraPreset,setCameraPreset]=useState<string|null>(initialProjection==="globe"?"world":null);
  const cameraPresetRef=useRef<string|null>(initialProjection==="globe"?"world":null);
  const chooseCameraPreset=useCallback((preset:string|null)=>{
    cameraPresetRef.current=preset;
    setCameraPreset(preset);
  },[]);
  const [cameraNotice,setCameraNotice]=useState("");
  const categories=useMemo(()=>mappedCategories(events),[events]);
  const coverage=useMemo(()=>mapGeometryCoverage(events),[events]);
  const selected=useMemo(()=>events.find((event)=>event.id===selectedId) ?? null,[events,selectedId]);
  const selectedHasGeometry=!!selected && positionsOf(selected.geometry).length>0;

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
          container:container.current,style:{...STYLE,projection:{type:initialProjection}},center:initialProjection==="globe"?[24,27]:[24,45],zoom:initialProjection==="globe"?worldCameraZoom(container.current.clientWidth,container.current.clientHeight):2.2,minZoom:-2,maxZoom:16,
          attributionControl:false,renderWorldCopies:false,
          dragRotate:false,pitchWithRotate:false,touchPitch:false,
          locale:{"AttributionControl.ToggleAttribution":"Informacja o mapie"},
        });
      }catch{
        fail("Przeglądarka nie uruchomiła WebGL. Wszystkie rekordy pozostają dostępne na liście.");
        return;
      }
      mapRef.current=map;
      map.touchZoomRotate.disableRotation();
      const resizeMap=()=>{
        if(disposed || failed)return;
        map.resize();
        const element=map.getContainer();
        const currentProjection=map.getProjection()?.type;
        const mode=currentProjection==="globe" || currentProjection==="mercator"?currentProjection:initialProjection;
        const zoom=worldCameraResizeZoom(mode,cameraPresetRef.current,element.clientWidth,element.clientHeight);
        if(zoom!==null)map.jumpTo({center:[24,27],zoom,bearing:0,pitch:0});
      };
      map.addControl(new lib.AttributionControl({compact:true,customAttribution:"Granice orientacyjne"}),"bottom-right");
      map.getCanvas().setAttribute("aria-label","Mapa zdarzeń. Strzałki przesuwają mapę, plus i minus zmieniają skalę. Pełna alternatywa klawiaturowa znajduje się na liście zdarzeń.");
      popup=new lib.Popup({closeButton:false,closeOnClick:true,offset:12,maxWidth:"300px"});
      const expand=async(id:number,coordinates:number[])=>{
        try{
          const source=map.getSource("event-points") as GeoJSONSource;
          const zoom=await source.getClusterExpansionZoom(id);
          if(disposed || failed)return;
          chooseCameraPreset(null);
          map.easeTo({center:[coordinates[0],coordinates[1]],zoom,duration:motionDuration()});
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
            clusterMarkers.set(id,new lib.Marker({element,opacityWhenCovered:0}).setLngLat([coordinates[0],coordinates[1]]).addTo(map));
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
        resizeMap();
        setReady(true);
      });
      map.on("movestart",(event)=>{if(event.originalEvent)chooseCameraPreset(null);});
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
      observer=new ResizeObserver(resizeMap);
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

  // The geographic scope is a data filter owned by the parent. Move once when
  // it changes, but do not force that camera again after manual navigation.
  // This effect precedes selected-record focus so a deep link still wins.
  useEffect(()=>{
    const map=mapRef.current;
    if(!ready || !map || !cameraScope)return;
    const preset=CAMERA_PRESETS.find((item)=>item.id===cameraScope);
    if(!preset)return;
    chooseCameraPreset(preset.id);
    if(preset.bounds)fitCamera(map,preset.bounds);
    else worldCamera(map);
    setCameraNotice(`Kamera ustawiona dla zakresu: ${preset.label}. Możesz ją przesuwać bez zmiany filtrów.`);
  },[ready,cameraScope,chooseCameraPreset]);

  const focusSelected=useCallback(()=>{
    const map=mapRef.current;
    if(!ready || !map || !selected)return;
    let bounds=geometryCameraBounds([selected.geometry],projection);
    if(!bounds)return;
    // A globe cannot show opposing hemispheres at once. Broad geometries are
    // fitted in 2D rather than falsely implying that every part is visible.
    if(projection==="globe" && (bounds[1][0]-bounds[0][0]>160 || bounds[1][1]-bounds[0][1]>140)){
      map.setProjection({type:"mercator"});setProjection("mercator");
      bounds=geometryCameraBounds([selected.geometry],"mercator")!;
    }
    chooseCameraPreset(null);
    fitCamera(map,bounds,selected.location_precision==="point"?8:6);
    setCameraNotice("Kamera: wybrany rekord. Jego geometria pozostaje bez zmian.");
  },[ready,selected,projection,chooseCameraPreset]);
  const lastFocused=useRef<string|null>(null);
  useEffect(()=>{
    if(!ready || selectedId===lastFocused.current)return;
    lastFocused.current=selectedId;
    if(selectedHasGeometry)focusSelected();
  },[ready,selectedId,selectedHasGeometry,focusSelected]);

  const fit=()=>{
    const map=mapRef.current;
    if(!map || !coverage.mapped)return;
    let bounds=geometryCameraBounds(events.map((event)=>event.geometry),projection)!;
    const broad=projection==="globe" && (bounds[1][0]-bounds[0][0]>160 || bounds[1][1]-bounds[0][1]>140);
    if(broad){
      map.setProjection({type:"mercator"});setProjection("mercator");
      bounds=geometryCameraBounds(events.map((event)=>event.geometry),"mercator")!;
    }
    chooseCameraPreset(null);
    fitCamera(map,bounds);
    setCameraNotice(broad?"Kamera: cały wynik w 2D. Globus nie pokazuje obu półkul jednocześnie.":"Kamera: geometrie wszystkich wyświetlanych rekordów.");
  };
  const showPreset=(preset:typeof CAMERA_PRESETS[number])=>{
    const map=mapRef.current;if(!map)return;
    chooseCameraPreset(preset.id);
    if(preset.bounds)fitCamera(map,preset.bounds);
    else worldCamera(map);
    setCameraNotice(`Kamera: ${preset.label}. Filtry i liczba rekordów bez zmian.`);
  };
  const switchProjection=(next:MapProjection)=>{
    const map=mapRef.current;if(!map || next===projection)return;
    try{
      map.setProjection({type:next});setProjection(next);
      if(cameraPreset==="world")worldCamera(map);
      setCameraNotice(next==="globe"?"Widok globusu. Widoczna jest jedna półkula.":"Widok mapy 2D. Filtry pozostają bez zmian.");
    }catch{
      map.setProjection({type:"mercator"});setProjection("mercator");
      setWarning("Globus nie jest dostępny w tej przeglądarce. Pozostaje mapa 2D i pełna lista rekordów.");
    }
  };
  return <div className="event-map map-experience" data-map-status={failure ? "failed" : ready ? "ready" : "loading"} data-map-projection={projection}>
    <div className="map-camera-controls">
      <div className="map-camera-presets" role="group" aria-label="Położenie kamery, bez zmiany filtrów"><span className="map-camera-label">Kamera</span>{CAMERA_PRESETS.map((preset)=><button type="button" key={preset.id} disabled={!ready} aria-pressed={cameraPreset===preset.id} onClick={()=>showPreset(preset)}>{preset.label}</button>)}</div>
      <div className="map-camera-actions"><button type="button" onClick={fit} disabled={!ready || !coverage.mapped}><Icon name="map" size={14}/>Dopasuj wynik</button><button type="button" onClick={focusSelected} disabled={!ready || !selectedHasGeometry} title={selected && !selectedHasGeometry?"Wybrany rekord nie ma geometrii źródłowej":undefined}>Wybrany rekord</button></div>
      <span className="map-camera-note">Położenie kamery nie zmienia filtrów.</span>
    </div>
    <div className="map-stage">
      <div ref={container} className="map-canvas"/>
      {!ready && !failure && <div className="map-loading" role="status">Ładowanie lokalnej mapy…</div>}
      {failure ? <div className="map-fallback" role="alert"><Icon name="map" size={25}/><strong>Mapa niedostępna</strong><p>{failure}</p><button type="button" onClick={onFallback}>Przejdź do listy</button></div> : <>
        <div className="map-toolbar"><div className="map-projection-controls" role="group" aria-label="Odwzorowanie mapy"><button type="button" disabled={!ready} aria-pressed={projection==="globe"} onClick={()=>switchProjection("globe")}>Globus</button><button type="button" disabled={!ready} aria-pressed={projection==="mercator"} onClick={()=>switchProjection("mercator")}>Mapa 2D</button></div><div className="map-zoom"><button type="button" aria-label="Przybliż mapę" disabled={!ready} onClick={()=>{chooseCameraPreset(null);mapRef.current?.zoomIn({duration:motionDuration()});}}>+</button><button type="button" aria-label="Oddal mapę" disabled={!ready} onClick={()=>{chooseCameraPreset(null);mapRef.current?.zoomOut({duration:motionDuration()});}}>−</button></div></div>
        {warning && <p className="map-warning" role="status">{warning}</p>}
        {ready && !coverage.mapped && <div className="map-empty-state"><strong>{events.length?"Te rekordy nie mają geometrii":"Brak rekordów w tym wyniku"}</strong><span>{events.length?"Nie przypisujemy im zastępczych punktów. Sprawdź ich daty i dowody na liście.":"Zmień filtry lub poszerz przedział czasu. Przesuwanie mapy nie zmienia wyniku."}</span><button type="button" onClick={onFallback}>Przejdź do listy</button></div>}
        <div className="map-projection-note">{projection==="globe"?"Globus: widoczna jedna półkula":"Mapa 2D"} · Natural Earth · podkład lokalny</div>
      </>}
    </div>
    <div className="map-coverage"><strong>Geometria: {coverage.mapped} / {coverage.total} rekordów</strong><span>{coverage.pointRecords} z punktami · {coverage.areaOnlyRecords} tylko obszar lub linia</span>{coverage.unlocated>0 && <button type="button" onClick={onFallback}>{coverage.unlocated} bez geometrii, zobacz listę</button>}{selected && !selectedHasGeometry && <span className="map-selected-note">Wybrany rekord: brak geometrii źródłowej.</span>}</div>
    <div className="map-legend"><span>Warstwy wyniku</span>{categories.map((category)=><span key={category}><i className={`category-dot category-${category}`}/>{CATEGORY_SHORT[category]}</span>)}{query.radius_km && <span>Linia przerywana: filtr promienia</span>}</div>
    <span className="map-camera-announcement" role="status" aria-live="polite">{cameraNotice}</span>
  </div>;
}
