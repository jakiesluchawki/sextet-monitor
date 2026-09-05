# Publiczny podgląd GitHub Pages

Pages służy do hostowania plików statycznych; nie uruchamia FastAPI, PostGIS ani stale działającego workera. Pełna instalacja nadal działa lokalnie. Publiczny interfejs 03 współdzieli komponenty mapy i dowodów, ale nie łączy się z prywatnym API. Poniższy opis dotyczy kodu tej wersji; sam dokument nie potwierdza wdrożenia.

**Publiczna wersja Sextet Monitor została zatwierdzona przez właściciela 1.09.2026** do udostępniania grupie. Kod i niezależnie pobrane publiczne źródła mogą być publikowane; prywatna baza, historia oraz sekrety pozostają lokalne. Publikacja wymaga `PUBLIC_PAGES_ENABLED=true`. Poprzednia próba dla prywatnego repozytorium była zablokowana przez plan GitHub (HTTP 422); publiczna widoczność jest teraz wyraźnie zaakceptowana, bez zmiany planu płatnego. Prywatność repozytorium nie gwarantuje prywatności strony. [Dokumentacja GitHub Pages](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages).

## Co obejmuje zestaw

Zestaw obejmuje **jedenaście kanałów**: USGS, MeteoAlarm Polska, CISA KEV, GDACS, EASA CZIB, NASA EONET, NOAA SWPC, GitHub Status, Cloudflare Status, CERT Polska i IMGW Hydrologia, pobrane od nowa do oddzielnej bazy. Widoczna liczba rekordów i stan źródeł zależą od konkretnego zestawu. CERT udostępnia wyłącznie indeks dat i odsyłaczy bez treści artykułów; IMGW obejmuje ostrzeżenia, nie pomiary rzek. Warunki użycia IMGW odnoszą się do obecnego niezarobkowego projektu bez reklam; nie deklarujemy prawa do zastosowania komercyjnego. IODA nie jest publikowana.

Publiczna wersja zawiera przegląd z wyróżnieniami, globus/mapę 2D, listę, oś czasu, dosłowne wyszukiwanie, filtry źródła i kategorii, daty wystąpienia, publikacji i ważności oraz dowody, odnośniki i atrybucję. Briefing i porównanie dwóch zestawów są lokalnymi operacjami przeglądarki. Nie ma pytań do prywatnej bazy, historii operatora, surowych payloadów, promienia PostGIS, AI ani tokenów. Radar pozostaje poza publicznym zestawem. „Odśwież” pobiera nowszy opublikowany plik; nie odpytuje dostawców i nie steruje prywatnym workerem.

NASA obejmuje do 400 wpisów z 30 dni o pożarach, wulkanach i silnych burzach; osiągnięcie limitu daje `partial`, nie zapewnienie kompletności. EASA opisuje ryzyko operacji lotniczych, bez wymyślania daty ataku. NOAA oddziela obserwowane alerty i podsumowania od prognoz/ostrzeżeń, bez lokalizacji awarii GPS. Statusy GitHuba i Cloudflare to ostatnie 50 incydentów każdego operatora, nie pełne archiwa ani globalny pomiar Internetu. Licznik źródeł nie jest liczbą niezależnych potwierdzeń zdarzenia.

Generator nie przyjmuje nazwy istniejącej bazy do eksportu. Połączenie administracyjne musi wskazywać `postgres`; skrypt sam tworzy `monitor_public_<losowy-id>`, wykonuje migracje i pobrania, a następnie usuwa wyłącznie utworzoną bazę. Źródła są zapisane na jawnej liście dozwolonych dostawców. Publiczne UUID powstają z identyfikatorów dostawców, nie są UUID prywatnego monitora. Kontrakt usuwa wewnętrzne pola, surowe dane (`raw`), rewizje i szczegóły błędów.

Limit zestawu: 10 000 zdarzeń i 16 MiB. Błąd jednego dostawcy nie zatrzymuje odczytu pozostałych. Jeśli `MONITOR_PUBLIC_SITE_URL` wskazuje dozwolony publiczny adres GitHub Pages, generator może odczytać poprzedni **już opublikowany** zestaw. Wymagane są poprawny schemat, dozwolone źródła, publiczne UUID wyliczalne z metadanych dostawcy, brak raw/rewizji oraz poprawne daty. Pobranie ma ograniczony czas i rozmiar, sprawdza publiczny DNS i nie podąża za przekierowaniami. Nie czyta lokalnego snapshotu ani prywatnej bazy jako danych zastępczych.

Poprzednie rekordy uszkodzonego źródła zachowują wszystkie daty dowodów i znacznik `cached_public_data`; źródło nadal ma stan `error`, stary czas ostatniego sukcesu i liczbę rzeczywiście zachowanych rekordów. Nie uzupełniamy w ten sposób poprawnego pustego ani częściowego odczytu z przyjętymi rekordami. Odczyt `partial`, w którym nie przyjęto żadnych poprawnych rekordów, jest dla publikacji błędem. Dawnego rekordu łączącego kilka źródeł nie przypisujemy arbitralnie jednemu z nich: można go zachować tylko wtedy, gdy wszystkie jego źródła uległy awarii i nie powiela nowych dowodów.

Błąd wszystkich źródeł, schematu lub przekroczenie limitu zatrzymuje publikację; ostatnia strona pozostaje bez zmian. Brak albo niepoprawność poprzedniego publicznego zestawu nie blokuje nowych odczytów. Plik snapshotu i katalog wynikowy są ignorowane przez Git. Nie publikujemy zrzutów baz ani obrazów usług.

## Widoki, zakres i daty

„Przegląd” wybiera do ośmiu zapisów według reguł, z reprezentacją kategorii i powodem wyróżnienia. Wybór korzysta z dat źródłowych i źródłowej wagi; nie jest rankingiem ryzyka. Powtarzające się opisy i jawnie powiązane możliwe duplikaty nie powinny zajmować wielu wyróżnień, ale pozostają osobnymi rekordami danych.

Zakresy świata, Europy, Polski i wybranego kraju/terytorium używają kodów krajów podanych w rekordach. Europa to jawna lista w `web/lib/areas.ts`, z Cyprem i Kosowem, bez Rosji i Turcji; nie jest geometrycznym kontynentem. Rekordów bez kraju oraz globalnych komunikatów usług nie przypisujemy automatycznie do wybranego regionu. Wybór kraju nie dodaje źródeł. Do ośmiu ulubionych krajów można zapisać pod kluczem `sextet.public.areas.v1`: tylko kody, bez nazw, geometrii czy historii. Błędy pamięci są jawne; inne karty odczytują zmieniony stan. Skróty kamery na samej mapie przesuwają widok, ale nie zmieniają filtrów danych.

Okna 24 h, 72 h i 7 dni kończą się w `generated_at` zestawu. Zegar urządzenia odświeża ostrzeżenia o wieku, nie przesuwa okna. Trzęsienia i katastrofy używają początku zdarzenia; pogoda i lotnictwo przecięcia okresu ważności; cyber, internet i pogoda kosmiczna daty publikacji. Nieznany wymagany czas wyklucza dopasowanie do okna, bez zastępowania go datą pobrania. Daty dzienne nie uzyskują wymyślonej godziny. Ostrzeżenie lub data dzienna może przecinać kilka słupków osi czasu: ich suma nie jest liczbą unikalnych zdarzeń. Tabela pod wykresem pokazuje dokładne wartości i podstawy czasu.

„Mapa i dane” wyszukuje dosłowne słowa w tytułach, opisach, krajach i nazwach źródeł, bez wykonywania wyrażeń regularnych ani kodu. Lista udostępnia wynik partiami po 60 rekordów. Mapa obejmuje najwyżej 500 pasujących rekordów i jawnie podaje limit; brak geometrii pozostaje poza mapą. Podkład i workery są lokalnymi plikami strony. Gdy WebGL nie działa, lista nadal pozwala przejść do rekordów i dowodów. „Filtry szczegółowe” otwierają `DetailedExplorer` z wcześniejszym wyborem podstawy czasu, wagi, kraju i przesunięcia okna; ten tryb również odczytuje wyłącznie publiczny zestaw.

## Przypięcia i punkt porównania

Gwiazdka zapisuje maksymalnie 30 publicznych UUID w `localStorage` bieżącej przeglądarki. „Zarządzaj przypiętymi” w briefingu pozwala usunąć także zapis spoza wybranego zakresu lub nieobecny w zestawie. Nie przechowujemy tytułów, opisów, geometrii ani pełnych dowodów przypiętych pozycji. Nie ma konta użytkownika, synchronizacji urządzeń, powiadomień ani dostępu do Signala.

Pierwszy poprawny odczyt może utworzyć lokalny punkt odniesienia. Przycisk „Zapamiętaj obecny zestaw” świadomie go przesuwa; zwykły odczyt nowszego pliku pozwala nadal porównywać z poprzednio zapisanym punktem. Baseline zawiera wersję schematu, czas publikacji oraz maksymalnie 10 000 par publiczny UUID–niekryptograficzny odcisk wybranych pól, w granicy 1 MiB. Odcisk jest skrótem do wykrywania zmian, nie podpisem, dowodem integralności ani autentyczności źródła. Nie przechowuje całych zdarzeń, payloadów, URL-i ani prywatnej historii.

Odciski uwzględniają treść, daty źródłowe, wagę, stan, geometrię i pochodzenie. Pomijają zmienne czasy pobrania i importu, odtworzoną historię, relacje wyliczone dla zestawu oraz samą flagę użycia wcześniejszych publicznych danych. Nowy rekord oznacza nowy wpis względem punktu odniesienia, nie nowy potwierdzony incydent. Zmieniony zapis nie ustala przyczyny zmiany; brak wcześniejszego zapisu może wynikać z okna, limitu lub grupowania, nie dowodzi zakończenia zdarzenia. Zestaw starszy od baseline nie tworzy zmian w odwrotnym kierunku i nie cofa punktu. Przy tym samym czasie publikacji, lecz innej treści kolejność pozostaje nieustalona.

Dane pamięci mają wersję, ograniczenia rozmiaru i walidację; uszkodzony lub nieobsługiwany zapis jest pomijany. Blokada storage, limit miejsca, tryb prywatny albo wyczyszczenie danych strony mogą uniemożliwić trwały zapis. Karty odczytują zmiany z tej samej pamięci, ale `localStorage` nie oferuje atomowych transakcji między kartami. To wygoda lokalna, nie kopia bezpieczeństwa ani trwały dziennik. Każdy mający dostęp do tego profilu przeglądarki może zobaczyć lub usunąć zapisane identyfikatory.

## Briefing i linki

Briefing powstaje z wyróżnień albo przypiętych rekordów mieszczących się w aktualnym obszarze i czasie. Zawiera najwyżej 12 pozycji, źródłowe daty i adresy, czas odczytu dowodów oraz ograniczenia. Powstaje deterministycznie w przeglądarce, bez modelu AI. „Kopiuj do Signala” kopiuje tekst do schowka, a przy odmowie przeglądarki pokazuje pole do ręcznego skopiowania. Użytkownik sam decyduje o wklejeniu i wysyłce. Zapis `.md` pobiera plik; „Drukuj / PDF” otwiera systemowy dialog przeglądarki.

Kopiowany link przechowuje w fragmencie URL tylko zatwierdzone pola: widok, obszar, okno, kategorię, źródło, wyszukiwanie i opcjonalny publiczny UUID. Parametry mają limity, a identyfikatory walidację. Zastane query i hash nie są kopiowane; adresy z danymi logowania lub innymi protokołami niż HTTP(S) są odrzucane. Wpisane wyszukiwanie jest częścią linku, więc nie wpisuj w nie sekretów. Przypięcia, ulubione obszary i baseline nie są udostępniane. `country:XX` jest walidowany jawną listą ISO alpha-2 plus XK, a historyczne `turkey` zamienia się na `country:TR`. Link zawsze otwiera najnowszy dostępny zestaw: nie zamraża danych i może wskazywać rekord, którego już w nim nie ma.

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

Workflow `Public Pages` działa tylko na gałęzi `main` i po jawnym ustawieniu zmiennej repozytorium `PUBLIC_PAGES_ENABLED=true`. Ustawienia Pages muszą wskazywać GitHub Actions. Zmienna repozytorium `PUBLIC_SITE_URL` zawiera adres HTTPS zwrócony przez GitHub Pages; workflow przekazuje go jako `MONITOR_PUBLIC_SITE_URL` do danych Open Graph, adresu canonical i opcjonalnego odczytu poprzedniej publicznej publikacji. Sam push kodu bez włączonej zmiennej `PUBLIC_PAGES_ENABLED` niczego nie publikuje.

Runner tworzy nowe losowe hasła, uruchamia testy i osobną bazę, pobiera publiczne źródła, buduje statyczny podgląd, a dopiero potem wysyła do Pages artefakt zawierający wyłącznie dozwolone pliki. Akcje są przypięte do pełnych SHA; uprawnienia do zapisu w Pages i wystawienia tokenu OIDC ma wyłącznie zadanie wdrożeniowe. Nie ma sekretów produkcyjnych ani połączenia z Makiem użytkownika.

Po włączeniu odświeżanie jest planowane raz na godzinę, w 17. minucie każdej godziny według UTC. Można je również wywołać pushem na `main` albo ręcznie przez `workflow_dispatch`. GitHub może opóźniać wykonanie harmonogramu; nie jest to gwarantowany czas aktualizacji (SLA). Strona pokazuje czas ostatniego udanego zestawu. Wyłączenie zmiennej zatrzymuje kolejne publikacje, ale nie usuwa już opublikowanej strony.

Sam udany build nie potwierdza działania publikacji. Po wdrożeniu należy sprawdzić odczyt `snapshot.json`, zgodność wersji plików, rzeczywiste daty i stany źródeł, ładowanie globusa lub czytelny powrót do listy, filtry i dowody na komputerze oraz telefonie, odtworzenie linku i zachowanie przypięć po przeładowaniu. Osobno trzeba sprawdzić odmowę zapisu pamięci i kopiowania oraz starszy zestaw względem baseline. Testy jednostkowe nie potwierdzają wykonania tych prób w przeglądarce. Nie deklarujemy formalnej próby 72 godzin.

## Pochodzenie i licencje

Przegląd źródeł z 1.09.2026:

- [USGS](https://www.usgs.gov/faqs/are-usgs-reportspublications-copyrighted): własne dane w domenie publicznej; zachowujemy pochodzenie sieci i nie publikujemy chronionych zdjęć/grafik partnerów.
- [MeteoAlarm](https://feeds.meteoalarm.org/): CC BY 4.0, MeteoAlarm/EUMETNET i IMGW-PIB; zaznaczamy przetworzenie i link do licencji.
- [CISA KEV](https://github.com/cisagov/kev-data/blob/develop/LICENSE): CC0 dla katalogu; zewnętrzne strony mają własne warunki. Nie używamy logo CISA/DHS ani nie sugerujemy poparcia.
- [GDACS](https://www.gdacs.org/Documents/2025/GDACS_Terms_of_use_Mar_25.pdf): atrybucja Global Disaster Awareness and Coordination System, GDACS; modele nie zastępują krajowych ostrzeżeń i nie gwarantują kompletności. Nie kopiujemy cudzych obrazów i raportów.
- [EASA](https://www.easa.europa.eu/en/copyright-disclaimer): reprodukcja z podaniem źródła, z wyjątkami materiałów zastrzeżonych; zachowujemy oryginalne odnośniki.
- [NASA EONET](https://eonet.gsfc.nasa.gov/what-is-eonet): kuratorskie metadane z pochodzeniem źródeł; przybliżone dane informacyjne, bez publikowania obrazów ani sugerowania poparcia NASA.
- [NOAA/NWS](https://www.weather.gov/disclaimer): własne informacje publiczne z zachowaną atrybucją; bez sugerowania oficjalnego poparcia.
- [GitHub Status](https://www.githubstatus.com/api/v2) i [Cloudflare Status](https://www.cloudflarestatus.com/api): publiczne API. Publikujemy faktyczne metadane (nazwa, stan, wpływ, daty, nazwy komponentów i link), bez pełnych komunikatów, aktualizacji i postmortemów; nie przypisujemy im licencji CC. Obowiązują [warunki GitHub](https://docs.github.com/en/site-policy/github-terms/github-terms-of-service#h-api-terms) i [Cloudflare](https://www.cloudflare.com/policies/terms/).
- [Natural Earth](https://www.naturalearthdata.com/about/terms-of-use/): domena publiczna, mapa uproszczona.
- `THIRD_PARTY_NOTICES.txt` zachowuje informacje o prawach i licencjach z zainstalowanych pakietów produkcyjnych i komponentów Next; MapLibre zachowuje również swój plik `LICENSE`.

Publiczna widoczność repozytorium nie nadaje autorskiemu kodowi aplikacji licencji MIT ani innej. Warunki zależności i danych pozostają odrębne.
