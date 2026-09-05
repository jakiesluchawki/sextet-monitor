# Źródła danych — uruchamiany zakres

Stan adapterów: 5.09.2026. Jedenaście kanałów bez klucza jest dostępnych dla niezależnego zestawu publicznego; Cloudflare Radar pozostaje dodatkowym adapterem lokalnym wymagającym tokenu. Poniższe okresy dotyczą workera lokalnego. Publiczne Pages planuje wspólny odczyt co godzinę. Żaden z tych okresów **nie jest deklaracją opóźnienia dostawcy ani SLA**.

Zatwierdzony snapshot researchu (historyczny materiał lokalny, poza repozytorium) i receipty HTTP (historyczny materiał lokalny, poza repozytorium) dotyczą odczytów z 26.08.2026 UTC. Zachowujemy je jako dowód tego sprawdzenia, nie aktualizujemy historycznych wyników wstecz.

| ID adaptera | Produkt i pobieranie w aplikacji | Dostęp / prawa z przeglądu |
|---|---|---|
| `usgs` | GeoJSON ostatniej doby co 5 min; tygodniowe uzupełnienie co około 6 h | Bez klucza; własne dane USGS public domain, zachować pochodzenie partnerów |
| `gdacs` | RSS większych katastrof co 15 min | Bez klucza; atrybucja GDACS i prawa źródeł składowych |
| `meteoalarm` | Atom Polski + CAP co 10 min | Bez klucza; CC BY 4.0, MeteoAlarm/EUMETNET + wystawca |
| `easa_czib` | Oficjalny eksport JSON co 30 min | Bez klucza; reprodukcja z podaniem EASA, z wyjątkami materiałów zastrzeżonych |
| `cisa_kev` | JSON katalogu KEV co 60 min | Bez klucza; repo danych CC0, atrybucja CISA |
| `nasa_eonet` | EONET v3: pożary, wulkany i silne burze, okno 30 dni, do 400 rekordów, co 15 min | Bez klucza; kuratorskie metadane z pochodzeniem źródeł, bez obrazów |
| `noaa_swpc` | Alerty, podsumowania, prognozy i ostrzeżenia SWPC, do 400 komunikatów z 30 dni, co 5 min | Bez klucza; własne informacje NOAA/NWS public domain, atrybucja |
| `github_status` | Ostatnie 50 incydentów oficjalnego statusu GitHuba, co 5 min | Bez klucza; wyłącznie metadane faktów i linki, bez komunikatów i postmortemów |
| `cloudflare_status` | Ostatnie 50 incydentów oficjalnego statusu Cloudflare, co 5 min | Bez klucza; wyłącznie metadane faktów i linki; nie jest to Radar |
| `cert_pl` | Ostatnich 10 odsyłaczy RSS dla użytkowników, co 60 min | Bez klucza; neutralny indeks ID/dat/linków bez oryginalnych tytułów i artykułów |
| `imgw_hydro` | Bieżące ostrzeżenia hydrologiczne, do 500, co 15 min | Bez klucza; niezarobkowy cel prywatny, pełna atrybucja i oznaczenie przetworzenia; nie CC |
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
- [Warunki i atrybucja](https://www.gdacs.org/Documents/2025/GDACS_Terms_of_use_Mar_25.pdf), [quickstart API](https://www.gdacs.org/Documents/2025/GDACS_API_quickstart_v2.pdf), [model pożarów](https://www.gdacs.org/knowledge/models_wf.aspx). Dokumentacja [feedów](https://www.gdacs.org/feed_reference.aspx) opisuje aktualizację co 6 minut, co nie stanowi SLA wykrywania zjawisk. Liczbowy rate limit niepotwierdzony. Automatyczne oceny nie zastępują krajowych ostrzeżeń.

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

**NASA EONET**

- [EONET v3 — używane zapytanie](https://eonet.gsfc.nasa.gov/api/v3/events?category=wildfires,volcanoes,severeStorms&status=all&days=30&limit=400), [dokumentacja v3](https://eonet.gsfc.nasa.gov/docs/v3), [przeznaczenie danych](https://eonet.gsfc.nasa.gov/what-is-eonet).
- Pobieramy otwarte i zamknięte wpisy z okna API 30 dni. Osiągnięcie 400 rekordów albo wskazanie kolejnej strony oznacza `partial`; nie wymyślamy nieudokumentowanego `offset` i nie deklarujemy pełnego archiwum. Odpowiedź jest dodatkowo kontrolowana względem dat geometrii i zamknięcia.
- To kuratorski indeks z przybliżonym miejscem i czasem. `occurred_start` jest pierwszą datowaną geometrią, a nie potwierdzonym początkiem zjawiska. Pokazujemy najnowszą geometrię, nie pełny tor; północ oznaczająca nieznaną godzinę ma precyzję dnia. Zamknięcie wpisu przez kuratora nie jest potwierdzoną chwilą fizycznego końca katastrofy.
- Zachowujemy pochodzenie źródeł. Powtórzenie GDACS, USGS albo FIRMS nie jest nowym niezależnym potwierdzeniem NASA. Jednoznaczne źródłowe identyfikatory mogą połączyć dwa kanały w jeden rekord. Nie pobieramy obrazów ani warstw satelitarnych i nie przenosimy na nie praw do metadanych.

**NOAA SWPC**

- [Publiczny feed](https://services.swpc.noaa.gov/products/alerts.json), [opis alertów, prognoz i ostrzeżeń](https://www.swpc.noaa.gov/products/alerts-watches-and-warnings), [skale NOAA](https://www.swpc.noaa.gov/noaa-scales-explanation), [warunki NOAA/NWS](https://www.weather.gov/disclaimer).
- Kategoria `space_weather`. Obserwowane `ALERT`/`SUMMARY` i prognozy `WATCH`/`WARNING` pozostają różnymi rodzajami materiału. Zachowujemy źródłową skalę G/R/S bez automatycznego przeliczenia na lokalne ryzyko. Brak zdefiniowanej skali pozostaje nieznany.
- Czas biuletynu pochodzi z jawnie podanego czasu UTC. Nie traktujemy czasu pobrania jako pomiaru. Przyszłe ostrzeżenie nie jest aktywną obserwacją; zegar ważności może uaktywnić lub wygasić prognozę bez zmiany dowodu i daty jego pobrania.
- Nie przypisujemy krajów ani geometrii do ogólnego tekstu potencjalnych skutków. To nie mapa zakłóceń GNSS. Feed nie gwarantuje pełnego archiwum; ograniczamy import do 400 komunikatów z 30 dni, a ograniczenie liczby pozostaje jawne.

**GitHub Status i Cloudflare Status**

- [GitHub — dane](https://www.githubstatus.com/api/v2/incidents.json), [dokumentacja](https://www.githubstatus.com/api/v2), [warunki API](https://docs.github.com/en/site-policy/github-terms/github-terms-of-service#h-api-terms).
- [Cloudflare — dane](https://www.cloudflarestatus.com/api/v2/incidents.json), [dokumentacja](https://www.cloudflarestatus.com/api), [warunki](https://www.cloudflare.com/policies/terms/).
- Ostatnie 50 incydentów każdego operatora, nie pełna historia. Zniknięcie z listy nie jest sygnałem zakończenia ani wycofania. Data `started_at` jest czasem rozpoczęcia, `created_at` publikacją, `updated_at` aktualizacją i `resolved_at` zakończeniem. Brakujący czas nie jest uzupełniany innym zegarem.
- Stan życia pochodzi z `incident.status`, a waga z deklarowanego `incident.impact`. Bieżący `components[].status` nie opisuje historycznej dotkliwości incydentu i nie jest używany. Waga nie określa skali awarii całego Internetu.
- Zapisujemy wyłącznie ID, nazwę, stan, wpływ, daty i nazwy komponentów oraz odnośnik na oficjalnym hoście. Pełne teksty aktualizacji, postmortemy i dowolne dodatkowe pola są odrzucane także z `raw`. Dostępność publicznego API nie oznacza licencji CC na wszystkie jego teksty. Brak udokumentowanej geometrii pozostaje brakiem geometrii.

**Cloudflare Radar — tylko lokalnie, po konfiguracji**

- [Pierwsza strona adnotacji](https://api.cloudflare.com/client/v4/radar/annotations/outages?limit=100&offset=0&dateRange=7d&format=json); kolejne strony przez `offset`, maksymalnie 500 adnotacji na próbę. Oczekiwane pole to `result.annotations`.
- Bez tokenu kod przerywa przed siecią. Test fixture oparty na dokumentacji nie oznacza udanego odczytu po autoryzacji; taki odczyt nie został dotąd potwierdzony.
- W przeglądzie API było bezpłatne na wszystkich planach; konto i token wymagają osobnego działania. Opublikowane limity ogólne: 1200 żądań/5 min na użytkownika wspólnie z innymi tokenami/dashboardem oraz 200/s na IP. Obsługujemy Retry-After; nie zakładamy osobnej puli tylko dla monitora.
- OUTAGE i ANOMALY są różnymi typami materiału. Widoczność Cloudflare nie obejmuje całego Internetu; nazwy `origins` w odpowiedzi nie są niezależnymi dostawcami dowodów. Przyczyna pozostaje przypisaniem Cloudflare.
- Brak potwierdzonego SLA i gwarancji archiwum. [Dokumentacja i dostęp](https://developers.cloudflare.com/radar/), [limity](https://developers.cloudflare.com/fundamentals/api/reference/limits/), [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/). Zakres dotyczy osobistego, niekomercyjnego użycia; nie zakładamy, że użycie firmowe mieści się w nim automatycznie.

## Co pozostaje poza integracją

Bezpośrednie FIRMS, pomiary rzek IMGW, EMSC, Open-Meteo/ECMWF, IODA, GDELT, ReliefWeb, NOTAM, AIS/GNSS oraz dane energetyczne i rynkowe pozostają kandydatami, nie uruchomionymi bezpośrednio źródłami. FIRMS może występować jako pochodzenie wpisu GDACS/EONET. Rozszerzenie wymaga sprawdzenia konkretnego produktu, dostępu, praw archiwizacji i rzeczywistego payloadu. Detekcje termiczne nie mogą stać się automatycznie „potwierdzonymi pożarami”. Pomiary rzek IMGW wymagają między innymi potwierdzenia strefy czasowej i interpretacji stanu stacji; ostrzeżenia hydrologiczne są już podłączone i opisane poniżej.

Licencja aplikacji lub biblioteki nie przyznaje prawa do publikacji feedów. Dane i atrybucja pozostają powiązane z konkretnym źródłem; eksport lub użycie komercyjne wymagają osobnego przeglądu.

## Źródła dodane w wersji 03

**CERT Polska**

- [Publiczny RSS dla użytkowników](https://moje.cert.pl/advisory_feed/advisory/feed/?category=1), oferowany w [oficjalnym komunikacie CERT](https://cert.pl/posts/2025/05/moje.cert.pl-powiadomienia/). Limit 10 ostatnich pozycji i 512 KiB; to nie archiwum.
- Publikujemy datę, stabilne ID rok/numer, odsyłacz i własny neutralny tytuł. Bez oryginalnych tytułów, opisów, obrazów i treści artykułów. [Regulamin NASK](https://moje.cert.pl/terms/) nie jest otwartą licencją, dlatego nie oznaczamy danych jako CC.
- Rodzaj advisory, kategoria cyber. Data RSS to publikacja, nie data ataku. Brak oceny dotkliwości, kraju oddziaływania i okresu ważności. Komunikaty są widoczne w obszarze Świat; polski wydawca nie wyznacza geograficznego zasięgu zagrożenia.

**IMGW Hydrologia**

- Oficjalna [mapa i lista ostrzeżeń](https://hydro.imgw.pl/#/warnings/hydro) używa [getCurrentWarnings](https://hydro-back.imgw.pl/alerts/warnings/hydro/getCurrentWarnings). Daty releaseDate/dateFrom/dateTo zawierają offset UTC. Starszy endpoint warningshydro z datami bez strefy nie jest fallbackiem.
- ID komunikatu pozostaje źródłowe; referenceDate nie jest ID poprzednika. Aktualizacje bez jednoznacznego poprzednika mają jawny tag i opis, bez zgadywanego scalenia. To ograniczenie historii, nie niekompletność bieżącej listy.
- Ważność do odwołania zachowuje isUntilRevoke; kod suszy -1 nie jest stopniem zerowego zagrożenia. Stopnie 1/2/3 mapują się na 2/3/4 z podaniem oryginału. Bez fikcyjnych poligonów i bez importowania stacji jako incydentów.
- IMGW jest też pochodzeniem polskich komunikatów MeteoAlarm. Dwa kanały nie stanowią niezależnego potwierdzenia.
- [Regulamin hydrologiczny](https://hydro.imgw.pl/#/regulamin) oraz [warunki danych IMGW](https://danepubliczne.imgw.pl/apiinfo): obecny projekt jest niezarobkowy i nie zawiera reklam. Zachowujemy pełną atrybucję i oznaczenie przetworzenia. Nie deklarujemy CC ani prawa do zastosowań komercyjnych lub specjalistycznych.
- Bieżąca lista nie jest archiwum. W instalacji lokalnej zniknięcie otwartego ostrzeżenia z kompletnego odczytu zmienia stan na nieustalony, nie odwołany; ostatnia obserwacja pozostaje z pierwotną datą. Błąd, niekompletna lub starsza lista nie uruchamia tej zmiany.

**IODA — niepublikowany pilotaż**

Sprawdzono ograniczony zestaw zapytań do oficjalnego API. Historyczny pomiar Ukrainy był niepusty, ale listy zdarzeń dla UA i PL nie potwierdziły gotowego feedu incydentów. Nie wyjaśniono retencji i progów tego endpointu ani aktualnych warunków redystrybucji danych Georgia Tech. Sam dostęp HTTP i licencja kodu nie wystarczają do publikacji danych. Adapter nie jest włączony ani wliczany do źródeł.
