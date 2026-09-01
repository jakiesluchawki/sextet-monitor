import { NextRequest, NextResponse } from "next/server";
import { buildBackendUrl, isAllowedRoute, requestPolicyError } from "@/lib/proxy-policy";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
const MAX_REQUEST = 32 * 1024;
const MAX_RESPONSE = 5 * 1024 * 1024;

async function readBounded(stream: ReadableStream<Uint8Array> | null, maximum: number, signal: AbortSignal): Promise<Uint8Array> {
  if (!stream) return new Uint8Array();
  const reader = stream.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  const onAbort = () => { void reader.cancel().catch(() => undefined); };
  signal.addEventListener("abort", onAbort, {once: true});
  try {
    while (true) {
      if (signal.aborted) throw new Error("timeout");
      const {done, value} = await reader.read();
      if (signal.aborted) throw new Error("timeout");
      if (done) break;
      size += value.byteLength;
      if (size > maximum) { await reader.cancel(); throw new RangeError("size"); }
      chunks.push(value);
    }
    const result = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) { result.set(chunk, offset); offset += chunk.byteLength; }
    return result;
  } finally { signal.removeEventListener("abort", onAbort); reader.releaseLock(); }
}
async function proxy(request: NextRequest, context: {params: Promise<{path: string[]}>}) {
  const {path} = await context.params;
  const error = requestPolicyError(request.method, new URL(request.url), request.headers);
  if (error) return NextResponse.json({detail:error}, {status:403});
  if (!isAllowedRoute(request.method, path)) return NextResponse.json({detail:"Nieobsługiwana ścieżka API."}, {status:404});
  let target: URL;
  try { target = buildBackendUrl(process.env.BACKEND_URL || "http://api:8000", path, request.nextUrl.searchParams); }
  catch { return NextResponse.json({detail:"Nieprawidłowa ścieżka lub konfiguracja lokalnego API."}, {status:400}); }
  const controller = new AbortController();
  const abort = () => controller.abort();
  request.signal.addEventListener("abort", abort, {once:true});
  if (request.signal.aborted) abort();
  const timer = setTimeout(abort, request.method === "POST" ? 25000 : 15000);
  try {
    const body = request.method === "POST" ? await readBounded(request.body, MAX_REQUEST, controller.signal) : undefined;
    if (body) {
      try { JSON.parse(new TextDecoder().decode(body)); }
      catch { return NextResponse.json({detail:"Nieprawidłowy JSON."}, {status:400}); }
    }
    const upstream = await fetch(target, {
      method:request.method,
      headers:{"Accept":"application/json", ...(body ? {"Content-Type":"application/json", "X-Monitor-Request":"1"} : {})},
      body:body ? new TextDecoder().decode(body) : undefined,
      signal:controller.signal, cache:"no-store", redirect:"error",
    });
    if (!upstream.headers.get("content-type")?.toLowerCase().includes("application/json")) {
      return NextResponse.json({detail:"Lokalne API zwróciło nieprawidłowy format odpowiedzi."}, {status:502});
    }
    const data = await readBounded(upstream.body, MAX_RESPONSE, controller.signal);
    // Do not forward provider URLs, cookies, server headers or upstream redirects.
    return new NextResponse(new TextDecoder().decode(data), {status:upstream.status, headers:{"Content-Type":"application/json; charset=utf-8", "Cache-Control":"no-store", "X-Content-Type-Options":"nosniff"}});
  } catch (cause) {
    if (cause instanceof RangeError) return NextResponse.json({detail:"Przekroczono bezpieczny rozmiar odpowiedzi lub zapytania."}, {status:413});
    return NextResponse.json({detail:controller.signal.aborted ? "Lokalne API nie odpowiedziało na czas." : "Lokalne API jest niedostępne. Sprawdź uruchomione usługi."}, {status:controller.signal.aborted ? 504 : 502});
  } finally { clearTimeout(timer); request.signal.removeEventListener("abort", abort); }
}
export const GET = proxy;
export const POST = proxy;
