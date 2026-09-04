import type { Geometry, Position } from "geojson";
import type { EventSummary } from "./contracts";
import { eventsToGeoJson, positionsOf, validPosition } from "./map-data";

export type MapProjection = "globe" | "mercator";
export type CameraBounds = [[number, number], [number, number]];
export const CAMERA_PRESETS = [
  { id: "world", label: "Świat", bounds: null },
  { id: "europe", label: "Europa", bounds: [[-25, 33], [45, 72]] as CameraBounds },
  { id: "poland", label: "Polska", bounds: [[13.5, 48.5], [24.8, 55.3]] as CameraBounds },
  { id: "turkey", label: "Turcja", bounds: [[25, 35.5], [45, 42.5]] as CameraBounds },
] as const;
export type CameraScope = typeof CAMERA_PRESETS[number]["id"];

/** Longitude ranges follow connected source paths. A broad polygon must not be
 * shrunk across the dateline simply because its corner points are close there. */
function longitudeRanges(geometry: Geometry): Array<[number, number]> {
  const range = (positions: Position[]): Array<[number, number]> => {
    let west = Infinity, east = -Infinity;
    for (const position of positions) {
      if (validPosition(position)) { west = Math.min(west, position[0]); east = Math.max(east, position[0]); }
    }
    return Number.isFinite(west) ? [[west, east]] : [];
  };
  switch (geometry.type) {
    case "Point": return range([geometry.coordinates]);
    case "MultiPoint": return geometry.coordinates.flatMap((position) => range([position]));
    case "LineString": return range(geometry.coordinates);
    case "MultiLineString": case "Polygon": return geometry.coordinates.flatMap(range);
    case "MultiPolygon": return geometry.coordinates.flatMap((polygon) => polygon.flatMap(range));
    case "GeometryCollection": return geometry.geometries.flatMap(longitudeRanges);
  }
}

/** Camera bounds only. Never writes an event location, centroid or geometry.
 * The globe can fit the shortest continuous longitude span. A flat single-world
 * map retains the full original span so records cannot disappear past its edge. */
export function geometryCameraBounds(geometries: Array<Geometry | null>, projection: MapProjection = "mercator"): CameraBounds | null {
  const positions = geometries.flatMap(positionsOf);
  if (!positions.length) return null;
  let south = 90, north = -90;
  for (const [, lat] of positions) { south = Math.min(south, lat); north = Math.max(north, lat); }
  const ranges = geometries.flatMap((geometry) => geometry ? longitudeRanges(geometry) : []);
  ranges.sort((a, b) => a[0] - b[0]);
  const merged: Array<[number, number]> = [];
  for (const [start, end] of ranges) {
    const last = merged[merged.length - 1];
    if (last && start <= last[1]) last[1] = Math.max(last[1], end);
    else merged.push([start, end]);
  }
  let west = merged[0][0], east = merged[merged.length - 1][1];
  if (projection === "globe") {
    let largestGap = west + 360 - east;
    for (let i = 0; i < merged.length - 1; i++) {
      const gap = merged[i + 1][0] - merged[i][1];
      if (gap > largestGap) {
        largestGap = gap;
        west = merged[i + 1][0];
        east = merged[i][1] + 360;
      }
    }
    if ((west + east) / 2 > 180) { west -= 360; east -= 360; }
  }
  // fitBounds uses Mercator camera calculations even when the globe is shown.
  // Clamp the camera, not the source's actual latitude, at Mercator's poles.
  const limit = 85.0511287798066;
  return [[west, Math.max(-limit, Math.min(limit, south))], [east, Math.max(-limit, Math.min(limit, north))]];
}

export function mapGeometryCoverage(events: EventSummary[]) {
  const data = eventsToGeoJson(events);
  const pointRecords = new Set(data.points.features.map((feature) => feature.properties.eventId));
  const areaRecords = new Set(data.areas.features.map((feature) => feature.properties.eventId));
  const mapped = new Set([...pointRecords, ...areaRecords]);
  return {
    total: events.length,
    mapped: mapped.size,
    pointRecords: pointRecords.size,
    areaOnlyRecords: [...areaRecords].filter((id) => !pointRecords.has(id)).length,
    unlocated: events.filter((event) => !mapped.has(event.id)).length,
  };
}

export function cameraAnimationDuration(reducedMotion: boolean): number { return reducedMotion ? 0 : 240; }

export function worldCameraZoom(width: number, height: number): number {
  return Math.max(-2, Math.min(1.5, Math.log2(Math.max(1, Math.min(width, height)) / 180)));
}

/** Resize may refit an explicitly selected world view, never a user's camera. */
export function worldCameraResizeZoom(projection: MapProjection, preset: string | null, width: number, height: number): number | null {
  if (projection !== "globe" || preset !== "world" || !Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) return null;
  return worldCameraZoom(width, height);
}
