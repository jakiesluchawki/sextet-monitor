# Mieszko Monitor

Prywatny, lokalny monitor zmian z mapą, listą zdarzeń i materiałem źródłowym. Kod Phase 1 powstał od zera, bez kopiowania World Monitor. Nie jest kompletnym obrazem świata ani narzędziem do decyzji operacyjnych o bezpieczeństwie.

**Aktualizacja 1.09.2026:** poprawki przetwarzania danych, pytań, briefingów i odświeżania dowodów opisuje [CHANGELOG](CHANGELOG.md). Testy uruchamia też GitHub Actions. Formalna próba 72 godzin nadal nie została wykonana.

Repozytorium obejmuje pełną instalację lokalną oraz **oddzielny statyczny podgląd**. GitHub Pages nie uruchamia backendu ani prywatnej bazy; zakres publiczny i wyłączoną domyślnie publikację opisuje [PUBLIC_PAGES](PUBLIC_PAGES.md).

## Co jest w tej wersji

Pięć źródeł można czytać bez klucza: USGS, GDACS, MeteoAlarm dla Polski, EASA CZIB i CISA KEV. Szósty adapter, Cloudflare Radar, pozostaje w stanie `needs_credentials` bez tokenu. Nie zastępuje go fikcyjny strumień.

Mapa, lista, dowody i pytania korzystają ze wspólnego modelu filtrów. Parser polskich pytań oraz briefing są deterministyczne. **AI jest wyłączone**; nie ma wywołań OpenAI/Ollama, pobierania modeli ani pozornego „AI risk score”.

Dostępne dane nie obejmują śledzenia wojsk, samolotów i statków, pełnych NOTAM, GNSS ani rynku ropy. Brak wyniku nie dowodzi braku zdarzenia. [Zakres źródeł](DATA_SOURCES.md) opisuje również różnicę między incydentem, ostrzeżeniem i informacją o podatności.

## Uruchomienie

Polecenia wykonuj z katalogu repozytorium. Potrzebne są Python 3 do skryptu zarządzającego oraz działający Docker Engine z Compose. Python backendu, Node i baza pracują w kontenerach; nie trzeba zmieniać globalnego Node.

Na obecnym Macu działa oddzielna maszyna Colima: 4 CPU, 6 GiB RAM, dysk 24 GiB, bez montowania katalogu domowego i bez autostartu. Przed uruchomieniem aplikacji Colima musi działać. Skrypt sam używa istniejącego gniazda `~/.colima/default/docker.sock`, jeśli nie ustawiono `DOCKER_HOST` ani `DOCKER_CONTEXT`; nie przełącza globalnego kontekstu Dockera.

Przy zwykłym powrocie do istniejącej instalacji uruchom, jeśli potrzeba, `colima start default`, następnie `python3 scripts/manage.py up`. Nie zmieniaj przy tym profilu ani montowanych katalogów. Poniższe parametry opisują przygotowanie nowej maszyny na Apple Silicon, a nie codzienny start istniejącej instalacji:

~~~sh
DOCKER_CONFIG="$PWD/../work/docker-config" colima start --cpu 4 --memory 6 --disk 24 --vm-type vz --mount none --activate=false --ssh-config=false
~~~

Na świeżym Macu najpierw wybierz istniejący Docker Desktop albo świadomie zainstaluj Colimę. Instalacja przez Homebrew może wymagać dodatkowego potwierdzenia i pobiera pakiety:

~~~sh
brew install colima docker docker-compose docker-buildx
~~~

Dla wariantu Homebrew przygotuj nowy, lokalny `../work/docker-config/config.json` z `cliPluginsExtraDirs` wskazującym katalog wtyczek Homebrew; na sprawdzonym Apple Silicon jest to `/opt/homebrew/lib/docker/cli-plugins`. Nie nadpisuj istniejącej konfiguracji. `manage.py` wybierze ten katalog, gdy istnieje; nie trzeba zmieniać `~/.docker/config.json`. Nie ustawiaj `COLIMA_HOME` na podstawie lokalizacji tego projektu.

Na Linuksie skrypt używa dostępnego `docker-compose` albo `docker compose`. Nie deklarujemy sprawdzonego wdrożenia AMD64 ani każdego wariantu Docker Desktop.

Pierwsze uruchomienie:

~~~sh
python3 scripts/manage.py init
python3 scripts/manage.py db
python3 scripts/manage.py build
python3 scripts/manage.py migrate
python3 scripts/manage.py seed
python3 scripts/manage.py ingest
python3 scripts/manage.py up
python3 scripts/manage.py status
~~~

`init` tworzy `.env` z losowymi hasłami i prawami 0600; zachowuje istniejący plik. `seed` ładuje lokalne granice krajów i konfigurację źródeł, nie dane demonstracyjne. `ingest` wykonuje jeden rzeczywisty odczyt włączonych źródeł. `up` uruchamia zwykły worker aplikacji, który dalej odpytuje dostawców. Nie jest to automatyzacja Codex.

Panel: [localhost:3180](http://localhost:3180). Jedynym opublikowanym portem Compose jest `127.0.0.1:3180`; API i baza nie mają portów hosta.

## Obsługa

| Polecenie po `python3 scripts/manage.py` | Działanie |
|---|---|
| `status` | Stan kontenerów, liczby rekordów i statusy źródeł |
| `ingest usgs` | Jednorazowe pobranie wskazanego źródła; bez usuwania zapisanych danych |
| `logs worker` | Ostatnie logi wskazanej usługi |
| `check` | Walidacja Compose bez wypisywania sekretów oraz sprawdzenie PostGIS i jednostek odległości |
| `test` | Testy integracyjne w nowej bazie `monitor_test_…`; po teście usuwa tylko tę bazę |
| `backup` | Para `backups/monitor-….dump` i `.manifest.json`: jedna spójna migawka bazy, SHA-256 dumpu oraz sumy kontrolne 13 tabel; pliki 0600 |
| `restore-check backups/monitor-….dump` | Weryfikacja SHA-256, odtworzenie do osobnej bazy, automatyczne porównanie 13 tabel z manifestem i usunięcie bazy kontrolnej |
| `fingerprint` | Liczby i sumy kontrolne 13 tabel z bieżącego odczytu; nie jest zamiennikiem manifestu kopii |
| `detach-source --event-id UUID --source-id gdacs` | Podgląd rozdzielenia źródła bez zmiany przypisań; szczegóły poniżej |
| `stop` | Zatrzymanie usług bez usuwania wolumenu danych |
| `up` | Ponowne uruchomienie usług; wymaga działającego silnika kontenerów |

`backup` korzysta z `pg_export_snapshot`: manifest i `pg_dump --snapshot` opisują ten sam stan nawet wtedy, gdy worker dalej zapisuje. Przechowuj dump razem z jego manifestem. `restore-check` porównuje odtworzoną bazę z manifestem tej kopii, nie z późniejszą bazą roboczą. Sukces wypisuje `restore_matches_backup: true`, potwierdzenie SHA-256 i usunięcia bazy kontrolnej.

Manifest nie jest podpisany kryptograficznie; oba pliki wymagają ochrony. Nie używaj `docker compose down -v` do zwykłego zatrzymywania: usuwa dane.

Token Radar, jeśli zostanie osobno udostępniony i zaakceptowany do osobistego, niekomercyjnego użycia, trafia wyłącznie do `CLOUDFLARE_RADAR_TOKEN` w `.env`. Nie podawaj go w URL, pytaniu ani poleceniu zapisywanym w historii. Po zmianie konfiguracji uruchom `up` i sprawdź rzeczywisty status źródła; sama obecność tokenu nie potwierdza odbioru danych.

## Ręczne rozdzielenie źródła

Używaj tej funkcji dopiero po stwierdzeniu błędnego wspólnego przypisania. Odłącza wszystkie obecne rekordy jednego źródła od wybranego zdarzenia; nie można odłączyć jego ostatniego źródła. Zastąp `UUID_ZDARZENIA` rzeczywistym identyfikatorem z danych aplikacji.

Najpierw tylko podgląd:

~~~sh
python3 scripts/manage.py detach-source --event-id "UUID_ZDARZENIA" --source-id gdacs
~~~

Sprawdź listę rekordów, przenoszonych kluczy, pozostających źródeł i ograniczenia. Podgląd nie rezerwuje stanu; zapis sprawdzi go ponownie. Dopiero po decyzji zastosuj zmianę, podając własny konkretny powód (maksymalnie 500 znaków):

~~~sh
python3 scripts/manage.py detach-source --event-id "UUID_ZDARZENIA" --source-id gdacs --reason "Powód ustalony po przeglądzie dowodów" --apply
~~~

Wariant `--apply` automatycznie tworzy kopię przed zmianą. Administracyjna transakcja zachowuje payloady i wcześniejsze rewizje, zapisuje powód oraz rozdziela powiązania. Nadpisanie reguł obejmuje obecne rekordy po obu stronach, więc kolejne odczyty tych rekordów nie scalą ich ponownie. Nie obejmuje jeszcze nieznanych identyfikatorów przyszłych rekordów. To korekta bazy, nie nowe zdarzenie ani kolejne potwierdzenie. Funkcja nie ma publicznego endpointu HTTP.

## Jak czytać wynik

„Czas zdarzenia” korzysta z `occurred_start`. Gdy źródło go nie podaje, pozostaje `NULL`: KEV i CZIB nie otrzymują wymyślonego czasu ataku. Wybierz datę publikacji dla CISA, okres ważności dla ostrzeżeń EASA i pogodowych, a tryb zmian dla korekt zapisanych lokalnie. Daty bez godziny są oznaczone precyzją dnia; zapis północy UTC jest techniczną kotwicą, nie chwilą pomiaru.

`source_count` liczy kanały publikacji, a `independent_source_count` ostrożnie rozpoznane pochodzenie dowodów. USGS oraz GDACS powtarzający USGS nie dają dwóch potwierdzeń.

Stan `ok_empty` oznacza poprawny pusty odczyt, `partial` niepełny, a `error` błąd; ostatnie poprawne dane nie znikają wskutek awarii. Dla USGS wiek `metadata.generated` większy niż 20 minut daje `stale` mimo poprawnego HTTP. To lokalna reguła świeżości monitora, nie SLA dostawcy.

## Prywatność i utrzymanie

To lokalny interfejs **bez logowania**. Inni użytkownicy i procesy tego samego hosta mogą uzyskać dostęp do portu; ochronę konta i dysku zapewnia system operacyjny. Localhost nie zastępuje autoryzacji. Nie otwieraj portu w LAN ani tunelu bez osobnego projektu zabezpieczeń.

`.env`, baza i kopie są lokalne. **Aplikacja nie szyfruje dysku ani backupów.** Kopie wymagają ochrony i osobnej polityki przechowywania. Nie wklejaj pełnego środowiska ani rozwiniętego `docker compose config` do zgłoszeń; mogą zawierać hasła.

Retencja usuwa surowe dane źródłowe (payloady) po 30 dniach. Stare, zakończone zdarzenia mogą zostać usunięte po 180 dniach bez ponownego odczytu; trwające zdarzenia są zachowywane. To nie sztywny limit całej bazy. Szczegóły: [architektura](ARCHITECTURE.md).

[Decyzje](DECISIONS.md), [dalsze kroki](ROADMAP.md), [kontrakt API](API_CONTRACT.md), [zależności i licencje zewnętrzne](THIRD_PARTY.md). Nie ustalano licencji udzielanej do autorskiego kodu aplikacji; ten opis nie nadaje mu licencji MIT, AGPL ani innej. Historyczne potwierdzenia walidacji Phase 0/1 i prywatny audyt pozostają poza repozytorium; nie są dołączane do publikacji. Bieżący zakres opisują dokumenty w tym repozytorium.
