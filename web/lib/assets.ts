/** Same-origin files for the local app and a repository-scoped Pages export. */
export function assetPath(path:string, basePath=process.env.NEXT_PUBLIC_MONITOR_BASE_PATH || ""):string {
  if(!/^(?:\/[A-Za-z0-9_-]+(?:\/[A-Za-z0-9_-]+)*)?$/.test(basePath))throw new Error("Nieprawidłowa ścieżka bazowa aplikacji.");
  if(!/^\/[A-Za-z0-9_./-]+$/.test(path) || path.startsWith("//") || path.split("/").includes(".."))throw new Error("Nieprawidłowa ścieżka zasobu.");
  return basePath+path;
}
