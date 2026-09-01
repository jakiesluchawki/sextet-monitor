# Architektura Mieszko Monitor

Stan kodu: 27.08.2026. End-to-end weryfikacja w toku; pełny status odbioru w raporcie odbioru Phase 1 (historyczny materiał lokalny, poza repozytorium). To opis istniejącej implementacji, nie lista wszystkich funkcji z pierwotnego briefu.

## Cztery usługi

~~~mermaid
flowchart LR
    Browser["Przeglądarka · localhost:3180"] --> Web["web · Next.js / MapLibre"]
    Web --> API["api · FastAPI"]
    API --> DB[("db · PostgreSQL / PostGIS")]
    Sources["Sześć zatwierdzonych źródeł"] --> Worker["worker · pobieranie i normalizacja"]
    Worker --> DB
    Maps["Lokalne granice Natural Earth"] --> Web
    Maps --> DB
~~~

`web` udostępnia UI i kontrolowane proxy `/api/*`. Przeglądarka nie pobiera feedów bezpośrednio. `api` obsługuje odczyty, ograniczone pytania i zapis briefingów; `worker` pobiera źródła niezależnie od otwartej przeglądarki. `db` zawiera dane oraz harmonogram, bez Redis/Kafka.

Wersje bazowe w Dockerfile: Python 3.13.13, PostgreSQL 17.11 z PostGIS 3.6.4, Node 24.19.0. Obraz Node przypięto także do digestu; nie wszystkie obrazy bazowe są przypięte do digestów. Zależności backendu zapisuje `requirements.lock`, frontend ma `package-lock.json`. Next.js, React i MapLibre są dostarczane lokalnie.

Podkład to uproszczone granice Natural Earth 1:110m, bez ulic, zdalnych kafli i fontów. Pochodzenie, data i SHA-256 są w [map-source.json](data/map-source.json). Taki podkład nie służy do nawigacji ani wyznaczania granic FIR.

## Przepływ danych i zapis

1. Worker otrzymuje dzierżawę źródła z bazy; konkurencyjny worker nie może przejąć aktywnej dzierżawy.
2. Transport wykonuje dozwolone żądania HTTPS poza transakcją zapisu. Obecnie do trzech źródeł pracuje równolegle, a wspólny klient ogranicza HTTP do czterech połączeń.
3. Adapter zwraca `ProviderBatch`: poprawne rekordy, liczbę odrzuceń, ostrzeżenia i metadane niepełności. Nieprawidłowy format nie udaje pustego zbioru.
4. Zapis sprawdza właściciela i termin dzierżawy po uzyskaniu blokady. Wynik spóźnionego workera nie publikuje zmian.
5. Oryginalny rekord, jego normalizacja i hash tworzą obserwację. Reguły tożsamości wiążą ją z aktualnym zdarzeniem. Zmiana stanu zdarzenia tworzy rewizję.
6. Dopiero po zapisie aktualizowane są stan źródła i termin kolejnej próby. Odczyt częściowy zachowuje sukcesy, lecz nie przesuwa kursora pełnego pobrania.

Klucz idempotencji obejmuje źródło, identyfikator rekordu, hash payloadu oraz wersję normalizatora. Korekta parsera może więc utworzyć nową normalizację bez nadpisania poprzedniej. `provider_records.latest_observation_id` wskazuje aktualny materiał; późny, starszy epizod GDACS nie zastępuje go samym czasem pobrania. Szczegóły wyświetlają właśnie wskazany materiał, nie dowolny ostatnio pobrany wiersz.

CAP Update/Cancel używa referencji tego samego nadawcy. Dwa języki jednego CAP nie tworzą dwóch dowodów. Relacja `supersedes` ma pierwszeństwo przed kolejnością odbioru, a Cancel oznacza wycofanie. Dane surowe są materiałem, nigdy instrukcjami do wykonania.

## Model

| Obiekt | Rola |
|---|---|
| `sources`, `ingestion_runs` | Dostęp, pochodzenie, harmonogram, próby, błędy i dzierżawy |
| `observations` | Wersje payloadu i normalizacji wraz z czasem pobrania |
| `provider_records` | Aktualna obserwacja danego identyfikatora dostawcy |
| `events` | Bieżąca projekcja zdarzenia lub komunikatu |
| `event_evidence`, `event_external_ids` | Materiał źródłowy i twarde powiązania tożsamości |
| `identity_overrides` | Jawne decyzje administracyjne o przypisaniu obecnych rekordów po rozdzieleniu |
| `event_revisions` | Historia zmian projekcji zdarzenia |
| `event_relations` | Jawne związki czasu i miejsca bez automatycznego scalenia |
| `countries` | Lokalne geometrie krajów |
| `briefing_runs` | Zapisane briefingi i ich zakresy/kursory |

Typy rekordu to `incident`, `advisory`, `vulnerability_notice`, `measurement`. Wartość `severity=0` oznacza nieokreśloną ważność, nie brak skutków; `anomaly_score` pozostaje null. Skala ważności jest opisana przy rekordzie i nie jest prawdopodobieństwem ani porównywalnym pomiarem szkód.

Tożsamość opiera się na identyfikatorach dostawców, jawnych identyfikatorach źródłowych i referencjach CAP. Bliskość czasu/miejsca może stworzyć `possible_same_event`, ale nie scala zdarzeń ani nie zwiększa liczby potwierdzeń. Sprzeczne twarde ID wymagają oceny. Administracyjne `detach-source` rozdziela obecne rekordy wybranego źródła po podglądzie, automatycznej kopii i podaniu powodu. Zapis ponownie sprawdza przypisania pod wspólną blokadą, zachowuje obserwacje i wcześniejsze rewizje oraz tworzy `identity_split`. `identity_overrides` chroni obecne rekordy obu stron przed ponownym automatycznym scaleniem; nie obejmuje nieznanych przyszłych identyfikatorów. To narzędzie CLI, bez endpointu zapisu przez HTTP.

Niezależność liczona jest po pochodzeniu: sieci `usgs:*` należą do jednej rodziny; GWIS/FIRMS nie stanowią kilku niezależnych kanałów. Jeden raport wymieniający kilku niejednoznacznych upstreamów nie daje kilku potwierdzeń. `unknown:gdacs` nie wnosi dodatkowego potwierdzenia; pojedynczy raport nadal pozostaje jednym raportem.

## Czas, obszar i zapytania

Baza przechowuje czasy ze strefą, normalizowane do UTC. Oddzielne pola oznaczają czas zjawiska, publikacji, korekty, pobrania oraz ważności. Domyślna prezentacja i interpretacja lokalnych godzin używają Europe/Warsaw. Przedziały zapytań są domknięte z lewej i otwarte z prawej: `since <= czas < until`.

Data bez godziny ma precyzję dnia i tag `date_only_utc_anchor`. Dzienna ważność EASA jest zapisana do następnej północy UTC wyłącznie; UI powinno pokazać koniec poprzedniego dnia, nie pozorną godzinę źródłową. Brak czasu lub współrzędnych pozostaje NULL.

Punkty USGS zachowują WGS84 lon/lat, głębokość zostaje w materiale źródłowym. Reprezentatywny punkt GDACS nie wyznacza zasięgu katastrofy. EASA nie tworzy fikcyjnego punktu FIR: znane kraje mogą otrzymać lokalne obszary krajowe z jawnym tagiem. PostGIS liczy promienie w metrach; filtr odległości pomija geometrię krajową i punkty reprezentatywne, a uwzględnia źródłowe punkty/obszary.

Mapa, lista i pytania używają `EventQuery`. Tryb `occurred` nie zastępuje nieznanego czasu zdarzenia datą publikacji; do publikacji służy `published`, do ważności `validity`, a `changed` opisuje lokalne korekty. Briefing odróżnia import historyczny od nowego zdarzenia i zapisuje kursor dopiero po poprawnym wyniku. Przy pierwszym imporcie świeży incydent lub pomiar może wejść do briefingu po swoim `occurred_start`, nawet bez `issued_at`; stare wpisy KEV pozostają tłem. Sam brak daty publikacji nie ukrywa aktualnego wstrząsu. Briefing odczytuje całość serwerowym kursorem po 250 rekordów. Zapamiętuje najwyżej 30 faktów do narracji, liczy wszystkie rekordy i przesuwa kursor dopiero po pełnym odczycie i walidacji. Błąd lub niezgodny licznik cofa transakcję.

## Błędy i retencja

Świeżość treści i powodzenie HTTP to oddzielne oceny. Dla USGS `provider_timestamp` z `metadata.generated` starszy niż 20 minut powoduje `stale` (albo `partial`, jeśli jednocześnie wystąpiły błędy). Pełny kursor nie jest wtedy przesuwany. To lokalna reguła monitora, nie obietnica dostawcy. Osobno status może stać się `stale`, gdy zbyt długo nie było poprawnego odczytu.

Transport ogranicza adresy/ścieżki, sprawdza publiczne adresy DNS, rozmiar odpowiedzi do 10 MiB i liczbę przekierowań. Nie ufa ustawieniom proxy środowiska. Cache ETag/Last-Modified jest pamięciowy, ograniczony do 300 dokumentów lub 64 MiB; restart go usuwa. 304 jest użyteczne tylko wraz z zachowanym body.

Cała próba pobierania ma limit krótszy od dzierżawy (domyślnie 840 wobec 900 sekund). Po jego przekroczeniu próba kończy się błędem; dotychczasowa baza pozostaje bez zmian, lecz nieukończony batch nie jest publikowany. HTTP 429 przekazuje ograniczony czas ponowienia. Radar zachowuje poprawne wcześniejsze strony, a MeteoAlarm nie zaczyna dalszych CAP po otrzymaniu limitu; już trwający odczyt może się zakończyć. Próby mają backoff. Żaden błąd źródła nie jest dowodem, że nie było zdarzeń.

Surowy payload znika po 30 dniach; hash i normalizacja pozostają zgodnie z retencją powiązanego zdarzenia. Po 180 dniach bez ponownego odczytu można usunąć wpisy wygasłe/wycofane, stare chwilowe trzęsienia ziemi oraz nieaktywne incydenty ze znanym dawnym końcem. Pozostałe trwające incydenty i incydenty bez potwierdzonego końca pozostają, również gdy wypadły z krótkiego feedu. Starsze rewizje są przycinane z zachowaniem ostatniej; kopie zapasowe nie mają automatycznej retencji w tej wersji.

## Kopia i odtworzenie

`backup` otwiera transakcję REPEATABLE READ READ ONLY, eksportuje snapshot i z tego samego snapshotu odczytuje fingerprint 13 tabel oraz wykonuje `pg_dump --snapshot`. Manifest zawiera liczbę bajtów, SHA-256 dumpu, liczby wierszy i ich checksumy. Para plików ma prawa 0600 w katalogu 0700. Nie są to pliki szyfrowane ani podpisane.

`restore-check` wymaga własnego dumpu i manifestu, najpierw weryfikuje SHA-256, następnie tworzy nową bazę `monitor_restore_…`, odtwarza do niej dane i porównuje wszystkie fingerprinty z manifestem. Bazę kontrolną usuwa po próbie; nie nadpisuje bazy roboczej. Bieżący stan roboczy nie służy jako punkt porównania, bo może zmienić się od czasu kopii. Funkcja sprawdzania odtworzenia nie jest poleceniem przywrócenia kopii do produkcyjnej bazy.

## Granice zabezpieczeń i praw

Compose publikuje wyłącznie localhost:3180. API/proxy sprawdzają Host/Origin, rozmiar i dozwolone ścieżki; odczyty bazy korzystają z roli `monitor_reader`, która może dodatkowo zapisywać briefingi. Worker ma odrębną rolę, a migracje i narzędzia administracyjne inną. Sekrety nie trafiają do frontendu.

To nie izolacja od administratora hosta ani ochrona przed innymi lokalnymi użytkownikami. Nie ma logowania, szyfrowania kopii przez aplikację, testu penetracyjnego ani deklaracji pełnego audytu bezpieczeństwa. Kontenery potrzebują wyjścia do zatwierdzonych źródeł; kontrola adresów w kodzie nie jest zewnętrznym firewallem egress.

Implementacja aplikacji nie zawiera skopiowanego kodu World Monitor/AGPL. Nie oznacza to jednej licencji dla całego stosu: Next.js/React są MIT, MapLibre BSD-3-Clause, Psycopg LGPL-3.0-only, PostGIS ma własną licencję GPL. Pozostałe biblioteki i obrazy zachowują swoje warunki i notices. Licencje kodu oraz prawa do danych są osobnymi sprawami; szczegóły danych podaje [DATA_SOURCES.md](DATA_SOURCES.md). Nie przyznajemy automatycznie prawa do publikacji całego zestawu. Rejestr wybranych zależności i miejsc weryfikacji zawiera [THIRD_PARTY.md](THIRD_PARTY.md). Licencji udzielanej do autorskiego kodu aplikacji nie ustalano; nie dodajemy domyślnej licencji projektu.

## Oddzielny publiczny zestaw

[Public Pages](PUBLIC_PAGES.md) nie uruchamia lokalnego API. Generator tworzy nową bazę monitor_public_* z allowlistą trzech źródeł i usuwa ją po eksporcie przetworzonych pól. Publiczny entrypoint nie zawiera routingu API, sekretów ani prywatnej historii. Artefakt jest niezależny od wolumenu prywatnej instalacji.
