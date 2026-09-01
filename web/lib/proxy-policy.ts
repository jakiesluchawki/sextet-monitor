/**
 * Local browser boundary, not account authentication. Only allowlisted routes and
 * query names can reach the fixed, server-configured BACKEND_URL; the browser
 * cannot supply an upstream URL. FastAPI validates query value types and ranges.
 *
 * Host/Origin, Fetch Metadata and the explicit JSON POST header reject cross-site
 * browser use. Public origins are fixed to localhost:3180 and 127.0.0.1:3180,
 * with an exactly matching Host header. Next may see an internal :3000 URL;
 * neither that URL nor caller-controlled forwarded headers establish origin.
 * Origin-less GET permits local container health checks. A trusted local CLI can
 * deliberately send a public Host and the POST header. These checks complement,
 * rather than replace, the deployment's loopback-only port.
 *
 * The route handler separately enforces 32 KiB request / 5 MiB response limits,
 * 15 s GET / 25 s POST deadlines, redirect rejection and no forwarding of browser
 * cookies or arbitrary caller headers.
 */
const PUBLIC_ORIGINS = new Set(["http://localhost:3180", "http://127.0.0.1:3180"]);
const PUBLIC_HOSTS = new Set(["localhost:3180", "127.0.0.1:3180"]);
const EVENT_QUERY_KEYS = new Set(["window_hours", "time_basis", "since", "until", "country", "region", "category", "severity_min", "min_sources", "lat", "lon", "radius_km", "include_inactive", "limit"]);
export function isAllowedRoute(method: string, parts: string[]): boolean {
  if (parts.some((part) => !/^[a-zA-Z0-9_:-]{1,160}$/.test(part))) return false;
  const path = parts.join("/");
  if (method === "GET") return ["events", "sources", "health", "briefings/latest"].includes(path) || (parts.length === 2 && parts[0] === "events");
  return method === "POST" && ["query", "briefings"].includes(path);
}
export function requestPolicyError(method: string, _internalUrl: URL, headers: Headers): string | null {
  const host = headers.get("host")?.toLowerCase();
  if (!host || !/^(?:localhost|127\.0\.0\.1|\[::1\])(?::\d{1,5})?$/.test(host)) return "Ten interfejs działa wyłącznie lokalnie.";
  try { new URL("http://" + host); } catch { return "Nieprawidłowy Host."; }
  const origin = headers.get("origin");
  if (origin && (!PUBLIC_ORIGINS.has(origin) || new URL(origin).host !== host)) return "Żądanie z obcej strony zostało odrzucone.";
  if (method === "POST" && !PUBLIC_HOSTS.has(host)) return "Nieprawidłowy publiczny Host.";
  if (headers.get("sec-fetch-site") === "cross-site") return "Żądanie z obcej strony zostało odrzucone.";
  if (method === "POST" && headers.get("x-monitor-request") !== "1") return "Brak wymaganego nagłówka żądania.";
  if (method === "POST" && headers.get("content-type")?.split(";")[0]?.trim().toLowerCase() !== "application/json") return "Wymagany format JSON.";
  return null;
}
export function buildBackendUrl(base: string, parts: string[], search: URLSearchParams): URL {
  const configured = new URL(base);
  if (!["http:", "https:"].includes(configured.protocol) || configured.username || configured.password || configured.search || configured.hash || configured.pathname !== "/") {
    throw new Error("Niepoprawna konfiguracja backendu.");
  }
  const target = new URL("/api/" + parts.map(encodeURIComponent).join("/"), configured);
  if (parts.length === 1 && parts[0] === "events") {
    for (const [key, value] of search) {
      if (!EVENT_QUERY_KEYS.has(key) || target.searchParams.has(key) || value.length > 160) throw new Error("Nieprawidłowe parametry zapytania.");
      target.searchParams.set(key, value);
    }
  } else if (search.size > 0) throw new Error("Ta ścieżka nie obsługuje parametrów.");
  return target;
}
