import { copyFile, lstat, mkdir, readFile, readdir, realpath, rm, symlink, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath, pathToFileURL } from "node:url";

const webRoot = await realpath(fileURLToPath(new URL("../", import.meta.url)));
const repoRoot = dirname(webRoot);
const buildRoot = join(webRoot, ".pages-build");
const require = createRequire(import.meta.url);
const markerName = "OWNED";
const marker = JSON.stringify({ format: 1, owner: "mieszko-monitor-pages-build", repository: repoRoot }) + "\n";
const basePath = process.env.MONITOR_PAGES_BASE_PATH ?? "/mieszko-monitor";
const sourceFiles = [
  "app/globals.css",
  ...["PublicMonitor", "EventMap", "EventList", "EventEvidence", "FilterPanel", "SourcePanel", "Icon"]
    .map((name) => `components/${name}.tsx`),
  ...["public-snapshot", "assets", "contracts", "filters", "format", "map-data", "countries"]
    .map((name) => `lib/${name}.ts`),
];
const publicFiles = [
  "snapshot.json",
  "maps/countries.geojson",
  "maplibre/maplibre-gl.mjs",
  "maplibre/maplibre-gl-worker.mjs",
  "maplibre/maplibre-gl-shared.mjs",
  "maplibre/LICENSE.txt",
  "THIRD_PARTY_NOTICES.txt",
];

async function optionalStat(path) {
  try {
    return await lstat(path);
  } catch (error) {
    if (error.code === "ENOENT") return null;
    throw error;
  }
}

async function regularSource(relativePath) {
  const path = join(webRoot, relativePath);
  const stat = await optionalStat(path);
  if (!stat?.isFile() || stat.isSymbolicLink() || await realpath(path) !== path) {
    throw new Error(`Brak zwykłego pliku wejściowego lub niedozwolony symlink: ${relativePath}`);
  }
  return { path, stat };
}

// Only this script's previous directory may be removed. rm never follows the
// intentional node_modules symlink; an unmarked or symlinked build root is refused.
async function ownedBuildExists() {
  const stat = await optionalStat(buildRoot);
  if (!stat) return false;
  if (!stat.isDirectory() || stat.isSymbolicLink() || await realpath(buildRoot) !== buildRoot) {
    throw new Error("Odmowa czyszczenia: .pages-build nie jest zwykłym katalogiem tego projektu.");
  }
  const markerPath = join(buildRoot, markerName);
  const markerStat = await optionalStat(markerPath);
  if (!markerStat?.isFile() || markerStat.isSymbolicLink() || await readFile(markerPath, "utf8") !== marker) {
    throw new Error("Odmowa czyszczenia: .pages-build nie ma zgodnego znacznika OWNED. Sprawdź katalog ręcznie.");
  }
  return true;
}

function buildEnvironment() {
  const env = { ...process.env };
  // Do not inherit unrelated browser-visible configuration from the private app.
  for (const key of Object.keys(env)) {
    if (key.startsWith("NEXT_PUBLIC_")) delete env[key];
  }
  return {
    ...env,
    NODE_ENV: "production",
    NEXT_TELEMETRY_DISABLED: "1",
    NEXT_PUBLIC_MONITOR_BASE_PATH: basePath,
  };
}

function runNode(args, cwd, label, env) {
  const result = spawnSync(process.execPath, args, { cwd, env, stdio: "inherit" });
  if (result.error) throw new Error(`${label}: ${result.error.message}`);
  if (result.status !== 0) {
    throw new Error(`${label}: proces zakończył się ${result.signal ? `sygnałem ${result.signal}` : `kodem ${result.status}`}.`);
  }
}

async function copyAllowed(relativePath) {
  const { path } = await regularSource(relativePath);
  const target = join(buildRoot, relativePath);
  await mkdir(dirname(target), { recursive: true });
  await copyFile(path, target);
}

async function assertPortableExport(directory) {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isSymbolicLink()) throw new Error("Publiczny eksport nie może zawierać symlinków.");
    if (entry.isDirectory()) await assertPortableExport(path);
    else if (entry.isFile()) {
      const bytes = await readFile(path);
      if (bytes.includes(repoRoot) || bytes.includes(pathToFileURL(repoRoot).href)) {
        throw new Error("Eksport zawiera ścieżkę maszyny budującej; publikacja została zatrzymana.");
      }
    }
  }
}

async function main() {
  if (!/^(?:\/[A-Za-z0-9_-]+(?:\/[A-Za-z0-9_-]+)*)?$/.test(basePath)) {
    throw new Error("MONITOR_PAGES_BASE_PATH musi być pusty albo mieć postać /nazwa-repo (bez końcowego /, kropki i znaków URL).");
  }
  await ownedBuildExists();
  const snapshot = await optionalStat(join(webRoot, "public/snapshot.json"));
  if (!snapshot) {
    throw new Error("Brak web/public/snapshot.json. Najpierw przygotuj i zatwierdź publiczny zestaw danych. Builder nie pobiera danych ani nie zastępuje ich fixture.");
  }
  const { path: snapshotPath, stat: snapshotStat } = await regularSource("public/snapshot.json");
  if (snapshotStat.size === 0 || snapshotStat.size > 16 * 1024 * 1024) {
    throw new Error("Publiczny snapshot.json musi mieć rozmiar od 1 bajta do 16 MiB.");
  }
  for (const path of [...sourceFiles, "package.json", "tsconfig.json", "scripts/prepare-map-assets.mjs", "scripts/prepare-notices.mjs"]) {
    await regularSource(path);
  }
  const modulesPath = await realpath(join(webRoot, "node_modules"));
  const nextCli = require.resolve("next/dist/bin/next");
  const tsxLoader = require.resolve("tsx");
  const env = buildEnvironment();
  const projectPackage = JSON.parse(await readFile(join(webRoot, "package.json"), "utf8"));
  const tsconfig = JSON.parse(await readFile(join(webRoot, "tsconfig.json"), "utf8"));

  // Reuse the same schema gate as the public UI before touching the previous
  // export. This reads one local file; it never fetches or queries the monitor.
  runNode(["--import", tsxLoader, "--input-type=module", "--eval", `
    import { readFile } from "node:fs/promises";
    const module = await import(${JSON.stringify(pathToFileURL(join(webRoot, "lib/public-snapshot.ts")).href)});
    const validate = module.validatePublicSnapshot ?? module.default?.validatePublicSnapshot;
    if (typeof validate !== "function") throw new Error("Brak walidatora publicznego zestawu.");
    validate(JSON.parse(await readFile(${JSON.stringify(snapshotPath)}, "utf8")));
  `], webRoot, "Walidacja publicznego zestawu", env);
  runNode([join(webRoot, "scripts/prepare-map-assets.mjs")], webRoot, "Przygotowanie lokalnych zasobów mapy", env);
  runNode([join(webRoot, "scripts/prepare-notices.mjs")], webRoot, "Przygotowanie informacji licencyjnych", env);
  for (const path of publicFiles) await regularSource(`public/${path}`);

  // Recheck ownership immediately before deletion, including on a repeated run.
  if (await ownedBuildExists()) await rm(buildRoot, { recursive: true });
  await mkdir(buildRoot);
  await writeFile(join(buildRoot, markerName), marker, { flag: "wx" });
  for (const path of [...sourceFiles, ...publicFiles.map((path) => `public/${path}`)]) {
    await copyAllowed(path);
  }
  await symlink(modulesPath, join(buildRoot, "node_modules"), "dir");
  await writeFile(join(buildRoot, "package.json"), JSON.stringify({
    name: "mieszko-monitor-public-pages",
    version: projectPackage.version,
    private: true,
    type: "module",
    dependencies: projectPackage.dependencies,
    devDependencies: projectPackage.devDependencies,
  }, null, 2) + "\n");
  await writeFile(join(buildRoot, "tsconfig.json"), JSON.stringify({
    ...tsconfig,
    include: ["next-env.d.ts", "app/**/*.ts", "app/**/*.tsx", "components/**/*.ts", "components/**/*.tsx", "lib/**/*.ts", "lib/**/*.tsx", ".next/types/**/*.ts"],
    exclude: ["node_modules"],
  }, null, 2) + "\n");
  await writeFile(join(buildRoot, "next-env.d.ts"), '/// <reference types="next" />\n/// <reference types="next/image-types/global" />\n');
  await writeFile(join(buildRoot, "next.config.mjs"), `export default {
    output: "export",
    outputFileTracingRoot: process.cwd(),
    basePath: ${JSON.stringify(basePath)},
    trailingSlash: true,
    poweredByHeader: false,
    reactStrictMode: true,
    images: { unoptimized: true },
  };\n`);
  await writeFile(join(buildRoot, "app/page.tsx"), 'import PublicMonitor from "@/components/PublicMonitor";\nexport default function Page() { return <PublicMonitor />; }\n');
  await writeFile(join(buildRoot, "app/layout.tsx"), `import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import "./globals.css";
export const metadata: Metadata = {
  title: "Mieszko Monitor — publiczny podgląd",
  description: "Publiczny, datowany podgląd źródeł. Dane ze źródeł publicznych wraz z opisem ograniczeń.",
  robots: { index: false, follow: false },
};
export const viewport: Viewport = { width: "device-width", initialScale: 1, colorScheme: "dark", themeColor: "#171b1a" };
export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return <html lang="pl"><body>{children}</body></html>;
}\n`);

  console.log(`Buduję publiczny podgląd dla ${basePath || "/"}; bez publikacji i połączenia z bazą.`);
  runNode([nextCli, "build", "--webpack"], buildRoot, "Next.js static export", env);
  const out = join(buildRoot, "out");
  const outStat = await optionalStat(out);
  if (!outStat?.isDirectory() || outStat.isSymbolicLink() || await realpath(out) !== out) {
    throw new Error("Next.js nie utworzył zwykłego katalogu .pages-build/out.");
  }
  await assertPortableExport(out);
  await writeFile(join(out, ".nojekyll"), "", { flag: "wx" });
  console.log(`Eksport gotowy do osobnej weryfikacji: ${out}`);
}

try {
  await main();
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error));
  process.exitCode = 1;
}
