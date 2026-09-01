# Zależności i materiały zewnętrzne

Sprawdzono lokalne metadane 27.08.2026. **Nie ustalano licencji udzielanej do autorskiego kodu Mieszko Monitor.** Ten dokument nie nadaje aplikacji licencji i nie zastępuje warunków poszczególnych pakietów. Nie dodajemy pliku LICENSE z domyślną licencją projektu.

Aplikacja powstała od zera, bez kopiowania kodu World Monitor/AGPL. Zależności pozostają odrębnymi utworami. Poniższa lista obejmuje główne komponenty; nie jest kompletnym SBOM ani zatwierdzeniem redystrybucji obrazów.

| Komponent / wersja sprawdzona lokalnie | Licencja w metadanych | Miejsce weryfikacji |
|---|---|---|
| FastAPI 0.141.1 | MIT | METADATA zainstalowanego pakietu Python |
| Pydantic 2.13.4 | MIT | METADATA zainstalowanego pakietu Python |
| SQLAlchemy 2.0.52 | MIT | METADATA zainstalowanego pakietu Python |
| HTTPX 0.28.1 | BSD-3-Clause | METADATA zainstalowanego pakietu Python |
| defusedxml 0.7.1 | PSFL | METADATA zainstalowanego pakietu Python |
| Psycopg 3.3.4 | LGPL-3.0-only | METADATA i plik LICENSE pakietu |
| Next.js 16.3.3 / React 19.2.8 | MIT | package.json zainstalowanych pakietów |
| MapLibre GL JS 6.6.0 | BSD-3-Clause | package.json zainstalowanego pakietu |
| Pakiet PostGIS 3.6.4 dla PostgreSQL 17 | GPL-2+ oraz inne warunki dla konkretnych plików | `/usr/share/doc/postgresql-17-postgis-3/copyright` w kontenerze db |

Plik copyright pakietu PostGIS wymienia także m.in. PostgreSQL, BSD, Apache, LGPL i odrębne warunki dokumentacji. Nie należy oznaczać całego obrazu jedną licencją na podstawie nazwy głównego programu. Python, Node, PostgreSQL, biblioteki pośrednie oraz komponenty obrazów systemowych zachowują swoje notices. Wersje przypięto w [requirements.lock](backend/requirements.lock), [package-lock.json](web/package-lock.json) i Dockerfile usług; przy aktualizacji trzeba ponownie sprawdzić właściwe pakiety.

Lokalna mapa używa Natural Earth 1:110m, public domain według [danych pochodzenia](data/map-source.json) i [warunków Natural Earth](https://www.naturalearthdata.com/about/terms-of-use/). Zachowujemy atrybucję, URL, datę pobrania i hash. Dane mapy są uproszczone i nie wyznaczają granic obszarów operacyjnych, takich jak FIR.

Licencje i atrybucja feedów znajdują się w [DATA_SOURCES.md](DATA_SOURCES.md), z odnośnikiem do datowanego przeglądu Phase 0. Licencja biblioteki nie przyznaje praw do źródłowych biuletynów ani danych. W szczególności Radar ma ograniczenie niekomercyjne, a materiały pośredników mogą zachowywać prawa upstreamów.

Przed udostępnieniem aplikacji, obrazów lub eksportu danych trzeba odrębnie ustalić licencję autorskiego kodu i obowiązki wszystkich dystrybuowanych komponentów. Zakres publicznego podglądu, jego osobny przegląd danych i plik THIRD_PARTY_NOTICES opisuje [PUBLIC_PAGES.md](PUBLIC_PAGES.md). Publikacja wymaga jawnego włączenia przez właściciela.
