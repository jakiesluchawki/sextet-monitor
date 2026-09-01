# Kontrakt lokalnego API

Interfejs wysyła żądania do `/api/*` w obrębie tego samego originu (same-origin). Kontrolowane proxy Next przekazuje je do FastAPI. Przeglądarka nie pobiera danych bezpośrednio ze źródeł. Żądania `POST` wymagają nagłówka `X-Monitor-Request: 1` i dozwolonego `Origin`. Jedyny port hosta: `127.0.0.1:3180`.

## Lista i czas

`GET /api/events` przyjmuje następujące parametry:

| Parametr | Zakres lub znaczenie |
|---|---|
| `window_hours` | 1–720 godzin; domyślnie 24 |
| `time_basis` | `occurred`, `changed`, `published` lub `validity` |
| `since`, `until` | Opcjonalne daty w formacie ISO |
| `country` | Kod kraju ISO2 |
| `region` | Obsługiwana wartość: `europe` |
| `category` | `earthquake`, `disaster`, `weather`, `aviation`, `cyber`, `internet`, `space_weather` |
| `severity_min` | 0–4 |
| `min_sources` | 1–10 niezależnych źródeł |
| `lat`, `lon`, `radius_km` | Współrzędne i promień; trzeba podać komplet trzech parametrów |
| `include_inactive` | Bez jawnego ustawienia: `false` dla `occurred`, `true` dla `changed`, `published` i `validity`. Jawne `false` zawsze wyklucza zakończone rekordy |
| `limit` | 1–1000 rekordów; domyślnie 300 |

- `occurred`: udokumentowany czas incydentu/pomiaru; `NULL` nie jest zastępowany czasem pobrania.
- `changed`: zmiana zapisu lokalnego, nie nowe zdarzenie w świecie.
- `published`: `issued_at`; publikacja znana tylko co do dnia dopasowuje się przez przecięcie dnia UTC z oknem. Godzina pozostaje nieznana.
- `validity`: źródłowy przedział ważności przecina okno. Brak początku wyklucza dopasowanie; brak końca pozostaje nieznany. Status jest bieżący, nie odtworzony historycznie.

Odpowiedź: `{items, total, shown, mapped, unlocated, truncated, query, source_health, generated_at, limitations}`. Lista, mapa i oś czasu pokazują ten sam ograniczony podzbiór; `total` liczy wszystkie dopasowania. `min_sources` liczy niezależne pochodzenie dowodów, nie odnośniki.

`EventSummary` zawiera identyfikator, rodzaj i kategorię, tytuł i opis, źródłowe daty i okres ważności, `first_seen_at`, `last_seen_at`, `last_changed_at`, kraje, geometrię albo `NULL`, dokładność, wagę i jej uzasadnienie, stan cyklu życia, źródła i niezależne pochodzenie dowodów, oryginalny URL oraz tagi. `anomaly_score` pozostaje `NULL`. Waga 0 oznacza nieokreśloną wagę, nie brak zagrożenia.

## Dowody, źródła i pytania

`GET /api/events/{id}` zwraca podsumowanie oraz `evidence`, `revisions` i `relations`. Dowód zawiera źródło, ID dostawcy, URL, czas pobrania, daty źródłowe, opcjonalny `source_snapshot_at`, pochodzenie, hash, surowe dane (`raw`) albo `NULL`, `raw_retained` oraz atrybucję i licencję. Czas snapshotu CISA pochodzi z katalogowego `dateReleased`, nie z daty ataku ani `dateAdded`.

`GET /api/sources`: `{items, generated_at}`. Stany: `pending`, `ok`, `ok_empty`, `partial`, `error`, `stale`, `needs_credentials`, `disabled`. Czasy ostatniej próby i ostatniego sukcesu, liczba rekordów, pokrycie, częstotliwość lokalnego odpytywania i licencja pozostają jawne.

`POST /api/query` z `{question: string}` zwraca `{supported, answer, interpretation, query_explanation, events, facts, inferences, limitations, source_health, generated_at, total, shown, truncated}`.

Parser obsługuje ograniczony język polski. Dla CISA domyślną podstawą czasu jest publikacja, a dla ostrzeżeń EASA i pogodowych — ważność. Historyczny okres uwzględnia zakończone rekordy, o ile pytanie nie żąda bieżących. Relacje pochodzą z tej samej transakcji i dotyczą cytowanych faktów, nie wymyślonych przyczyn.

## Briefing

`POST /api/briefings` z `{window_hours: 24, country?: string}` zapisuje zakres i kursor dla danego kraju i okna czasowego. Pierwszy briefing obejmuje zadane okno; następne obejmują zmiany od poprzedniego briefingu tego samego zakresu. Pozostałe filtry widoku nie ograniczają briefingu.

Zapis obejmuje `id`, `answer`, `since`, `until`, `scope`, `first_briefing`, `sections`, `facts`, `inferences`, `limitations`, `source_health`, `generated_at` oraz liczniki:

- `total` i `processed_count`: wszystkie rekordy wybranego zakresu po odsunięciu tła pierwszego importu. Są odczytywane strumieniowo, partiami po 250 rekordów.
- `shown`: najwyżej 30 faktów w tekście.
- `citable_count`: rekordy, które można zacytować z odnośnikami.
- `omitted_fact_count`: fakty pominięte wyłącznie ze względu na długość narracji.
- `historical_count`: tło historyczne rozpoznane w przetworzonym strumieniu; zawiera się w `processed_count`.
- `initial_import_background_count`: tło pierwszego importu odsunięte przed przetwarzaniem strumienia; nie wchodzi do `total` ani `processed_count`.
- `uncitable_count`: rekordy bez danych niezbędnych do cytowania.

`truncated=true` oznacza skróconą narrację, nie nieodczytaną partię. Kursor przesuwa się dopiero po pełnym odczycie, sprawdzeniu zgodności licznika i walidacji odpowiedzi; wyjątek cofa transakcję.

`GET /api/briefings/latest` zwraca ostatni zapis globalnie albo `NULL`. Interfejs pokazuje kraj i zakres zapisane w tym briefingu. Nowe liczniki mogą mieć wartość `NULL` w starych briefingach.

`GET /api/health`: `{status, version, database, ai_mode: 'off', timezone: 'Europe/Warsaw'}`. Brak API powoduje błąd/ponowienie, nigdy dane demonstracyjne.

## Publiczny podgląd

GitHub Pages nie implementuje tego API. Osobny interfejs odczytuje statyczny `snapshot.json` zgodnie z [PUBLIC_PAGES.md](PUBLIC_PAGES.md). Nie ma proxy do localhost, klucza do bazy ani publicznego endpointu briefingu.
