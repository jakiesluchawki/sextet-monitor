# Źródła danych — uruchamiany zakres

Stan adapterów: 27.08.2026. End-to-end weryfikacja w toku; wynik odczytów aplikacji jest w raporcie odbioru Phase 1 (historyczny materiał lokalny, poza repozytorium). Poniższe okresy są lokalną konfiguracją pollingu, **nie deklaracją opóźnienia dostawcy ani SLA**.

Zatwierdzony snapshot researchu (historyczny materiał lokalny, poza repozytorium) i receipty HTTP (historyczny materiał lokalny, poza repozytorium) dotyczą odczytów z 26.08.2026 UTC. Zachowujemy je jako dowód tego sprawdzenia, nie aktualizujemy historycznych wyników wstecz.

| ID adaptera | Produkt i pobieranie w aplikacji | Dostęp / prawa z przeglądu |
|---|---|---|
| `usgs` | GeoJSON ostatniej doby co 5 min; tygodniowe uzupełnienie co około 6 h | Bez klucza; własne dane USGS public domain, zachować pochodzenie partnerów |
| `gdacs` | RSS większych katastrof co 15 min | Bez klucza; atrybucja GDACS i prawa źródeł składowych |
| `meteoalarm` | Atom Polski + CAP co 10 min | Bez klucza; CC BY 4.0, MeteoAlarm/EUMETNET + wystawca |
| `easa_czib` | Oficjalny eksport JSON co 30 min | Bez klucza; reprodukcja z podaniem EASA, z wyjątkami materiałów zastrzeżonych |
| `cisa_kev` | JSON katalogu KEV co 60 min | Bez klucza; repo danych CC0, atrybucja CISA |
| `cloudflare_radar` | Adnotacje outages z ostatnich 7 dni co 5 min, warunkowo | Bearer Radar Read; CC BY-NC 4.0; bez tokenu brak żądań i `needs_credentials` |

## Dokładne endpointy i interpretacja

**USGS**

- Główny feed: [all_day.geojson](https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson).
- Uzupełnienie: [all_week.geojson](https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_week.geojson). Adapter nie realizuje pełnego historycznego backfillu FDSN.
- Lokalna kontrola: gdy `metadata.generated` jest starsze niż 20 minut, odczyt otrzymuje `stale` mimo poprawnego HTTP, chyba że błędy wymagają `partial`. To reguła monitora, nie SLA USGS.
- Publikowany rytm generowania feedu co minutę nie jest czasem od wstrząsu do jego wykrycia. Globalne pokrycie małych wstrząsów jest nierówne. Magnituda, pozycja i identyfikatory mogą być korygowane.
- [Opis formatu](https://earthquake.usgs.gov/earthquakes/feed/v1.0/geojson.php), [zasady praw USGS](https://www.usgs.gov/faqs/are-usgs-reportspublications-copyrighted). Liczbowy limit zapytań tego feedu nie został potwierdzony w przeglądzie.

**GDACS**

- Feed: [rss.xml](https://www.gdacs.org/xml/rss.xml).
- `eventtype + eventid` oznacza zdarzenie; `episodeid` i wersja oznaczają jego kolejne obserwacje. Liczba elementów RSS nie jest liczbą odrębnych aktywnych katastrof. Kolor to modelowany wpływ humanitarny, nie pewność faktu.
- Nie ma jednego potwierdzonego rytmu ani SLA dla wszystkich katastrof; kanał nie zastępuje kompletnego archiwum. Pożary obejmują przede wszystkim duże zdarzenia, a linia GWIS/FIRMS nie jest niezależna od FIRMS. Sejsmologia może powtarzać USGS.
- [Warunki i atrybucja](https://gdacs.org/About/termofuse.aspx), [model pożarów](https://www.gdacs.org/knowledge/models_wf.aspx). Liczbowy rate limit niepotwierdzony.

**MeteoAlarm**

- [Atom Polska](https://feeds.meteoalarm.org/feeds/meteoalarm-legacy-atom-poland). CAP pobierany jest wyłącznie spod oficjalnego odnośnika `application/cap+xml`, np. [próbka historyczna z przeglądu](https://feeds.meteoalarm.org/api/v1/warnings/feeds-poland/90f17002-353a-4686-88d3-0af8cde7f40c).
- Worker obecnie wybiera Polskę; adapter ma listę dopuszczonych krajów, lecz nie jest to automatyczny monitoring całej Europy. Turcji nie ma w użytej liście. Nie przenosimy deklaracji oddzielnego EDR API na Atom.
- Atom nie ma potwierdzonego stałego okna historii ani SLA. Aplikacja sprawdza do 200 unikatowych dokumentów CAP na próbę i jawnie oznacza obcięcie lub błędy. ETag zmniejsza transfer tam, gdzie wspiera go dostawca.
- `Actual` nie znaczy „teraz aktywny”. Liczą się effective/onset/expires i Update/Cancel/References. Języki i powielone linki nie zwiększają liczby zdarzeń. Dla Polski źródłem pierwotnym pozostaje IMGW.
- [Katalog i zasady](https://feeds.meteoalarm.org/), [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/): wskazać MeteoAlarm/EUMETNET, wystawcę, licencję i przetworzenie. Liczbowy limit Atom niepotwierdzony.

**EASA CZIB**

- [Eksport JSON](https://www.easa.europa.eu/en/domains/air-operations/czibs/export-json?_format=json&page=).
- Aktualizacje zależą od publikacji EASA. Eksport zawiera także wpisy nieaktywne, lecz nie gwarantuje pełnej historii rewizji. Numeryczny rate limit/SLA niepotwierdzony.
- To publiczne biuletyny ryzyka wybranych stref, nie NOTAM, pełna mapa zamknięć ani pozycje samolotów. Współrzędne z eksportu nie wyznaczają FIR i nie są mapowane jako dokładny punkt zdarzenia.
- Daty aktualizacji mogą być osadzone w HTML `time`; ważność kalendarzowa jest zachowana z precyzją dnia.
- [Strona CZIB](https://www.easa.europa.eu/en/domains/air-operations/czibs), [copyright EASA](https://www.easa.europa.eu/en/copyright-disclaimer).

**CISA KEV**

- [Oficjalne repozytorium — JSON](https://raw.githubusercontent.com/cisagov/kev-data/develop/known_exploited_vulnerabilities.json).
- Lustro ma synchronizować katalog w ciągu minut po zmianie; to nie SLA aplikacji. Historia repozytorium nie jest automatycznie importowana. Nie przypisujemy raw CDN limitów GitHub REST.
- Wpis potwierdza obecność podatności w KEV według CISA. `dateAdded` jest datą dodania do katalogu, nie datą ataku; `dueDate` jest terminem działania, nie końcem incydentu. Brak geolokalizacji i `occurred_start`; ważność nie jest wymyślanym CVSS.
- [Opis i licencja repo](https://github.com/cisagov/kev-data), [CC0](https://github.com/cisagov/kev-data/blob/develop/LICENSE). Zachowujemy nazwę CISA i źródłowy CVE.

**Cloudflare Radar**

- [Pierwsza strona adnotacji](https://api.cloudflare.com/client/v4/radar/annotations/outages?limit=100&offset=0&dateRange=7d&format=json); kolejne strony przez `offset`, maksymalnie 500 adnotacji na próbę. Oczekiwane pole to `result.annotations`.
- Bez tokenu kod przerywa przed siecią. Test fixture oparty na dokumentacji nie oznacza udanego odczytu po autoryzacji; taki odczyt nie został dotąd potwierdzony.
- W przeglądzie API było bezpłatne na wszystkich planach; konto i token wymagają osobnego działania. Opublikowane limity ogólne: 1200 żądań/5 min na użytkownika wspólnie z innymi tokenami/dashboardem oraz 200/s na IP. Obsługujemy Retry-After; nie zakładamy osobnej puli tylko dla monitora.
- OUTAGE i ANOMALY są różnymi typami materiału. Widoczność Cloudflare nie obejmuje całego Internetu; nazwy `origins` w odpowiedzi nie są niezależnymi dostawcami dowodów. Przyczyna pozostaje przypisaniem Cloudflare.
- Brak potwierdzonego SLA i gwarancji archiwum. [Dokumentacja i dostęp](https://developers.cloudflare.com/radar/), [limity](https://developers.cloudflare.com/fundamentals/api/reference/limits/), [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/). Zakres dotyczy osobistego, niekomercyjnego użycia; nie zakładamy, że użycie firmowe mieści się w nim automatycznie.

## Co pozostaje poza integracją

FIRMS, IMGW hydro/pomiary, EMSC, Open-Meteo/ECMWF, IODA, GDELT, ReliefWeb, NOTAM, AIS/GNSS oraz dane energetyczne i rynkowe są kandydatami ze snapshotu Phase 0 (historyczny materiał lokalny, poza repozytorium), nie uruchomionymi źródłami. Rozszerzenie wymaga sprawdzenia konkretnego produktu, dostępu, praw archiwizacji i rzeczywistego payloadu. Detekcje termiczne FIRMS nie mogą stać się automatycznie „potwierdzonymi pożarami”.

Licencja aplikacji lub biblioteki nie przyznaje prawa do publikacji feedów. Dane i atrybucja pozostają powiązane z konkretnym źródłem; eksport lub użycie komercyjne wymagają osobnego przeglądu.
