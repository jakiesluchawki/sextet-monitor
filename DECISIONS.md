# Decyzje implementacyjne

Zatwierdzony zakres Phase 1: 27.08.2026. Historyczne propozycje pozostają w Phase 0 (historyczny materiał lokalny, poza repozytorium); poniżej opisujemy wdrażane decyzje i ich skutki. End-to-end weryfikacja w toku; pełny wynik w raporcie odbioru Phase 1 (historyczny materiał lokalny, poza repozytorium).

| Decyzja | Uzasadnienie i konsekwencja |
|---|---|
| Własna implementacja od zera | Nie kopiujemy World Monitor ani jego kodu AGPL. Zależności i źródła danych nadal mają odrębne licencje. |
| Cztery usługi Compose | Next.js/MapLibre, FastAPI, worker, PostgreSQL/PostGIS. Bez Redis, Kafka i orkiestracji wieloagentowej w aplikacji. |
| Izolowany runtime na Macu | Colima 4 CPU / 6 GiB / 24 GiB, bez home mount i autostartu. Globalny Node i kontekst Dockera nie są zmieniane. |
| Dostęp tylko localhost:3180 | Bez publikacji, LAN i tuneli; brak logowania oznacza zaufanie do lokalnego systemu i jego użytkowników. |
| Sześć konkretnych adapterów | USGS, GDACS, MeteoAlarm, EASA CZIB, CISA KEV i Radar. Pięć anonimowych; Radar nie wysyła żądań bez tokenu. |
| Typ materiału jest jawny | Incydent, biuletyn, podatność i pomiar nie są zamienne. KEV nie staje się lokalnym cyberatakiem, CZIB nie staje się NOTAM. |
| Nieznane pola pozostają nieznane | Brak pozycji nie daje punktu (0,0); data katalogu nie daje czasu ataku. Dzienny UTC anchor ma etykietę, nie pozorną dokładność. |
| Tylko twarde powiązania tożsamości | Identyfikatory źródłowe i referencje tego samego nadawcy CAP. Bliskie raporty tworzą co najwyżej relację, nie automatyczne scalenie. |
| Niezależność po pochodzeniu | Dystrybutor USGS i USGS to jedno pochodzenie. Niejednoznaczna lista upstreamów nie daje wielu potwierdzeń; brak rozpoznania nie zwiększa pewności. |
| Ręczne rozdzielenie z audytem | `detach-source`: podgląd, decyzja z powodem i automatyczny backup przed zapisem. Zachowana historia; reguła dotyczy obecnych rekordów obu stron, nie nieznanych przyszłych ID. |
| Świeżość odrębna od HTTP | USGS `metadata.generated` starsze niż 20 min oznacza lokalnie `stale`; to nie SLA źródła ani stwierdzenie braku zdarzeń. |
| Wersje obserwacji i normalizatora | Zachowujemy dowód korekty. Nowszy odczyt starego epizodu nie wystarcza do cofnięcia aktualnego stanu. |
| Ważność nie zależy od obecności w feedzie | CAP Actual może być wygasły; Cancel wycofuje wskazany komunikat. Zniknięcie z ograniczonego feedu nie jest dowodem zakończenia katastrofy. |
| Jedna semantyka filtrów | Mapa, lista, zapytania i briefing korzystają z EventQuery. Tryb zmian jest jawnie inny niż czas zdarzenia. |
| AI wyłączone | Polski parser i briefing deterministyczny nie wymagają modelu. Nie ma zgody na model w chmurze, pobranie wag ani wysyłkę źródeł do LLM. |
| Bez sztucznego wyniku anomalii | anomaly_score pozostaje null. Ważność ma jawne reguły, nie jest skalibrowaną prognozą ani prawdopodobieństwem. |
| Retencja i kopie lokalne | Raw 30 dni; stare zakończone zdarzenia mogą być usunięte po 180 dniach. Trwające pozostają. Backup i manifest pochodzą z jednego snapshotu; odtworzenie porównuje 13 tabel z manifestem. Pliki nie są szyfrowane przez aplikację. |
| Licencje danych oceniane osobno | Radar tylko w zaakceptowanym osobistym, niekomercyjnym zakresie. Brak automatycznej zgody na publikację lub komercyjny eksport. |

Licencji udzielanej do autorskiego kodu nie ustalano. [THIRD_PARTY.md](THIRD_PARTY.md) dokumentuje odrębne warunki zależności, nie stanowi pliku LICENSE aplikacji.

Uruchomienie zwykłego workera jest częścią bieżącej implementacji. Nie tworzymy automatyzacji Codex ani zadania budzącego ten wątek.

Próba 72 godzin, nowe źródła wymagające konta lub opłat, modele lokalne/chmurowe, otwarcie dostępu zdalnego, autostart hosta/VM oraz publikacja wymagają osobnego uzgodnienia. Samo istnienie kontraktu adaptera lub przyszłego rozszerzenia nie oznacza wdrożenia tych funkcji.
