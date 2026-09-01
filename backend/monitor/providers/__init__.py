"""Adapters for the six approved sources; all network access is injected."""
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
        coverage="Globalne katastrofy o potencjalnym wpływie humanitarnym; nie wszystkie lokalne zdarzenia.",
        license_name="Reuse with attribution; prawa produktów źródłowych",
        license_url="https://gdacs.org/About/termofuse.aspx",
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
