# Design

Register: product. Public edition 02 lives in `web/app/sextet.css`, with shared tokens and the private/detailed workspace in `web/app/globals.css`. Map controls are scoped in `web/components/map-experience.css`.

Scene: Members of a small group inspect changing public-source records on their computers and phones, including evening sessions with subdued room lighting. The approved dark-first brief uses quiet surfaces and readable text, not decorative effects.

## Tokens

System sans: -apple-system, BlinkMacSystemFont, Segoe UI, system-ui.
Mono only for identifiers, timestamps and raw source data: ui-monospace, SFMono-Regular, Menlo, Consolas.
The public edition has a larger page heading, readable record titles and compact supporting metadata. Exact sizes and responsive rules are defined in the stylesheets rather than duplicated here.
Neutrals are lightly green-tinted OKLCH, with a restrained green accent for selection. Warning, error and success colors describe source/operation state, not a computed safety level. Source-state labels remain visible alongside color.
Thin borders and quiet surfaces separate controls and evidence. No gradients, glow, downloaded fonts or glass surfaces. The briefing uses a light, document-like surface with a print stylesheet.
Map style uses explicit sRGB equivalents because MapLibre expressions are not CSS tokens. Category hues only identify source categories; severity remains a separate label.

## Structure

Public navigation has three views: overview, exploration and briefing. A shared rail chooses world, Europe, Poland or an explicit country/territory and a 24-hour, 72-hour or 7-day window. A native country select offers ISO alpha-2 codes plus XK; the member can keep up to eight favorite areas only in this browser. Labels and sorting appear after hydration. Turkey has no special shortcut; historical turkey hashes still restore its country filter. The header shows the publication clock and source health; evidence and source details open separately from the main task.

The overview pairs a globe with up to eight rule-selected records and their selection reasons. It also exposes a timeline and an accessible table with exact interval counts. Exploration pairs literal search and source/category filters with progressively loaded records and a map. A narrow screen keeps records available and lets the user reveal the map. Evidence can be opened from either representation.

The overview and timeline calculate over the complete matching public set. The map deliberately uses at most 500 matching records and states that boundary; the exploration list makes the full result available in batches. Missing geometry never becomes a centroid or invented point. Globe/2D selection and camera presets alter presentation, not event geography or scope filters. Choosing a country updates the data scope and frames its local underlay boundary. Missing boundaries fall back to actual matching event geometry, or a world view with an explicit message when neither is available. The list remains the complete alternative when WebGL fails.

The older exact-filter workflow remains in `DetailedExplorer`; its map, list and timeline still consume the same bounded response. The private workspace keeps its existing API workflow and is not replaced by the public edition.

Keyboard: skip link, native controls, visible focus, selectable records, source links and map controls. `/` focuses public search; Escape closes public details and manual-copy UI. Reduced-motion preferences suppress map camera animation. The timeline's data table avoids dependence on hover, bar height or color.

## Source and time semantics

Edition 03 uses eleven public channels, including a facts-and-links index from CERT Polska and IMGW hydrological warnings. Channel count never implies independent confirmation; IMGW also originates Polish MeteoAlarm data. Undated records remain visible in a separate list without entering time counts, maps or time-bounded briefings. The overview selects category representatives using source dates and documented source severity, with limited repetition; it does not compute a threat ranking, anomaly score or cause.

Scope uses explicit source country codes. Europe is the application's declared country list, not a geometry intersection; countryless and global records are not silently assigned to a region. The data window ends at the snapshot's publication clock. The device clock only affects freshness messages.

Earthquakes/disasters use occurrence start, weather/aviation use validity overlap, and cyber/internet/space weather use publication. A validity period or day-precision date may appear in multiple timeline bins. Labels and the table explain why summing bars does not give a unique incident count. Source acquisition time is never substituted for a missing event time.

## Local actions and sharing

Star controls keep at most 30 public UUIDs in this browser. Briefing management includes unavailable and out-of-scope pins so an expired reference cannot trap a slot. A browser-local baseline stores publication time and non-cryptographic content fingerprints, not event text or a history archive. Re-import clocks do not count as changes; missing records do not mean resolved incidents. Storage errors and out-of-order publications must be described explicitly.

Briefings use the monitor's selection or in-scope pins, with a 12-record cap, source links, dates and limitations. Copying prepares text for a user to paste into Signal; it never sends a message. Markdown download and browser print/PDF are explicit actions. A clipboard failure exposes selectable text.

Shared hashes carry approved public view settings and an optional public event ID. They do not carry local pins, favorite areas or baseline state. Links open the latest available publication, so an old ID may no longer exist; the detail panel must say so rather than invent an archive. No cloud AI or private API backs these public interactions.

## Truthful states

No sample records or invented counters. Pending, successful-empty, partial, stale, error, credentials-required and disabled are distinct.
Evidence separates source date from retrieval, original severity from interpretation, revisions from first import.
Daily source dates show no fabricated time. Exclusive advisory expiry is displayed as the previous source day, end of day.
The public briefing and local comparison are rule-based and distinct from the private application's query/history features. AI is off; no model/key switch is simulated. Scheduled publication is not a live-data guarantee.

## Verification boundary

This document describes intended and implemented behavior, not proof that edition 02 is deployed. Unit tests cover selection, clocks, filtering, camera bounds, storage and link policy. Desktop/mobile browser checks must still verify actual map rendering or fallback, keyboard focus, copied text, reloads and links against the built artifact. No formal 72-hour soak is claimed.
