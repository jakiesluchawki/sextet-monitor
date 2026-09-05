# Sextet Monitor web

Polish interfaces for two separate runtimes: the local Phase 1 API application and the static public edition 03. No simulated records, cloud AI, model switch, third-party fonts or remote map tiles. Edition 03 adds CERT Polska facts and links plus IMGW hydrological warnings (eleven public channels). Records without the required source clock are listed separately and excluded from time counts, maps and briefings.

## Public edition 02

`PublicMonitor` reads the same-origin `snapshot.json` through the bounded public validator. It does not import the private API client or use the local database. `npm run build:pages` exports only the allowlisted public components, utilities and assets into `.pages-build/out`; the default base path is `/sextet-monitor`. Preparation and publication are documented in [PUBLIC_PAGES](../PUBLIC_PAGES.md). A successful export does not prove that production has been deployed.

The public interface has overview, exploration and briefing views. `situation.ts` evaluates world/Europe/Poland or a validated country/territory and the 24-hour, 72-hour and 7-day windows at `snapshot.generated_at`. Europe is an explicit list of source country codes, including Cyprus and Kosovo but not Russia or Turkey; neither this filter nor the country scopes are spatial intersections. Unknown-country records and global services are not automatically assigned to a region.

Occurrence start, publication and validity overlap remain separate by category. Device time updates age warnings, not the data window. Day-precision records and advisories can occur in several timeline intervals; the accessible table and labels explain that totals across intervals are not unique incidents. Overview highlights choose up to eight representatives using disclosed source/date rules, not AI or a risk score.

`public-view.ts` searches literal words in public titles, descriptions, country labels and source names, with case and Polish-diacritic normalization. Search is capped at 200 characters. Source/category filters apply locally. The list reveals results in batches of 60; the map has an explicit 500-record cap and preserves unknown geometry. `DetailedExplorer` retains the previous exact filters, time basis and shifted-window controls, still without a private API connection.

### Browser-local state and links

`public-session.ts` and `areas.ts` own the following versioned, bounded storage entries:

| Key | Contents | Boundary |
|---|---|---|
| `sextet.public.areas.v1` | ISO alpha-2 country codes plus XK | At most eight favorites and 1 KiB; no names, coordinates or history |
| `sextet.public.watch.v1` | Public UUIDs and last-write timestamp | At most 30 pins; no titles, text or geometry |
| `sextet.public.baseline.v1` | Publication timestamp and UUID/content-fingerprint pairs | At most 10,000 entries and 1 MiB; no full events, evidence payloads or URLs |

Baseline fingerprints are compact, non-cryptographic summaries of selected source content, timestamps, severity, state, geography and provenance. They are not signatures or authenticity checks. Retrieval/import clocks, payload hashes, derived relations and a transport-cache flag are excluded so a fresh build of unchanged sources is not called a change. The first read can establish a baseline; the user can advance it with “Zapamiętaj obecny zestaw”. A newer file alone does not silently discard the saved comparison point. Older or conflicting equal-time baselines cannot deliberately replace a newer saved reference.

Comparison reports first visit, same publication, newer publication or out-of-order publication. “Added” means new to these two public sets, not a confirmed new incident. “Missing” is not “resolved”; source windows, caps and grouping can remove a record. This is a two-set comparison, not full source history. Browser storage may be blocked, exhausted or cleared. Cross-tab storage notifications re-read current storage rather than replaying a stale event value; `localStorage` still offers no atomic cross-tab transaction or device synchronization. Briefing pin management includes unavailable records so their slots can be released.

Shared hashes contain approved scope/window/view/filter/search fields and an optional validated public event UUID, within a 2,048-character limit. `buildShareUrl` strips the previous query/hash and rejects non-HTTP(S) or credential-bearing base addresses. Explicit search text becomes part of the link; do not put secrets into search. Links open the latest available public set, not an immutable snapshot, and carry neither pins, favorite areas nor baseline. Country scopes use `country:XX`; the legacy `turkey` value is restored as `country:TR`, and `country:PL` becomes `poland`. State restoration happens after mounting to preserve deterministic SSR output.

### Briefing and map behavior

Public briefings are deterministic text built from highlights or in-scope pins, capped at 12 records, with source links, clocks and limitations. Copy-to-Signal only writes to the clipboard; the user must paste and send. Clipboard refusal exposes a manual-copy field. Markdown download and browser print/PDF are explicit local actions, with no messaging API or notification permission.

`EventMap` supports globe and Mercator projection, camera presets and fitting the available source geometry. Camera controls do not alter data filters or manufacture coordinates. MapLibre and the Natural Earth underlay are same-origin assets. Unknown geometry remains on the list. Reduced-motion preferences suppress camera animation; a WebGL failure exposes the list alternative. Browser checks must confirm `data-map-status` and visible behavior rather than treating a loaded JavaScript bundle as a rendered map.

## Local development

For the private application, use the repository's isolated Node 24 runtime, then install from the lockfile with `npm ci`. Set `BACKEND_URL` to the local FastAPI origin (for example `http://127.0.0.1:8000`) and run `npm run dev`. The development UI binds only `127.0.0.1:3180`. Production Compose exposes only the host loopback address. Public export does not use `BACKEND_URL`.

`npm run typecheck` checks TypeScript. `npm test` runs `tests/*.test.ts`, covering query alignment, date precision, source states, public situation/selection rules, literal search, map geometry/camera, storage and link safety, rendering and the fixed-upstream proxy policy. `npm run build` produces the standalone Next application; `npm run build:pages` produces the static public artifact. `NEXT_TELEMETRY_DISABLED=1` should be set for builds and execution.

In the private application only, the browser calls `/api/*` on its own origin. API paths are allowlisted; POST requires `X-Monitor-Request: 1` and same-origin JSON. The proxy forwards to a server-configured `BACKEND_URL`, blocks redirects and limits request/response size and duration.

MapLibre uses public/maps/countries.geojson supplied by the repository owner. Cluster labels use local HTML/system fonts, not remote glyphs. Areas remain source polygons. Unknown locations are not manufactured.

## Verification limits

Unit/static rendering tests do not prove WebGL rendering, keyboard behavior, contrast, live API integration or a deployed Pages release. Verify the built artifact in desktop/mobile browsers: scope/source/search results and evidence, map/list fallback, restored hashes, pin persistence and management, cross-tab updates, out-of-order baselines and copy/print behavior. Check actual snapshot dates and source metadata; do not assume fixed record counts or a fixed number of partial sources. No formal 72-hour soak or live freshness SLA is claimed.

## Private browser proxy boundary

The proxy is a local-browser request boundary, not user authentication. The loopback-only host binding is still required. Known resource paths and query names are allowed; FastAPI validates value types and ranges. GET is bounded to 15 seconds, POST to 25 seconds, request bodies to 32 KiB and responses to 5 MiB. Redirects, caller cookies and arbitrary forwarded headers are not admitted. A trusted local command can deliberately send the required POST header.

The CSP intentionally permits Next bootstrap inline script/style; source text is rendered through React or textContent, never injected as source HTML. This is not a substitute for the browser integration and network-request audit.

The private API briefing button always uses 24 hours plus the selected country. Category, region, radius, severity, source-count and the visible event time window do not constrain it. This difference is stated visibly beside the button and in the briefing panel. This rule does not describe the public edition's local briefing, which uses the current public scope and window.

MapLibre 6 module workers are prepared by npm run prepare:map (also predev/prebuild/pretest). It copies the pinned package's worker, relative shared module and full license notice unchanged to public/maplibre/. The application sets this same-origin worker URL explicitly; webpack's build-time import.meta.url is not a browser worker URL. No CDN or blob worker is needed. A map that cannot initialize within 20 seconds becomes an explicit list fallback; data-map-status exposes loading/ready/failed for browser checks.

The private proxy's allowed browser origins are strictly `http://localhost:3180` and `http://127.0.0.1:3180`, with a matching Host. The internal container URL/port and forwarded headers do not authorize POSTs. Origin-less local GET health checks still work. These POST rules do not expose a private API on GitHub Pages. Existing event labels remain shared static Polish strings. The public country picker uses an explicit validated code list with browser-local Polish Intl labels and sorting only after hydration; unsupported labels fall back to static names or codes.

The Czas fieldset includes the keyboard-accessible Koniec okna number stepper (0–168 hours back, 1-hour increments). A past position writes since/until in UTC to the same EventQuery used by the map, list and timeline, preserving an existing absolute range width or the selected window_hours. Teraz clears the absolute bounds. Warsaw timestamps are visible below the control; current time is read only when the user changes it, never during SSR.
