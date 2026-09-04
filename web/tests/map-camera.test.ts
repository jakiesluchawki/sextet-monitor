import assert from "node:assert/strict";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import type { Geometry } from "geojson";
import type { EventSummary } from "../lib/contracts";
import { DEFAULT_QUERY } from "../lib/filters";
import EventMap from "../components/EventMap";
import { cameraAnimationDuration, geometryCameraBounds, mapGeometryCoverage, worldCameraResizeZoom, worldCameraZoom } from "../lib/map-camera";

const point = (longitude: number, latitude: number): Geometry => ({type:"Point",coordinates:[longitude,latitude]});
function event(id:string,geometry:Geometry|null):EventSummary {
  return {id,geometry,category:"earthquake",title:id,severity:0} as EventSummary;
}

test("camera does not invent geometry or a location for unknown and invalid positions",()=>{
  assert.equal(geometryCameraBounds([null]),null);
  assert.equal(geometryCameraBounds([point(NaN,20),point(21,91)]),null);
  assert.equal(geometryCameraBounds([{type:"GeometryCollection",geometries:[]}]),null);
});

test("camera fit preserves a point and does not rewrite source polygon vertices",()=>{
  const polygon:Geometry={type:"Polygon",coordinates:[[[14,49],[24,49],[24,55],[14,49]]]};
  const before=JSON.stringify(polygon);
  assert.deepEqual(geometryCameraBounds([point(21,52)]),[[21,52],[21,52]]);
  assert.deepEqual(geometryCameraBounds([polygon]),[[14,49],[24,55]]);
  assert.equal(JSON.stringify(polygon),before);
});

test("globe fits two dateline points locally while a single-world 2D map preserves both sides",()=>{
  const records=[point(179,20),point(-179,21)];
  assert.deepEqual(geometryCameraBounds(records,"globe"),[[179,20],[181,21]]);
  assert.deepEqual(geometryCameraBounds(records,"mercator"),[[-179,20],[179,21]]);
});

test("connected source polygons are not reinterpreted as dateline shortcuts",()=>{
  const broad:Geometry={type:"Polygon",coordinates:[[[-170,0],[170,0],[170,10],[-170,0]]]};
  assert.deepEqual(geometryCameraBounds([broad],"globe"),[[-170,0],[170,10]]);
  const cut:Geometry={type:"MultiPolygon",coordinates:[[[[175,0],[180,0],[180,10],[175,0]]],[[[-180,0],[-175,0],[-175,10],[-180,0]]]]};
  assert.deepEqual(geometryCameraBounds([cut],"globe"),[[175,0],[185,10]]);
});

test("mixed and overlapping longitude intervals retain their complete source paths",()=>{
  const geometries:Geometry[]=[
    {type:"LineString",coordinates:[[10,30],[30,40]]},
    {type:"LineString",coordinates:[[20,20],[40,45]]},
    point(0,50),
  ];
  assert.deepEqual(geometryCameraBounds(geometries,"globe"),[[0,20],[40,50]]);
  assert.deepEqual(geometryCameraBounds([{type:"GeometryCollection",geometries}],"globe"),[[0,20],[40,50]]);
});

test("poles remain in source geometry while camera fit stays finite",()=>{
  const poles=[point(10,90),point(12,-90)];
  const before=JSON.stringify(poles);
  const bounds=geometryCameraBounds(poles)!;
  assert.ok(bounds.flat().every(Number.isFinite));
  assert.ok(bounds[0][1]>-90 && bounds[1][1]<90);
  assert.equal(JSON.stringify(poles),before);
});

test("coverage counts records, not vertices or source points, and exposes unknown geometry",()=>{
  const area:Geometry={type:"Polygon",coordinates:[[[1,1],[2,1],[2,2],[1,1]]]};
  const records=[
    event("many-points",{type:"MultiPoint",coordinates:[[10,10],[11,11],[12,12]]}),
    event("area",area),
    event("both",{type:"GeometryCollection",geometries:[area,point(11,10)]}),
    event("unknown",null),
    event("invalid",point(400,52)),
  ];
  assert.deepEqual(mapGeometryCoverage(records),{total:5,mapped:3,pointRecords:2,areaOnlyRecords:1,unlocated:2});
});

test("all camera animation paths can respect reduced motion and tiny viewports stay finite",()=>{
  assert.equal(cameraAnimationDuration(true),0);
  assert.equal(cameraAnimationDuration(false),240);
  for(const [width,height] of [[0,0],[390,180],[1200,800]]){
    const zoom=worldCameraZoom(width,height);
    assert.ok(Number.isFinite(zoom) && zoom>=-2 && zoom<=1.5);
  }
});

test("resize fits only the explicitly selected globe world view and leaves user navigation intact",()=>{
  assert.equal(worldCameraResizeZoom("globe","world",600,250),worldCameraZoom(600,250));
  assert.ok(worldCameraResizeZoom("globe","world",600,320)!>worldCameraResizeZoom("globe","world",390,180)!);
  for(const preset of [null,"poland","europe","turkey"]){
    assert.equal(worldCameraResizeZoom("globe",preset,390,180),null);
  }
  assert.equal(worldCameraResizeZoom("mercator","world",600,250),null);
  for(const [width,height] of [[0,180],[390,0],[NaN,180],[390,Infinity]]){
    assert.equal(worldCameraResizeZoom("globe","world",width,height),null);
  }
});

test("map controls explicitly separate camera from filters and keep unknown records accessible",()=>{
  const html=renderToStaticMarkup(React.createElement(EventMap,{
    events:[event("unknown",null)],query:DEFAULT_QUERY,selectedId:"unknown",onSelect:()=>undefined,onFallback:()=>undefined,
  }));
  assert.match(html,/Położenie kamery nie zmienia filtrów/);
  for(const label of ["Świat","Europa","Polska","Turcja","Dopasuj wynik","Wybrany rekord","Globus","Mapa 2D"])assert.ok(html.includes(label),label);
  assert.match(html,/Geometria: 0 \/ 1 rekordów/);
  assert.match(html,/1 bez geometrii, zobacz listę/);
  assert.match(html,/Wybrany rekord: brak geometrii źródłowej/);
  assert.match(html,/data-map-status="loading"/);
});
