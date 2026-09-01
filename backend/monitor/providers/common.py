"""Conservative normalization shared by source-specific parsers.

Date-only values, when explicitly allowed, use UTC calendar-day anchors and
must be labelled with day precision by their caller. Naive timestamps are
never silently interpreted in the machine's local timezone.
"""
from __future__ import annotations

import json
import math
import re
from datetime import date, datetime, time, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit

from defusedxml import ElementTree as SafeET
from monitor.contracts import FetchedDocument, ProviderBatch, utcnow

UTC = timezone.utc


def _bounded_retry_after(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return min(86400, max(60, value))
    return None


def retry_after(error: Exception) -> int | None:
    """Carry only the numeric transport backoff, never its arbitrary message."""
    return _bounded_retry_after(getattr(error, "retry_after_seconds", None))


class ProviderError(ValueError):
    """The response cannot be interpreted as a valid provider feed."""

    def __init__(self, message: str, *, retry_after_seconds: int | None = None):
        super().__init__(message)
        self.retry_after_seconds = _bounded_retry_after(retry_after_seconds)


class MissingCredentials(ProviderError):
    """A configured source requires credentials before any network access."""


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.time_values: list[str] = []
        self._ignored = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self._ignored += 1
        if tag == "time":
            value = dict(attrs).get("datetime")
            if value:
                self.time_values.append(value)
        if tag in {"br", "p", "div", "li"} and not self._ignored:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self._ignored:
            self._ignored -= 1
        if tag in {"p", "div", "li"} and not self._ignored:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self._ignored:
            self.parts.append(data)


def plain(value: Any, limit: int = 12000) -> str:
    if value is None:
        return ""
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        raise ValueError("expected text")
    parser = _TextExtractor()
    parser.feed(str(value))
    text = unescape("".join(parser.parts))
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    return re.sub(r"\s+", " ", text).strip()[:limit]


def identifier(value: Any, field: str = "id") -> str:
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise ValueError(f"missing {field}")
    result = str(value).strip()
    if not result or len(result) > 512 or re.search(r"[\s\x00-\x1f]", result):
        raise ValueError(f"invalid {field}")
    return result


def required_title(value: Any) -> str:
    result = plain(value, 800)
    if not result:
        raise ValueError("missing title")
    return result


def ensure_document(doc: FetchedDocument, source: str) -> None:
    if not (200 <= doc.status < 300 or (doc.not_modified and doc.body)):
        raise ProviderError(
            f"{source}: HTTP {doc.status}",
            retry_after_seconds=60 if doc.status == 429 else None,
        )
    if not doc.body:
        raise ProviderError(f"{source}: pusta odpowiedź, nie potwierdzony pusty zbiór")


def _invalid_json_constant(value: str):
    raise ValueError(f"non-finite JSON number: {value}")


def json_document(doc: FetchedDocument, source: str) -> dict[str, Any]:
    ensure_document(doc, source)
    try:
        result = json.loads(doc.body, parse_constant=_invalid_json_constant)
    except (ValueError, UnicodeError) as exc:
        raise ProviderError(f"{source}: nieprawidłowy JSON") from exc
    if not isinstance(result, dict):
        raise ProviderError(f"{source}: nieprawidłowy obiekt główny JSON")
    return result


def xml_document(doc: FetchedDocument, source: str, root_tag: str):
    ensure_document(doc, source)
    try:
        root = SafeET.fromstring(
            doc.body, forbid_dtd=True, forbid_entities=True, forbid_external=True,
        )
    except Exception as exc:
        raise ProviderError(f"{source}: nieprawidłowy lub niedozwolony XML") from exc
    if root.tag != root_tag:
        raise ProviderError(f"{source}: nieoczekiwany element główny XML")
    return root


def warn(warnings: list[str], message: str) -> None:
    # A malformed large feed must not create an unbounded health message.
    if message not in warnings and len(warnings) < 100:
        warnings.append(message[:500])


def reject(batch: ProviderBatch, source: str, index: int, exc: Exception) -> None:
    batch.rejected_count += 1
    # Do not copy response bodies, URLs with credentials, or arbitrary exception
    # payloads into source-health logs.
    warn(batch.warnings, f"{source}: odrzucono rekord {index} ({type(exc).__name__})")


def timestamp(
    value: Any, *, warnings: list[str] | None = None, field: str = "time",
    allow_date: bool = False, day_first: bool = False,
) -> tuple[datetime | None, str]:
    if value is None or value == "":
        return None, "unknown"
    try:
        if isinstance(value, datetime):
            parsed = value
            precision = "second"
        elif isinstance(value, str):
            token = value.strip()
            if "<" in token:
                parser = _TextExtractor()
                parser.feed(token)
                token = parser.time_values[0] if parser.time_values else plain(token)
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", token):
                if not allow_date:
                    raise ValueError("date-only value")
                return datetime.combine(date.fromisoformat(token), time(), UTC), "day"
            if day_first and re.fullmatch(r"\d{2}/\d{2}/\d{4}", token):
                if not allow_date:
                    raise ValueError("date-only value")
                parsed_date = datetime.strptime(token, "%d/%m/%Y").date()
                return datetime.combine(parsed_date, time(), UTC), "day"
            try:
                parsed = datetime.fromisoformat(token.replace("Z", "+00:00"))
                clock = re.search(r"[T ](\d{2})(?::(\d{2}))?(?::(\d{2}))?", token)
                precision = (
                    "second" if clock and clock[3] is not None else
                    "minute" if clock and clock[2] is not None else "hour"
                )
            except ValueError:
                parsed = parsedate_to_datetime(token)
                precision = "second"
        else:
            raise ValueError("unsupported timestamp")
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("timestamp without offset")
        return parsed.astimezone(UTC), precision
    except (ValueError, TypeError, OverflowError, OSError, AttributeError):
        if warnings is not None:
            warn(warnings, f"{field}: niepoprawny czas lub brak strefy; pozostawiono nieznany")
        return None, "unknown"


def milliseconds(value: Any, warnings: list[str], field: str):
    if value is None:
        return None
    try:
        if isinstance(value, bool):
            raise ValueError("boolean timestamp")
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("non-finite timestamp")
        return datetime.fromtimestamp(number / 1000, UTC)
    except (ValueError, TypeError, OverflowError, OSError):
        warn(warnings, f"{field}: niepoprawny czas epoch; pozostawiono nieznany")
        return None


def observed_now(doc: FetchedDocument) -> datetime:
    if doc.fetched_at is not None:
        result, _ = timestamp(doc.fetched_at)
        if result is not None:
            return result
    return utcnow()


def point(lon: Any, lat: Any, warnings: list[str], field: str = "geometry"):
    if lon is None or lat is None or lon == "" or lat == "":
        return None
    try:
        if isinstance(lon, bool) or isinstance(lat, bool):
            raise ValueError("boolean coordinate")
        x, y = float(lon), float(lat)
        if not (math.isfinite(x) and math.isfinite(y) and -180 <= x <= 180 and -90 <= y <= 90):
            raise ValueError("coordinate outside WGS84")
        return {"type": "Point", "coordinates": [x, y]}
    except (TypeError, ValueError, OverflowError):
        warn(warnings, f"{field}: niepoprawne współrzędne; rekord pozostaje bez geometrii")
        return None


def safe_url(value: Any, fallback: str) -> str:
    if isinstance(value, str):
        try:
            parsed = urlsplit(unescape(value.strip()))
            if parsed.scheme in {"https", "http"} and parsed.hostname and not parsed.username and not parsed.password:
                return unescape(value.strip())
        except ValueError:
            pass
    return fallback


def metadata(doc: FetchedDocument, count: int, **extra) -> dict[str, Any]:
    return {
        "feed_url": doc.url, "records_seen": count,
        "not_modified": doc.not_modified, **extra,
    }


# ISO 3166 code facts, not geocoding. Unknown codes/names remain unknown.
_ISO_PAIRS = """
AF AFG AX ALA AL ALB DZ DZA AS ASM AD AND AO AGO AI AIA AQ ATA AG ATG AR ARG AM ARM AW ABW
AU AUS AT AUT AZ AZE BS BHS BH BHR BD BGD BB BRB BY BLR BE BEL BZ BLZ BJ BEN BM BMU
BT BTN BO BOL BQ BES BA BIH BW BWA BV BVT BR BRA IO IOT BN BRN BG BGR BF BFA BI BDI
CV CPV KH KHM CM CMR CA CAN KY CYM CF CAF TD TCD CL CHL CN CHN CX CXR CC CCK
CO COL KM COM CG COG CD COD CK COK CR CRI CI CIV HR HRV CU CUB CW CUW CY CYP CZ CZE
DK DNK DJ DJI DM DMA DO DOM EC ECU EG EGY SV SLV GQ GNQ ER ERI EE EST SZ SWZ ET ETH
FK FLK FO FRO FJ FJI FI FIN FR FRA GF GUF PF PYF TF ATF GA GAB GM GMB GE GEO DE DEU
GH GHA GI GIB GR GRC GL GRL GD GRD GP GLP GU GUM GT GTM GG GGY GN GIN GW GNB GY GUY
HT HTI HM HMD VA VAT HN HND HK HKG HU HUN IS ISL IN IND ID IDN IR IRN IQ IRQ IE IRL
IM IMN IL ISR IT ITA JM JAM JP JPN JE JEY JO JOR KZ KAZ KE KEN KI KIR KP PRK KR KOR
KW KWT KG KGZ LA LAO LV LVA LB LBN LS LSO LR LBR LY LBY LI LIE LT LTU LU LUX MO MAC
MG MDG MW MWI MY MYS MV MDV ML MLI MT MLT MH MHL MQ MTQ MR MRT MU MUS YT MYT MX MEX
FM FSM MD MDA MC MCO MN MNG ME MNE MS MSR MA MAR MZ MOZ MM MMR NA NAM NR NRU NP NPL
NL NLD NC NCL NZ NZL NI NIC NE NER NG NGA NU NIU NF NFK MK MKD MP MNP NO NOR OM OMN
PK PAK PW PLW PS PSE PA PAN PG PNG PY PRY PE PER PH PHL PN PCN PL POL PT PRT PR PRI
QA QAT RE REU RO ROU RU RUS RW RWA BL BLM SH SHN KN KNA LC LCA MF MAF PM SPM
VC VCT WS WSM SM SMR ST STP SA SAU SN SEN RS SRB SC SYC SL SLE SG SGP SX SXM SK SVK
SI SVN SB SLB SO SOM ZA ZAF GS SGS SS SSD ES ESP LK LKA SD SDN SR SUR SJ SJM SE SWE
CH CHE SY SYR TW TWN TJ TJK TZ TZA TH THA TL TLS TG TGO TK TKL TO TON TT TTO TN TUN
TR TUR TM TKM TC TCA TV TUV UG UGA UA UKR AE ARE GB GBR US USA UM UMI UY URY UZ UZB
VU VUT VE VEN VN VNM VG VGB VI VIR WF WLF EH ESH YE YEM ZM ZMB ZW ZWE
""".split()
ISO3_TO_ISO2 = dict(zip(_ISO_PAIRS[1::2], _ISO_PAIRS[::2]))
ISO2 = set(ISO3_TO_ISO2.values())
COUNTRY_NAMES = {
    "afghanistan": "AF", "albania": "AL", "algeria": "DZ", "andorra": "AD",
    "austria": "AT", "australia": "AU", "bahrain": "BH", "belgium": "BE",
    "bosnia and herzegovina": "BA", "brazil": "BR", "bulgaria": "BG",
    "canada": "CA", "china": "CN", "croatia": "HR", "cyprus": "CY",
    "czechia": "CZ", "czech republic": "CZ", "denmark": "DK", "egypt": "EG",
    "estonia": "EE", "ethiopia": "ET", "finland": "FI", "france": "FR",
    "germany": "DE", "greece": "GR", "hungary": "HU", "iceland": "IS",
    "india": "IN", "indonesia": "ID", "iran": "IR", "iraq": "IQ",
    "ireland": "IE", "israel": "IL", "italy": "IT", "japan": "JP",
    "jordan": "JO", "kenya": "KE", "kuwait": "KW", "latvia": "LV",
    "lebanon": "LB", "libya": "LY", "lithuania": "LT", "luxembourg": "LU",
    "mali": "ML", "malta": "MT", "moldova": "MD", "montenegro": "ME",
    "netherlands": "NL", "north macedonia": "MK", "norway": "NO", "oman": "OM",
    "pakistan": "PK", "poland": "PL", "portugal": "PT", "qatar": "QA",
    "romania": "RO", "russia": "RU", "russian federation": "RU",
    "saudi arabia": "SA", "serbia": "RS", "slovakia": "SK", "slovenia": "SI",
    "somalia": "SO", "south sudan": "SS", "spain": "ES", "sudan": "SD",
    "sweden": "SE", "switzerland": "CH", "syria": "SY", "turkey": "TR",
    "türkiye": "TR", "ukraine": "UA", "united arab emirates": "AE",
    "united kingdom": "GB", "united states": "US", "venezuela": "VE",
    "yemen": "YE", "north korea": "KP",
    "democratic people's republic of korea": "KP",
}

COUNTRY_NAMES.update({'åland islands': 'AX', 'aland islands': 'AX', 'american samoa': 'AS', 'angola': 'AO', 'anguilla': 'AI', 'antarctica': 'AQ', 'antigua and barbuda': 'AG', 'argentina': 'AR', 'armenia': 'AM', 'aruba': 'AW', 'azerbaijan': 'AZ', 'bahamas': 'BS', 'bangladesh': 'BD', 'barbados': 'BB', 'belarus': 'BY', 'belize': 'BZ', 'benin': 'BJ', 'bermuda': 'BM', 'bhutan': 'BT', 'bolivia': 'BO', 'bonaire, sint eustatius and saba': 'BQ', 'botswana': 'BW', 'bouvet island': 'BV', 'british indian ocean territory': 'IO', 'brunei': 'BN', 'brunei darussalam': 'BN', 'burkina faso': 'BF', 'burundi': 'BI', 'cabo verde': 'CV', 'cape verde': 'CV', 'cambodia': 'KH', 'cameroon': 'CM', 'cayman islands': 'KY', 'central african republic': 'CF', 'chad': 'TD', 'chile': 'CL', 'christmas island': 'CX', 'cocos islands': 'CC', 'cocos (keeling) islands': 'CC', 'colombia': 'CO', 'comoros': 'KM', 'congo': 'CG', 'republic of the congo': 'CG', 'democratic republic of the congo': 'CD', 'democratic republic of congo': 'CD', 'congo democratic republic': 'CD', 'cook islands': 'CK', 'costa rica': 'CR', "côte d'ivoire": 'CI', "cote d'ivoire": 'CI', 'ivory coast': 'CI', 'cuba': 'CU', 'curaçao': 'CW', 'curacao': 'CW', 'djibouti': 'DJ', 'dominica': 'DM', 'dominican republic': 'DO', 'ecuador': 'EC', 'el salvador': 'SV', 'equatorial guinea': 'GQ', 'eritrea': 'ER', 'eswatini': 'SZ', 'swaziland': 'SZ', 'falkland islands': 'FK', 'faroe islands': 'FO', 'fiji': 'FJ', 'french guiana': 'GF', 'french polynesia': 'PF', 'french southern territories': 'TF', 'gabon': 'GA', 'gambia': 'GM', 'georgia': 'GE', 'ghana': 'GH', 'gibraltar': 'GI', 'greenland': 'GL', 'grenada': 'GD', 'guadeloupe': 'GP', 'guam': 'GU', 'guatemala': 'GT', 'guernsey': 'GG', 'guinea': 'GN', 'guinea-bissau': 'GW', 'guyana': 'GY', 'haiti': 'HT', 'heard island and mcdonald islands': 'HM', 'holy see': 'VA', 'vatican city': 'VA', 'honduras': 'HN', 'hong kong': 'HK', 'isle of man': 'IM', 'jamaica': 'JM', 'jersey': 'JE', 'kazakhstan': 'KZ', 'kiribati': 'KI', "korea, democratic people's republic of": 'KP', 'republic of korea': 'KR', 'south korea': 'KR', 'kyrgyzstan': 'KG', 'laos': 'LA', "lao people's democratic republic": 'LA', 'lesotho': 'LS', 'liberia': 'LR', 'liechtenstein': 'LI', 'macao': 'MO', 'macau': 'MO', 'madagascar': 'MG', 'malawi': 'MW', 'malaysia': 'MY', 'maldives': 'MV', 'marshall islands': 'MH', 'martinique': 'MQ', 'mauritania': 'MR', 'mauritius': 'MU', 'mayotte': 'YT', 'mexico': 'MX', 'micronesia': 'FM', 'monaco': 'MC', 'mongolia': 'MN', 'montserrat': 'MS', 'morocco': 'MA', 'mozambique': 'MZ', 'myanmar': 'MM', 'namibia': 'NA', 'nauru': 'NR', 'nepal': 'NP', 'new caledonia': 'NC', 'new zealand': 'NZ', 'nicaragua': 'NI', 'niger': 'NE', 'nigeria': 'NG', 'niue': 'NU', 'norfolk island': 'NF', 'northern mariana islands': 'MP', 'palau': 'PW', 'palestine': 'PS', 'state of palestine': 'PS', 'panama': 'PA', 'papua new guinea': 'PG', 'paraguay': 'PY', 'peru': 'PE', 'philippines': 'PH', 'pitcairn': 'PN', 'puerto rico': 'PR', 'réunion': 'RE', 'reunion': 'RE', 'rwanda': 'RW', 'saint barthélemy': 'BL', 'saint barthelemy': 'BL', 'saint helena': 'SH', 'saint kitts and nevis': 'KN', 'saint lucia': 'LC', 'saint martin': 'MF', 'saint pierre and miquelon': 'PM', 'saint vincent and the grenadines': 'VC', 'samoa': 'WS', 'san marino': 'SM', 'sao tome and principe': 'ST', 'são tomé and príncipe': 'ST', 'senegal': 'SN', 'seychelles': 'SC', 'sierra leone': 'SL', 'singapore': 'SG', 'sint maarten': 'SX', 'solomon islands': 'SB', 'solomon is.': 'SB', 'south africa': 'ZA', 'south georgia and the south sandwich islands': 'GS', 'sri lanka': 'LK', 'suriname': 'SR', 'svalbard and jan mayen': 'SJ', 'syrian arab republic': 'SY', 'taiwan': 'TW', 'tajikistan': 'TJ', 'tanzania': 'TZ', 'united republic of tanzania': 'TZ', 'thailand': 'TH', 'timor-leste': 'TL', 'east timor': 'TL', 'togo': 'TG', 'tokelau': 'TK', 'tonga': 'TO', 'trinidad and tobago': 'TT', 'tunisia': 'TN', 'turkmenistan': 'TM', 'turks and caicos islands': 'TC', 'tuvalu': 'TV', 'uganda': 'UG', 'united states of america': 'US', 'united states minor outlying islands': 'UM', 'uruguay': 'UY', 'uzbekistan': 'UZ', 'vanuatu': 'VU', 'vietnam': 'VN', 'viet nam': 'VN', 'british virgin islands': 'VG', 'u.s. virgin islands': 'VI', 'wallis and futuna': 'WF', 'western sahara': 'EH', 'zambia': 'ZM', 'zimbabwe': 'ZW'})


def country_code(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    token = plain(value).strip()
    upper = token.upper()
    if upper in ISO2:
        return upper
    return ISO3_TO_ISO2.get(upper) or COUNTRY_NAMES.get(token.casefold())


def country_list(value: Any) -> list[str]:
    if isinstance(value, str) and (single := country_code(value)):
        return [single]
    tokens = value if isinstance(value, list) else str(value or "").split(",")
    return sorted({code for token in tokens if (code := country_code(token))})


def element_text(node, path: str) -> str:
    value = node.findtext(path)
    return value.strip() if value else ""


def xml_raw(node) -> dict[str, Any]:
    """Lossless-enough structured XML evidence, retaining namespace and ordering."""
    return {
        "tag": node.tag, "attributes": dict(node.attrib),
        "text": node.text.strip() if node.text and node.text.strip() else None,
        "children": [xml_raw(child) for child in node],
    }
