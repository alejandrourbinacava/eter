// Renderiza saturno.html a fotogramas con el Chrome que ya está instalado.
//
// Es exactamente lo que hace Remotion por debajo —Chrome sin ventana capturando
// fotograma a fotograma— pero sin traerse todo su andamiaje para un prototipo.
// Si la calidad convence, se envuelve en Remotion y se gana el resto: React
// para las ilustraciones, composiciones, y el renderizado en paralelo.

import puppeteer from "puppeteer-core";
import { writeFileSync, mkdirSync, rmSync } from "node:fs";
import { pathToFileURL } from "node:url";
import { resolve } from "node:path";

const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const SALIDA = resolve("frames");

rmSync(SALIDA, { recursive: true, force: true });
mkdirSync(SALIDA, { recursive: true });

const navegador = await puppeteer.launch({
  executablePath: CHROME,
  headless: "new",
  args: [
    "--no-sandbox",
    // Sin esto las texturas salen en blanco: Chrome trata cada fichero local
    // como un origen distinto y le niega la lectura a la página.
    "--allow-file-access-from-files",
    // Sin esto Chrome sin ventana usa SwiftShader por software y tarda una
    // eternidad; con ellos tira de la GPU de verdad.
    "--use-gl=angle",
    "--use-angle=default",
    "--enable-gpu-rasterization",
    "--ignore-gpu-blocklist",
  ],
});

const pagina = await navegador.newPage();
await pagina.setViewport({ width: 1920, height: 1080, deviceScaleFactor: 1 });
pagina.on("pageerror", (e) => console.error("  error en la página:", e.message));

const t0 = Date.now();
const ESCENA = process.argv[2] || "saturno.html";
const url = ESCENA.includes("?")
  ? pathToFileURL(resolve(ESCENA.split("?")[0])).href + "?" + ESCENA.split("?")[1]
  : pathToFileURL(resolve(ESCENA)).href;
await pagina.goto(url, {
  waitUntil: "domcontentloaded",
});
await pagina.waitForFunction("window.__listo === true", { timeout: 300000 });

const cuantos = await pagina.evaluate("window.__marcos.length");
console.log(`  ${cuantos} fotogramas calculados en ${((Date.now() - t0) / 1000).toFixed(1)} s`);

for (let i = 0; i < cuantos; i++) {
  const datos = await pagina.evaluate((n) => window.__marcos[n], i);
  writeFileSync(
    `${SALIDA}/f${String(i).padStart(4, "0")}.png`,
    Buffer.from(datos.split(",")[1], "base64"),
  );
}
console.log(`  volcados a ${SALIDA}`);

await navegador.close();
