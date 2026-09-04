import type { GeoJSON, Geometry, Position } from "geojson";
import type { EventSummary } from "./contracts";
import type { ScopeId } from "./areas";
import { eventsToGeoJson, positionsOf, validPosition } from "./map-data";

export type MapProjection = "globe" | "mercator";
export type CameraBounds = [[number, number], [number, number]];
export const CAMERA_PRESETS = [
  { id: "world", label: "Świat", bounds: null },
  { id: "europe", label: "Europa", bounds: [[-25, 33], [45, 72]] as CameraBounds },
  { id: "poland", label: "Polska", bounds: null },
] as const;
export type CameraScope = ScopeId;

/** Camera metadata is optional. A hung/rejected source read must not disable
 * an already rendered map, and a late result must not move the reader's view. */
export function loadCountryBoundaries(read: () => Promise<GeoJSON>, timeoutMs = 2_000): Promise<GeoJSON | null> {
  return new Promise((resolve) => {
    let settled = false;
    const finish = (data: GeoJSON | null) => {
      if (settled) return;
      settled = true;
      clearTimeout(deadline);
      resolve(data);
    };
    const deadline = setTimeout(() => finish(null), timeoutMs);
    try { void read().then(finish, () => finish(null)); }
    catch { finish(null); }
  });
}

/** Natural Earth uses ISO_A2_EH for some countries whose ISO_A2 is -99.
 * Never infer a country from POSTAL, labels, sovereigns or label coordinates. */
export function naturalEarthCountryGeometries(data: GeoJSON | null, countryCode: string): Geometry[] {
  if (!/^[A-Z]{2}$/.test(countryCode) || !data) return [];
  const features = data.type === "FeatureCollection" ? data.features : data.type === "Feature" ? [data] : [];
  return features.flatMap((feature) => {
    const properties = feature.properties;
    const primary = properties?.ISO_A2;
    const fallback = properties?.ISO_A2_EH;
    const code = typeof primary === "string" && /^[A-Z]{2}$/.test(primary) ? primary
      : typeof fallback === "string" && /^[A-Z]{2}$/.test(fallback) ? fallback : null;
    const geometry = feature.geometry;
    return code === countryCode && geometry && (geometry.type === "Polygon" || geometry.type === "MultiPolygon")
      && positionsOf(geometry).length > 0 ? [geometry] : [];
  });
}

export interface CountryCameraTarget {
  kind: "boundary" | "records" | "world";
  bounds: CameraBounds | null;
  projection: MapProjection;
}

/** Fit the original country boundary, or explicitly fall back to source events.
 * Only records carrying the requested country code qualify for that fallback. */
export function countryCameraTarget(countryCode: string, data: GeoJSON | null, events: EventSummary[], projection: MapProjection): CountryCameraTarget {
  const boundary = naturalEarthCountryGeometries(data, countryCode);
  const geometries = boundary.length ? boundary : events.filter((event) => event.countries.includes(countryCode)).map((event) => event.geometry);
  let bounds = geometryCameraBounds(geometries, projection);
  if (!bounds) return { kind: "world", bounds: null, projection };
  let mode = projection;
  if (projection === "globe" && (bounds[1][0] - bounds[0][0] > 160 || bounds[1][1] - bounds[0][1] > 140)) {
    mode = "mercator";
    bounds = geometryCameraBounds(geometries, mode);
  }
  return { kind: boundary.length ? "boundary" : "records", bounds, projection: mode };
}

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
