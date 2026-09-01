import type { Feature, FeatureCollection, Geometry, Point, Position } from "geojson";
import type { EventSummary } from "./contracts";

export interface EventMapProperties {eventId: string; title: string; category: string; severity: number}
export function validPosition(position: Position): boolean {
  return position.length >= 2 && Number.isFinite(position[0]) && Number.isFinite(position[1])
    && Math.abs(position[0]) <= 180 && Math.abs(position[1]) <= 90;
}
export function positionsOf(geometry: Geometry | null): Position[] {
  if (!geometry) return [];
  switch (geometry.type) {
    case "Point": return validPosition(geometry.coordinates) ? [geometry.coordinates] : [];
    case "MultiPoint": case "LineString": return geometry.coordinates.filter(validPosition);
    case "MultiLineString": case "Polygon": return geometry.coordinates.flat().filter(validPosition);
    case "MultiPolygon": return geometry.coordinates.flat(2).filter(validPosition);
    case "GeometryCollection": return geometry.geometries.flatMap(positionsOf);
  }
}

/** Keep provider polygons/lines; never turn missing positions or areas into fabricated centroids. */
export function eventsToGeoJson(events: EventSummary[]): {
  points: FeatureCollection<Point, EventMapProperties>;
  areas: FeatureCollection<Geometry, EventMapProperties>;
} {
  const points: Array<Feature<Point, EventMapProperties>> = [];
  const areas: Array<Feature<Geometry, EventMapProperties>> = [];
  function append(geometry: Geometry, properties: EventMapProperties) {
    if (geometry.type === "GeometryCollection") {
      geometry.geometries.forEach((part) => append(part, properties));
    } else if (geometry.type === "Point") {
      if (validPosition(geometry.coordinates)) points.push({type:"Feature", geometry, properties});
    } else if (geometry.type === "MultiPoint") {
      geometry.coordinates.filter(validPosition).forEach((coordinates) => {
        points.push({type:"Feature", geometry:{type:"Point", coordinates}, properties});
      });
    } else if (positionsOf(geometry).length > 0) {
      areas.push({type:"Feature", geometry, properties});
    }
  }
  for (const event of events) {
    if (!event.geometry) continue;
    append(event.geometry, {eventId:event.id, title:event.title, category:event.category, severity:event.severity});
  }
  return {points:{type:"FeatureCollection", features:points}, areas:{type:"FeatureCollection", features:areas}};
}
