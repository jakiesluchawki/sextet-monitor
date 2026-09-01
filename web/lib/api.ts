export class ApiError extends Error {
  constructor(message: string, public status: number) { super(message); this.name = "ApiError"; }
}
export async function apiFetch<T>(path: string, options: {signal?: AbortSignal; body?: unknown} = {}): Promise<T> {
  if (!path.startsWith("/api/") || path.startsWith("//")) throw new ApiError("Nieprawidłowa ścieżka API.", 400);
  const controller = new AbortController();
  const abort = () => controller.abort(options.signal?.reason);
  options.signal?.addEventListener("abort", abort, {once: true});
  if (options.signal?.aborted) abort();
  const timer = setTimeout(() => controller.abort(new DOMException("Przekroczono czas odpowiedzi.", "TimeoutError")), 30000);
  try {
    const response = await fetch(path, {
      method: options.body === undefined ? "GET" : "POST",
      headers: options.body === undefined ? {Accept: "application/json"} : {"Content-Type": "application/json", Accept: "application/json", "X-Monitor-Request": "1"},
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: controller.signal, cache: "no-store", credentials: "same-origin",
    });
    const reader = response.body?.getReader();
    const chunks: Uint8Array[] = [];
    let bytes = 0;
    if (reader) {
      while (true) {
        const part = await reader.read();
        if (part.done) break;
        bytes += part.value.byteLength;
        if (bytes > 5 * 1024 * 1024) { await reader.cancel(); throw new ApiError("Odpowiedź jest zbyt duża. Zawęź filtry.", 413); }
        chunks.push(part.value);
      }
    }
    const buffer = new Uint8Array(bytes);
    let offset = 0;
    for (const chunk of chunks) { buffer.set(chunk, offset); offset += chunk.byteLength; }
    let payload: unknown;
    try { payload = JSON.parse(new TextDecoder().decode(buffer)); }
    catch { throw new ApiError("Serwer nie zwrócił poprawnej odpowiedzi JSON.", response.status); }
    if (!response.ok) {
      const detail = payload && typeof payload === "object" && "detail" in payload ? (payload as {detail: unknown}).detail : null;
      throw new ApiError(typeof detail === "string" ? detail : response.status === 422 ? "Serwer odrzucił parametry. Sprawdź zakres i filtry." : "Nie można pobrać danych z lokalnego API.", response.status);
    }
    return payload as T;
  } catch (error) {
    if (options.signal?.aborted) throw error;
    if (error instanceof ApiError) throw error;
    if (controller.signal.aborted) throw new ApiError("Lokalne API nie odpowiedziało na czas. Spróbuj ponownie.", 504);
    throw new ApiError("Brak połączenia z lokalnym API. Sprawdź, czy usługi są uruchomione.", 502);
  } finally { clearTimeout(timer); options.signal?.removeEventListener("abort", abort); }
}
