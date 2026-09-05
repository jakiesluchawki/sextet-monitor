"""Reviewed public-data adapters; all network access is injected."""
from __future__ import annotations

from importlib import import_module

from monitor.contracts import Fetcher, ProviderBatch, SourceSpec
from .common import MissingCredentials, ProviderError

SOURCES: dict[str, SourceSpec] = {
    "usgs": SourceSpec(
        id="usgs", name="USGS · trzęsienia ziemi", poll_interval_seconds=300,
        coverage="Globalny katalog; małe wstrząsy nie są rejestrowane jednakowo wszędzie.",
        license_name="USGS public domain; zachowano źródła partnerów",
        license_url="https://www.usgs.gov/faqs/are-usgs-reportspublications-copyrighted",
        attribution="U.S. Geological Survey i wskazane sieci sejsmiczne",
    ),
    "gdacs": SourceSpec(
        id="gdacs", name="GDACS · większe katastrofy", poll_interval_seconds=900,
        coverage="Globalne katastrofy o potencjalnym wpływie humanitarnym. Automatyczne oceny GDACS nie zastępują krajowych ostrzeżeń; nie wszystkie lokalne zdarzenia.",
        license_name="Reuse with attribution; prawa produktów źródłowych",
        license_url="https://www.gdacs.org/Documents/2025/GDACS_Terms_of_use_Mar_25.pdf",
        attribution="Global Disaster Awareness and Coordination System, GDACS",
    ),
    "meteoalarm": SourceSpec(
        id="meteoalarm", name="MeteoAlarm · ostrzeżenia pogodowe", poll_interval_seconds=600,
        coverage="Domyślnie Polska; oficjalne ostrzeżenia krajowych służb europejskich.",
        license_name="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="MeteoAlarm/EUMETNET i krajowy wystawca komunikatu; dane przetworzone",
    ),
    "easa_czib": SourceSpec(
        id="easa_czib", name="EASA · biuletyny CZIB", poll_interval_seconds=1800,
        coverage="Wybrane strefy ryzyka lotniczego; nie NOTAM, nie pozycje lotów ani pełne granice FIR.",
        license_name="EASA reproduction with acknowledgement",
        license_url="https://www.easa.europa.eu/en/copyright-disclaimer",
        attribution="European Union Aviation Safety Agency (EASA)",
    ),
    "cisa_kev": SourceSpec(
        id="cisa_kev", name="CISA · wykorzystywane podatności", poll_interval_seconds=3600,
        coverage="Globalny katalog podatności; bez lokalizacji i dat poszczególnych ataków.",
        license_name="CC0 1.0",
        license_url="https://github.com/cisagov/kev-data/blob/develop/LICENSE",
        attribution="Cybersecurity and Infrastructure Security Agency (CISA)",
    ),
    "cert_pl": SourceSpec(
        id="cert_pl", name="CERT Polska · komunikaty dla użytkowników", poll_interval_seconds=3600,
        coverage="Indeks dat i odsyłaczy do ostatnich 10 komunikatów RSS dla użytkowników. Bez artykułów, oceny wagi, pełnego archiwum i przypisywania kraju oddziaływania na podstawie siedziby wydawcy.",
        license_name="Indeks odsyłaczy; treść u wydawcy (NASK)",
        license_url="https://moje.cert.pl/terms/",
        attribution="CERT Polska / NASK; indeks dat, identyfikatorów i odsyłaczy z publicznego RSS. Tytuły indeksu wygenerowano; treść komunikatów pozostaje u wydawcy.",
    ),
    "imgw_hydro": SourceSpec(
        id="imgw_hydro", name="IMGW · ostrzeżenia hydrologiczne", poll_interval_seconds=900,
        coverage="Polska: komunikaty hydrologiczne, nie pomiary stacji ani mapa zalania. Daty UTC z oficjalnej listy; ważność do odwołania zachowano. Zniknięcie z listy nie jest dowodem odwołania. IMGW jest też źródłem polskich danych MeteoAlarm.",
        license_name="Regulamin IMGW-PIB; użytek niezarobkowy, atrybucja i oznaczenie przetworzenia",
        license_url="https://hydro.imgw.pl/#/regulamin",
        attribution="Źródłem pochodzenia danych jest Instytut Meteorologii i Gospodarki Wodnej – Państwowy Instytut Badawczy. Dane Instytutu Meteorologii i Gospodarki Wodnej – Państwowego Instytutu Badawczego zostały przetworzone.",
    ),
    "nasa_eonet": SourceSpec(
        id="nasa_eonet", name="NASA EONET · pożary, wulkany i burze", poll_interval_seconds=900,
        coverage="Kuratorskie metadane globalnych pożarów, wulkanów i silnych burz; okno API 30 dni, maksymalnie 400 rekordów. Przybliżone miejsce i czas; nie wszystkie lokalne zdarzenia.",
        license_name="NASA EONET — metadane informacyjne; zachowano prawa źródeł",
        license_url="https://eonet.gsfc.nasa.gov/what-is-eonet",
        attribution="NASA EONET i źródła wskazane w metadanych; dane przetworzone, bez obrazów i poparcia NASA",
    ),
    "noaa_swpc": SourceSpec(
        id="noaa_swpc", name="NOAA SWPC · pogoda kosmiczna", poll_interval_seconds=300,
        coverage="Biuletyny SWPC: obserwowane alerty i podsumowania oraz osobno prognozy i ostrzeżenia. Do 400 komunikatów z ostatnich 30 dni; bez lokalizacji zakłóceń GPS i pełnego archiwum.",
        license_name="NOAA/NWS public domain; bez sugerowania poparcia",
        license_url="https://www.weather.gov/disclaimer",
        attribution="NOAA/NWS Space Weather Prediction Center; dane przetworzone, oryginalny komunikat w źródle",
    ),
    "github_status": SourceSpec(
        id="github_status", name="GitHub Status · dostępność usług", poll_interval_seconds=300,
        coverage="Ostatnie 50 incydentów zgłoszonych przez GitHub; niepełne archiwum, bez globalnego pomiaru Internetu i lokalizacji.",
        license_name="Publiczne API; metadane statusu bez pełnych komunikatów",
        license_url="https://docs.github.com/en/site-policy/github-terms/github-terms-of-service#h-api-terms",
        attribution="GitHub Status; metadane przetworzone, komunikaty dostępne u źródła",
    ),
    "cloudflare_status": SourceSpec(
        id="cloudflare_status", name="Cloudflare Status · dostępność usług", poll_interval_seconds=300,
        coverage="Ostatnie 50 incydentów zgłoszonych przez Cloudflare; niepełne archiwum, bez globalnego pomiaru Internetu i lokalizacji. To nie Cloudflare Radar.",
        license_name="Publiczne API; metadane statusu bez pełnych komunikatów",
        license_url="https://www.cloudflare.com/policies/terms/",
        attribution="Cloudflare Status; metadane przetworzone, komunikaty dostępne u źródła",
    ),
    "cloudflare_radar": SourceSpec(
        id="cloudflare_radar", name="Cloudflare Radar · zakłócenia Internetu",
        poll_interval_seconds=300, requires_key=True,
        coverage="Widoczność Cloudflare; nie cały Internet. Brak tokenu blokuje pobieranie.",
        license_name="CC BY-NC 4.0",
        license_url="https://creativecommons.org/licenses/by-nc/4.0/",
        attribution="Cloudflare Radar; osobisty użytek niekomercyjny; dane przetworzone",
    ),
}

_MODULES = {
    "usgs": "usgs", "gdacs": "gdacs", "meteoalarm": "meteoalarm",
    "easa_czib": "easa", "cisa_kev": "cisa", "cloudflare_radar": "radar",
    "nasa_eonet": "eonet", "noaa_swpc": "swpc",
    "github_status": "github_status", "cloudflare_status": "cloudflare_status",
    "cert_pl": "cert", "imgw_hydro": "imgw_hydro",
}


async def collect(
    source_id: str, fetcher: Fetcher, config: dict[str, str] | None = None,
) -> ProviderBatch:
    """Collect one source. Invalid feeds raise; partial records remain explicit."""
    if source_id not in _MODULES:
        raise ProviderError(f"Nieznane źródło: {source_id!r}")
    module = import_module(f"{__name__}.{_MODULES[source_id]}")
    return await module.collect(fetcher, config or {})


__all__ = ["SOURCES", "collect", "MissingCredentials", "ProviderError"]
