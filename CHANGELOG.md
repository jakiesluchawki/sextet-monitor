# Zmiany — 1.09.2026

Poprawki po audycie lokalnej wersji z 27.08.2026.

Nazwa wspólnego projektu i publicznej wersji to **Sextet Monitor**. Właściciel zatwierdził publiczne repozytorium oraz Pages. Zmieniono widoczną markę, slug, ikonę i metadane linku; zachowano techniczne identyfikatory istniejącej lokalnej instalacji, aby nie tworzyć pustego wolumenu bazy.

Rozszerzenie źródeł dla grupy:

- Pages obejmuje dziewięć źródeł zamiast trzech: dołączono GDACS, EASA, NASA EONET, NOAA SWPC, GitHub Status i Cloudflare Status. Każdy dostawca ma jawne pokrycie, pochodzenie, daty i ograniczenia.
- Dodano wybór konkretnego źródła oraz rozdzielenie liczników udanych niepustych odczytów, pustych odpowiedzi, częściowych danych i błędów. Kategorie i podstawa czasu dostosowują się do rodzaju materiału; brak geolokalizacji nie usuwa danych z listy.
- Publiczny odczyt toleruje awarię jednego dostawcy. Opcjonalnie zachowuje poprzednie, zweryfikowane dane wyłącznie z publicznej strony, z dawnymi datami i jawnym błędem źródła. Awaria wszystkich źródeł nadal zatrzymuje publikację.
- SWPC odróżnia obserwacje od prognoz; ważność prognozy jest przeliczana bez tworzenia fałszywego nowego odczytu. Nie wytwarza punktów rzekomych zakłóceń GPS.
- Statusy operatorów zachowują tylko metadane incydentów; nie kopiują tekstów aktualizacji ani postmortemów i nie udają globalnego pomiaru Internetu.

- Briefing przetwarza cały zakres zmian partiami po 250 rekordów; limit 30 faktów dotyczy tekstu. W razie błędu kursor nie jest zapisywany. Briefing pokazuje rzeczywisty okres, kraj i liczniki.
- Dane CISA są filtrowane według publikacji, a ostrzeżenia według ważności. Historia uwzględnia zakończone komunikaty bez wymyślania historycznego statusu ani czasu ataku.
- Pytania i briefingi otrzymują rzeczywiste relacje między cytowanymi faktami.
- Dowody odświeżają się razem z rekordem i ręcznym odświeżeniem; opóźnione żądania są anulowane, w razie błędu poprzedni odczyt pozostaje widoczny i jawnie oznaczony.
- Zmiana podstawy czasu zachowuje zakres; spóźniona odpowiedź pytania nie nadpisuje ręcznych filtrów. Legenda dotyczy geometrii faktycznie narysowanej na mapie.
- CAP aktywuje się z początkiem okresu ważności również przy niezmienionym payloadzie. Wygasłe i wycofane komunikaty nie reaktywują się po cofnięciu zegara.
- Przeklasyfikowanie USGS wycofuje wcześniej znany wstrząs także przy zmianie głównego ID ze znanym oficjalnym aliasem. Nowych wybuchów kamieniołomowych nie importujemy; konflikt aliasów wymaga oceny.
- Nowa wersja normalizatora może poprawić interpretację bieżącego payloadu mimo skorygowania czasu źródłowego wstecz; stary materiał nie może nadpisać nowego tylko z powodu ponownego pobrania.
- Błędny cykl odwołań CAP jest izolowany punktem zapisu transakcji (savepointem) i nie cofa poprawnych, niezależnych rekordów w tej samej partii.
- Chronologię CISA A→B→A wyznacza źródłowy czas snapshotu. Ponowienie odczytu nie gubi nieprzetworzonych rekordów; starsze i sprzeczne odczyty nie zastępują nowszego stanu.
- MapLibre jest ładowane jako niezmienione lokalne moduły ESM; build publiczny odrzuca artefakt zawierający ścieżkę komputera budującego.
- Dodano CI oraz osobny statyczny podgląd Pages z niezależnie pobranych publicznych źródeł, bez eksportu prywatnej bazy.

Migracja `0002` dodaje pole `provider_records.source_snapshot_at`, które dopuszcza wartość `NULL`. Stare rekordy CISA uzupełnią to pole przy pierwszym poprawnie datowanym odczycie. Nie zmieniamy `occurred_start` ani nie tworzymy rewizji tylko dlatego, że katalog został ponownie opublikowany. Normalizator pozostaje w wersji 1; zmiana logiki nie wymaga masowego sztucznego ponownego importu.

Nie wykonano formalnej próby 72 godzin ani testu wszystkich awarii dostawców. Przeklasyfikowanie USGS poza pobieranym oknem 1 lub 7 dni wymaga osobnego uzupełnienia danych historycznych (backfillu). [Wersja publiczna i jej ograniczenia](PUBLIC_PAGES.md).
