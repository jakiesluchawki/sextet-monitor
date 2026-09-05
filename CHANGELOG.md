# Zmiany

## 03 — 5.09.2026

- Dodano dwa kanały do niezależnego zestawu publicznego: CERT Polska (indeks dat i odsyłaczy do komunikatów dla użytkowników) i IMGW (ostrzeżenia hydrologiczne z jawnymi datami UTC). Razem 11 kanałów; wspólne pochodzenie IMGW/MeteoAlarm nie zwiększa niezależnego potwierdzenia.
- CERT: bez kopiowania treści artykułów i źródłowych tytułów, bez kraju oddziaływania wywiedzionego z wydawcy. RSS obejmuje ostatnie 10 odsyłaczy, nie archiwum.
- IMGW: ważność do odwołania, kod suszy poza skalą porządkową, jawne aktualizacje bez powiązania historycznego i brak fikcyjnej geometrii. Lokalny monitor oznacza brak otwartego ostrzeżenia na pełnej aktualnej liście jako stan nieustalony, nie potwierdzenie odwołania.
- Osobna lista „Poza osią czasu” ujawnia zapisy bez wiarygodnej daty wymaganej dla danej kategorii. Nie dodaje ich do wybranego okna, mapy ani briefingu czasowego. Podpisy przypięć uwzględniają tę granicę.
- IODA pozostaje niepublikowanym pilotażem: niepotwierdzony feed zdarzeń i warunki redystrybucji. Nie dodano kont, kluczy, usług AI ani prywatnych danych.
- Testy parserów, sieci, kontraktu publicznego, filtrów oraz izolowanej bazy poprzedzają publikację. Sam wpis w changelogu nie potwierdza bieżącego wdrożenia.


## 0.2.1 — 5.09.2026

- Stałe skróty to świat, Europa i Polska. Pozostałe kraje i terytoria wybiera się z podpisanej listy; Turcja nie jest już wyróżniona. Do ośmiu własnych ulubionych obszarów pozostaje w tej przeglądarce, z obsługą zmian między kartami i błędów zapisu.
- Kraj filtruje rzeczywiste kody źródłowe, również w briefingu i udostępnianym linku. `scope=turkey` zachowuje zgodność, przechodząc na `country:TR`. Nieznane kody są odrzucane; wybór kraju nie dodaje nowych źródeł ani nie przypisuje mu globalnych komunikatów.
- Kamera wykorzystuje lokalne granice wybranego kraju, a gdy ich brakuje, rzeczywistą geometrię pasujących zapisów. Brak obu oznacza jawny widok świata, bez wymyślonych współrzędnych.
- Nadal dziewięć dotychczasowych kanałów. Badanie kandydatów na nowe źródła nie oznacza ich podłączenia. Publikacja wymaga osobnego sprawdzenia artefaktu i przeglądarki.

## 02 — 5.09.2026

Zakres zmian w kodzie publicznego interfejsu. Ten wpis nie potwierdza jeszcze publikacji na produkcji. Pozostaje dziewięć dotychczasowych kanałów danych; nie dodano źródeł, prywatnego API ani usług AI.

- Nowe widoki „Przegląd”, „Mapa i dane” i „Briefing”. Przegląd wybiera do ośmiu zapisów według jawnych reguł, z reprezentacją różnych kategorii i uzasadnieniem przy każdym wyróżnieniu. Nie wyznacza prawdopodobieństwa zagrożenia ani przyczyn.
- Globus i mapa 2D korzystają z lokalnego podkładu i oryginalnych geometrii. Kamera ma skróty obszarów, przybliżanie i dopasowanie danych. Nieznane pozycje nie stają się punktami; przy niedostępnym WebGL pozostaje lista. Jawny limit mapy wynosi 500 rekordów, a lista udostępnia cały wynik partiami.
- Zakresy świata, Europy, Polski i Turcji oraz okna 24 h, 72 h i 7 dni kończą się w chwili przygotowania zestawu. Filtr Europy używa jawnej listy kodów krajów, nie geometrii kontynentu. Rodzaje materiału zachowują osobne daty wystąpienia, publikacji i ważności; oś czasu ma tabelę oraz ostrzeżenie przed sumowaniem wielokrotnie uwzględnionych ostrzeżeń.
- Dosłowne wyszukiwanie uwzględnia tytuły, opisy, kraje i nazwy źródeł, także bez polskich znaków. Filtry źródła i kategorii nie wysyłają zapytań do prywatnego monitora. Dotychczasowe szczegółowe filtry i oś czasu pozostają w `DetailedExplorer`.
- Do 30 przypięć zapisuje się lokalnie jako publiczne UUID, bez kopii opisów. Zarządzanie w briefingu pozwala usunąć również identyfikator nieobecny w bieżącym zestawie. Odczyt storage przed pojedynczą zmianą ogranicza nadpisywanie stanu innej karty; pamięć przeglądarki nie zapewnia transakcji między kartami ani trwałości po jej wyczyszczeniu.
- Lokalny punkt porównania zawiera czas zestawu oraz pary UUID–niekryptograficzny odcisk treści. Ponowne pobranie i przebudowa bazy nie tworzą pozornych zmian. Starszy zestaw nie cofa zapisanego punktu, a sprzecznych zestawów o identycznym czasie nie porządkujemy arbitralnie. Brak rekordu nie jest oznaczany jako zakończenie zdarzenia.
- Briefing z wyróżnień albo przypięć zachowuje daty, oryginalne adresy, pochodzenie i ograniczenia. Limit wynosi 12 pozycji. Można skopiować tekst, zapisać Markdown lub wywołać druk/PDF; nie ma automatycznej wysyłki do Signala ani do innych usług.
- Linki zapisują wyłącznie zatwierdzone ustawienia widoku i opcjonalny publiczny UUID w hashu. Usuwają zastane query/hash z kopiowanego adresu. Link odnosi się do najnowszego dostępnego zestawu, nie archiwizuje danych i nie przenosi lokalnej listy przypięć ani punktu porównania.
- Testy obejmują reguły przeglądu i czasu, kamerę mapy, wyszukiwanie, bezpieczne linki oraz ograniczone i wersjonowane dane przeglądarki. Testy jednostkowe nie zastępują sprawdzenia WebGL, telefonu, klawiatury i działającej publikacji. Nie wykonano formalnej próby 72 godzin ani nie ustanowiono SLA świeżości.

## 1.09.2026

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
