# Dalsze kroki i warunki odbioru

Aktualizacja 1.09.2026: poprawki audytu i regresje opisuje [CHANGELOG](CHANGELOG.md); osobny publiczny podgląd [PUBLIC_PAGES](PUBLIC_PAGES.md). Poniższe liczby z 27.08 są historycznym punktem odniesienia.

Stan 27.08.2026: **funkcjonalna pierwsza wersja działa lokalnie; etap stabilizacji pozostaje otwarty**. Pobrano 5/5 anonimowych źródeł, potwierdzono 23/23 sprawdzenia HTTP, zachowanie 4721/4721 zdarzeń po restarcie i zgodność odtworzonej kopii. Potwierdzone liczby testów i stan końcowego odbioru przeglądarki opisuje raport Phase 1 (historyczny materiał lokalny, poza repozytorium). Plan Phase 0 (historyczny materiał lokalny, poza repozytorium) jest historycznym punktem odniesienia, nie bieżącą listą ukończonych działań.

## Obecna implementacja

Istnieją cztery usługi, model PostGIS, sześć adapterów, zapis obserwacji i rewizji, reguły tożsamości/pochodzenia, mapa i lista, panel dowodów, statusy źródeł, ograniczone pytania PL oraz briefing deterministyczny. AI pozostaje wyłączone, Radar oczekuje na token.

Testy deterministyczne obejmują poprawne i błędne formaty, brak czasu/geometrii, języki CAP, Cancel/Update, epizody GDACS, KEV jako informację o podatności i zachowanie po HTTP 429. Potwierdzono 300 testów backendu, 42 przypadki z osobną natywną bazą PostGIS i 26 testów frontendu oraz TypeScript/Ruff. Zestaw uruchomiony przy PostGIS liczył 51 kontroli, w tym 9 powtórzonych testów czystych reguł; nie doliczamy ich ponownie. Sprawdzono m.in. rozdzielenie źródła i dwa kolejne odczyty po obu stronach bez ponownego scalenia. Sprawdzono również rzeczywistą kopię: zgodność SHA-256 oraz wszystkich 13 tabel z manifestem, usunięcie bazy kontrolnej i brak zmiany bazy roboczej. Dokładne wyniki wykazuje raport odbioru. Potwierdzono główne przepływy w przeglądarce i końcowy odczyt poprawek mobilnych przy 390 × 844 px. Ocena użytkownika oraz formalna próba 72 godzin pozostają otwarte.

## Bieżąca bramka: pełny przepływ

| Obszar | Dowód i stan odbioru |
|---|---|
| Pięć anonimowych źródeł | Potwierdzono rzeczywiste pobranie i dane w API: 5/5; w interfejsie odczytano rzeczywiste materiały USGS i CISA oraz statusy źródeł. |
| Tożsamość i korekty | Powtórka bez duplikatu; nowszy stan niecofnięty starszym GDACS/CAP; odwołanie i wygaśnięcie widoczne |
| Zgodność widoków | Testy HTTP potwierdzają zgodność ID odpowiedzi z filtrowaną listą. Mapa, lista i panel dowodów odczytane w przeglądarce; poprawki mobilnego przewijania, legendy i etykiety wyłączonego źródła potwierdzone po końcowym buildzie. |
| Rzeczywiste pytania i briefing | Potwierdzono pytania PL/12 h, 800 km i minimum dwóch źródeł, a także dwa briefingi: fakty ze źródłami, oddzielone tło i brak wymyślonych zmian przy powtórce. |
| Odporność i lokalność | Restart: 4721 → 4721, utracone ID: 0; 23/23 kontrole HTTP. To nie próba 72 godzin ani pełny audyt bezpieczeństwa. |
| Kopia zapasowa — potwierdzona | Jedna rzeczywista kopia odtworzona do osobnej bazy, automatyczna zgodność SHA-256 i 13 tabel z manifestem tego samego snapshotu; bez nadpisania bazy roboczej |
| Ocena użytkownika | Kilka briefingów pokazuje użyteczne zmiany i pozwala sprawdzić materiał źródłowy |

Żaden pojedynczy HTTP 200, test parsera, build ani start kontenera nie zamyka całej tej bramki. Bez poprawnego odczytu po autoryzacji opisujemy stan jako „pięć źródeł, Radar oczekuje”, nie sześć sprawdzonych integracji.

## Osobna próba 72 godzin

**Nie uruchomiono jej i nie uznano za zaakceptowaną.** Zwykły uruchomiony worker nie oznacza rozpoczęcia kontrolowanej próby. Po osobnej zgodzie należy ustalić czas startu/końca, zachowanie przy uśpieniu Maca, rejestrowane błędy, rozmiar bazy i metodę porównania odtworzenia. Nie zmieniamy ustawień energii ani autostartu przy okazji.

Próba ma sprawdzić działanie w lokalnym środowisku, nie wyznaczać SLA dostawców. Odbiór wymaga rzeczywistych zapisów z całego okresu, a nie estymacji na podstawie krótkiego testu.

## Późniejsze rozszerzenia

Najpierw wybór jednej niezaspokojonej potrzeby i osobny przegląd praw/dostępu. Kandydaci: FIRMS jako detekcje termiczne, IMGW hydro/pomiary, ReliefWeb, diagnostyka Internetu lub licencjonowane dane lotnicze. EMSC nie daje automatycznie drugiego niezależnego potwierdzenia USGS.

Dalej można rozważyć watchlisty, kalibrowany model anomalii, powiadomienia, opcjonalny model językowy albo bezpieczny dostęp zdalny. Żadna z tych funkcji nie działa tylko dlatego, że przewidziano kontrakt rozszerzenia. Śledzenie wojsk, GNSS, AIS, pełne NOTAM i wyjaśnianie ruchów ropy nadal nie są obsługiwane.
