# Provider parser fixtures

These files are read by tests only. The application never loads fixtures as live data.

USGS, GDACS, EASA, and the expired IMGW/MeteoAlarm CAP retain small examples of
the public formats observed on 2026-08-26. Unneeded fields were omitted.
The Atom file repeats a CAP link to exercise language/area deduplication.
The CISA row is explicitly synthetic while retaining the official KEV schema.
The Radar fixture follows the public API example; it is not an authenticated
live response and makes no claim about an actual current outage.

Tests derive updates, cancellations and malformed records in memory.

Format sources and attribution:
- USGS: https://earthquake.usgs.gov/earthquakes/feed/v1.0/geojson.php — USGS/public domain.
- GDACS: https://www.gdacs.org/xml/rss.xml — GDACS; public attribution requirements.
- MeteoAlarm: https://feeds.meteoalarm.org/ — MeteoAlarm/EUMETNET and IMGW-PIB, CC BY 4.0; shortened for tests.
- EASA: https://www.easa.europa.eu/en/domains/air-operations/czibs/export-json?_format=json&page= — EASA.
- CISA: https://github.com/cisagov/kev-data — schema; data repo CC0.
- Radar: https://developers.cloudflare.com/api/resources/radar/subresources/annotations/subresources/outages/methods/get/ — Cloudflare API schema/example.
