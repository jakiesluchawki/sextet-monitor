import { copyFile, mkdir, readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const webRoot = fileURLToPath(new URL("../", import.meta.url));
const packageRoot = dirname(createRequire(import.meta.url).resolve("maplibre-gl/package.json"));
const installed = JSON.parse(await readFile(join(packageRoot,"package.json"),"utf8"));
const project = JSON.parse(await readFile(join(webRoot,"package.json"),"utf8"));
if (installed.version !== project.dependencies["maplibre-gl"]) {
  throw new Error("MapLibre assets must match the exact version pinned in package.json.");
}
const destination = join(webRoot,"public","maplibre");
await mkdir(destination,{recursive:true});
// MapLibre 6's module worker imports the shared module relative to its own URL.
// Preserve both files and the upstream BSD/dependency notices, without rewriting.
for (const [source,target] of [
  ["dist/maplibre-gl.mjs","maplibre-gl.mjs"],
  ["dist/maplibre-gl-worker.mjs","maplibre-gl-worker.mjs"],
  ["dist/maplibre-gl-shared.mjs","maplibre-gl-shared.mjs"],
  ["LICENSE.txt","LICENSE.txt"],
]) {
  await copyFile(join(packageRoot,source),join(destination,target));
}
console.log(`Local MapLibre ${installed.version} main, worker, shared module and license prepared.`);
