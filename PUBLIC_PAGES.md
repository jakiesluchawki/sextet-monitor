# Publiczny podgląd GitHub Pages

Pages służy do hostowania plików statycznych; nie uruchamia FastAPI, PostGIS ani stale działającego workera. Pełna instalacja nadal działa lokalnie. Osobny podgląd współdzieli komponenty mapy, listy i dowodów, ale nie udaje połączenia z prywatnym API.

**Publiczna wersja Sextet Monitor została zatwierdzona przez właściciela 1.09.2026** do udostępniania grupie. Kod i niezależnie pobrane publiczne źródła mogą być publikowane; prywatna baza, historia oraz sekrety pozostają lokalne. Publikacja wymaga `PUBLIC_PAGES_ENABLED=true`. Poprzednia próba dla prywatnego repozytorium była zablokowana przez plan GitHub (HTTP 422); publiczna widoczność jest teraz wyraźnie zaakceptowana, bez zmiany planu płatnego. Prywatność repozytorium nie gwarantuje prywatności strony. [Dokumentacja GitHub Pages](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages).

## Co obejmuje zestaw

Zestaw obejmuje dane USGS, MeteoAlarm Polska i CISA KEV, pobrane od nowa do oddzielnej bazy. Podgląd zawiera mapę, listę i oś czasu, filtry kategorii, wagi i kraju, źródłowe daty wystąpienia, publikacji i ważności, odnośniki, atrybucję oraz jawny wiek zestawu. Domyślne okno kończy się w chwili przygotowania zestawu, a nie w pozornym „teraz” po tygodniu bez odświeżenia.

Nie ma pytań do prywatnej bazy, briefingu od poprzedniego, historii operatora, surowych payloadów, promienia PostGIS, filtra Europy, AI ani tokenów. GDACS, EASA i Radar pozostają poza publicznym zestawem. „Odśwież” pobiera nowszy opublikowany plik; nie steruje prywatnym workerem.

Generator nie przyjmuje nazwy istniejącej bazy do eksportu. Połączenie administracyjne musi wskazywać `postgres`; skrypt sam tworzy `monitor_public_<losowy-id>`, wykonuje migracje i pobrania, a następnie usuwa wyłącznie utworzoną bazę. Źródła są zapisane na jawnej liście dozwolonych dostawców. Publiczne UUID powstają z identyfikatorów dostawców, nie są UUID prywatnego monitora. Kontrakt usuwa wewnętrzne pola, surowe dane (`raw`), rewizje i szczegóły błędów.

Limit zestawu: 10 000 zdarzeń i 16 MiB. Błąd źródła lub schematu albo przekroczenie limitu zatrzymuje przygotowanie publikacji. Jeśli strona była wcześniej opublikowana, pozostaje bez zmian. Częściowy odczyt jest jawnie oznaczony. Plik snapshotu i katalog wynikowy są ignorowane przez Git. Nie publikujemy zrzutów baz ani obrazów usług.

## Przygotowanie lokalne

Wymaga zainicjowanej konfiguracji Docker i zbudowanego aktualnego obrazu API. Poniższe działania zbierają nowy publiczny zestaw, ale nie wysyłają go do GitHuba:

~~~sh
python3 scripts/manage.py db
python3 scripts/manage.py build api
python3 scripts/build_public_snapshot.py
cd web
npm ci --ignore-scripts --no-audit --no-fund
npm test
npm run typecheck
npm run build:pages
~~~

Katalog wynikowy: `web/.pages-build/out`. Skrypt kopiuje wyłącznie wskazane komponenty i pliki publiczne; nie kopiuje tras API, `.env`, baz, konfiguracji hosta ani prywatnego interfejsu. Katalog wynikowy ma znacznik `OWNED`; katalog bez zgodnego znacznika lub dowiązanie symboliczne powodują odmowę czyszczenia. `MONITOR_PAGES_BASE_PATH` domyślnie wynosi `/sextet-monitor` i obejmuje mapę, workery, JS oraz JSON.

## Publikacja

Workflow `Public Pages` działa tylko na gałęzi `main` i po jawnym ustawieniu zmiennej repozytorium `PUBLIC_PAGES_ENABLED=true`. Ustawienia Pages muszą wskazywać GitHub Actions. Zmienna repozytorium `PUBLIC_SITE_URL` zawiera adres HTTPS zwrócony przez GitHub Pages; workflow przekazuje go jako `MONITOR_PUBLIC_SITE_URL` do danych Open Graph i adresu canonical. Sam push kodu bez włączonej zmiennej `PUBLIC_PAGES_ENABLED` niczego nie publikuje.

Runner tworzy nowe losowe hasła, uruchamia testy i osobną bazę, pobiera publiczne źródła, buduje statyczny podgląd, a dopiero potem wysyła do Pages artefakt zawierający wyłącznie dozwolone pliki. Akcje są przypięte do pełnych SHA; uprawnienia do zapisu w Pages i wystawienia tokenu OIDC ma wyłącznie zadanie wdrożeniowe. Nie ma sekretów produkcyjnych ani połączenia z Makiem użytkownika.

Po włączeniu odświeżanie jest planowane raz na godzinę, w 17. minucie każdej godziny według UTC. Można je również wywołać pushem na `main` albo ręcznie przez `workflow_dispatch`. GitHub może opóźniać wykonanie harmonogramu; nie jest to gwarantowany czas aktualizacji (SLA). Strona pokazuje czas ostatniego udanego zestawu. Wyłączenie zmiennej zatrzymuje kolejne publikacje, ale nie usuwa już opublikowanej strony.

## Pochodzenie i licencje

Przegląd źródeł z 1.09.2026:

- [USGS](https://www.usgs.gov/faqs/are-usgs-reportspublications-copyrighted): własne dane w domenie publicznej; zachowujemy pochodzenie sieci i nie publikujemy chronionych zdjęć/grafik partnerów.
- [MeteoAlarm](https://feeds.meteoalarm.org/): CC BY 4.0, MeteoAlarm/EUMETNET i IMGW-PIB; zaznaczamy przetworzenie i link do licencji.
- [CISA KEV](https://github.com/cisagov/kev-data/blob/develop/LICENSE): CC0 dla katalogu; zewnętrzne strony mają własne warunki. Nie używamy logo CISA/DHS ani nie sugerujemy poparcia.
- [Natural Earth](https://www.naturalearthdata.com/about/terms-of-use/): domena publiczna, mapa uproszczona.
- `THIRD_PARTY_NOTICES.txt` zachowuje informacje o prawach i licencjach z zainstalowanych pakietów produkcyjnych i komponentów Next; MapLibre zachowuje również swój plik `LICENSE`.

Publiczna widoczność repozytorium nie nadaje autorskiemu kodowi aplikacji licencji MIT ani innej. Warunki zależności i danych pozostają odrębne.
