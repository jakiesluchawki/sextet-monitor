# Design

Register: product. Implemented in web/app/globals.css.

Scene: Mieszko inspects changing public-source records on his Mac during an evening session with subdued room lighting. The approved dark-first brief uses quiet surfaces and readable text, not decorative effects.

## Tokens

System sans: -apple-system, BlinkMacSystemFont, Segoe UI, system-ui.
Mono only for identifiers, timestamps and raw source data: ui-monospace, SFMono-Regular, Menlo, Consolas.
Dense product scale: 9-12 px metadata, 12-13 px records/body, 16 px application title, 18 px evidence title.
Neutrals are lightly green-tinted OKLCH. background 0.192/0.008/155; surface 0.218/0.008/155; raised 0.253/0.009/155; text 0.926/0.009/145; muted 0.748/0.012/145.
Restrained accent: 0.823/0.073/125. Selection uses 0.29/0.027/140.
State roles: warning 0.82/0.085/78; error 0.78/0.106/28; success 0.805/0.072/155.
Borders 1 px. Control radius 5 px. No gradients, glow, custom fonts or glass surfaces.
Map style uses explicit sRGB equivalents because MapLibre expressions are not CSS tokens. Category hues only identify source categories; severity remains a separate label.

## Structure

Desktop: filters/query rail, map plus results, evidence/briefing/sources panel.
Map/list/timeline consume the identical response. Unknown geometry is not placed at a centroid.
At 900 px and below: collapsible filters, list/map selector and details below.
Keyboard: skip link, native controls, visible focus, selectable records and cluster buttons; the list is the complete alternative to WebGL.

## Truthful states

No sample records or invented counters. Pending, successful-empty, partial, stale, error, credentials-required and disabled are distinct.
Evidence separates source date from retrieval, original severity from interpretation, revisions from first import.
Daily source dates show no fabricated time. Exclusive advisory expiry is displayed as the previous source day, end of day.
Only rule-based queries and briefings are exposed. AI is off; no model/key switch is simulated.
