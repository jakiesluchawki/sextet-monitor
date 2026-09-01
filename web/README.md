# Sextet Monitor web

Polish local interface for the approved Phase 1 API. No simulated records, model switch, third-party fonts or remote map tiles.

## Local development

Use the repository's isolated Node 24 runtime, then install from the lockfile with npm ci. Set BACKEND_URL to the local FastAPI origin (for example http://127.0.0.1:8000) and run npm run dev. The development UI binds only 127.0.0.1:3180. Production Compose exposes only the host loopback address.

npm run typecheck checks TypeScript. npm test exercises query alignment, date precision, geospatial omission, source states, safe rendering and the fixed-upstream proxy policy. npm run build produces the standalone Next application. NEXT_TELEMETRY_DISABLED=1 should be set for builds and execution.

The browser calls only /api/* on its own origin. API paths are allowlisted; POST requires X-Monitor-Request: 1 and same-origin JSON. The proxy forwards to a server-configured BACKEND_URL, blocks redirects and limits request/response size and duration.

MapLibre uses public/maps/countries.geojson supplied by the repository owner. Cluster labels use local HTML/system fonts, not remote glyphs. Areas remain source polygons. Unknown locations are not manufactured.

## Verification limits

Unit/static rendering tests do not prove WebGL rendering, keyboard behavior, contrast or live API integration. Those require the parent task's browser and container smoke checks. No dev server is started by the implementation subtask.

## Browser proxy boundary

The proxy is a local-browser request boundary, not user authentication. The loopback-only host binding is still required. Known resource paths and query names are allowed; FastAPI validates value types and ranges. GET is bounded to 15 seconds, POST to 25 seconds, request bodies to 32 KiB and responses to 5 MiB. Redirects, caller cookies and arbitrary forwarded headers are not admitted. A trusted local command can deliberately send the required POST header.

The CSP intentionally permits Next bootstrap inline script/style; source text is rendered through React or textContent, never injected as source HTML. This is not a substitute for the browser integration and network-request audit.

The briefing button always uses 24 hours plus the selected country. Category, region, radius, severity, source-count and the visible event time window do not constrain it. This difference is stated visibly beside the button and in the briefing panel.

MapLibre 6 module workers are prepared by npm run prepare:map (also predev/prebuild/pretest). It copies the pinned package's worker, relative shared module and full license notice unchanged to public/maplibre/. The application sets this same-origin worker URL explicitly; webpack's build-time import.meta.url is not a browser worker URL. No CDN or blob worker is needed. A map that cannot initialize within 20 seconds becomes an explicit list fallback; data-map-status exposes loading/ready/failed for browser checks.

Public browser origins are strictly http://localhost:3180 and http://127.0.0.1:3180, with a matching Host. The internal container URL/port and forwarded headers do not authorize POSTs. Origin-less local GET health checks still work. Country labels are shared static Polish strings to avoid server/browser ICU hydration differences; unmapped codes remain codes.

The Czas fieldset includes the keyboard-accessible Koniec okna number stepper (0–168 hours back, 1-hour increments). A past position writes since/until in UTC to the same EventQuery used by the map, list and timeline, preserving an existing absolute range width or the selected window_hours. Teraz clears the absolute bounds. Warsaw timestamps are visible below the control; current time is read only when the user changes it, never during SSR.
