import {mkdir, readFile, readdir, writeFile} from "node:fs/promises";
import {join} from "node:path";
import {fileURLToPath} from "node:url";

const root=fileURLToPath(new URL("../",import.meta.url));
const lock=JSON.parse(await readFile(join(root,"package-lock.json"),"utf8"));
const parts=[
  "Sextet Monitor — notices for distributed third-party components",
  "This file preserves upstream notices. It does not license the application's original code.",
  "Map data: Natural Earth, public domain. https://www.naturalearthdata.com/about/terms-of-use/",
  "Public data: USGS with originating networks; MeteoAlarm/EUMETNET and IMGW-PIB (CC BY 4.0, transformed); CISA KEV (CC0). Source-specific links appear beside the evidence.",
];
let count=0;
async function appendNotices(directory,label) {
  let names;
  try { names=await readdir(directory); } catch(error) {if(error.code==="ENOENT")return;throw error;}
  for(const name of names.sort()) {
    if(!/^(?:licen[cs]e|notice|copying)(?:[._-].*)?$/i.test(name))continue;
    const path=join(directory,name);
    let content;
    try{content=await readFile(path,"utf8");}catch(error){if(error.code==="EISDIR")continue;throw error;}
    parts.push("\n===== "+label+" — "+name+" =====\n"+content);
    count++;
  }
}
for(const [key,pkg] of Object.entries(lock.packages ?? {}).sort(([a],[b])=>a.localeCompare(b))) {
  if(!key.startsWith("node_modules/") || pkg.dev)continue;
  if(key.split("/").some(part=>part===".." || part==="."))throw new Error("Unexpected dependency path.");
  await appendNotices(join(root,key),key.replace(/^node_modules\//,"")+"@"+pkg.version);
}
// Next also includes vendored build/runtime dependencies outside package-lock's tree.
const compiled=join(root,"node_modules/next/dist/compiled");
for(const entry of await readdir(compiled,{withFileTypes:true})) {
  if(!entry.isDirectory())continue;
  await appendNotices(join(compiled,entry.name),"Next.js bundled "+entry.name);
}
if(count<4)throw new Error("Required third-party notices are missing; install locked dependencies first.");
await mkdir(join(root,"public"),{recursive:true});
await writeFile(join(root,"public/THIRD_PARTY_NOTICES.txt"),parts.join("\n\n")+"\n","utf8");
console.log("Prepared "+count+" upstream notice files for public distribution.");
