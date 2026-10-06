/* Carga la página de operativa (su <script>) con un operativa.json dado, sobre
   un DOM mínimo, y cuenta lo que se pintó. Lo usa tests/test_pagina_versiones.py.

   Uso: node cargar_operativa.js <plantilla.html> <datos.json> [latido.json]
   Imprime un JSON con: errores de consola, avisos, texto del semáforo, filas
   pintadas por sección y la etiqueta de la cuenta. */
const fs = require("fs");
const [plantilla, datos, latido] = process.argv.slice(2);

function nodo(tag, id){
  const n = {
    tag, id, children: [], className: "", dataset: {}, style: {}, hidden: false,
    attrs: {}, _texto: "", value: "", checked: false, href: "", type: "",
    get textContent(){ return this._texto + this.children.map(c => c.textContent || "").join(""); },
    set textContent(v){ this._texto = String(v); this.children = []; },
    get innerHTML(){ return this.textContent; }, set innerHTML(v){ this.textContent = v; },
    appendChild(c){ this.children.push(c); return c; },
    insertBefore(c){ this.children.unshift(c); return c; },
    removeChild(c){ this.children = this.children.filter(x => x !== c); },
    remove(){}, replaceChildren(){ this.children = []; },
    setAttribute(k, v){ this.attrs[k] = v; }, getAttribute(k){ return this.attrs[k]; },
    addEventListener(){}, focus(){}, click(){},
    querySelector(sel){ return buscar(this, sel); },
    querySelectorAll(){ return []; }, closest(){ return null; },
    classList: { add(){}, remove(){}, toggle(){}, contains(){ return false; } },
    get firstChild(){ return this.children[0] || null; },
  };
  return n;
}
function buscar(raiz, sel){
  const clase = sel.startsWith(".") ? sel.slice(1) : null;
  const etiqueta = clase ? null : sel.split(".")[0];
  for (const c of raiz.children || []){
    if (!c || typeof c !== "object") continue;
    if ((clase && (c.className || "").split(" ").includes(clase)) ||
        (etiqueta && c.tag === etiqueta)) return c;
    const r = buscar(c, sel); if (r) return r;
  }
  return null;
}
const porId = {};
global.document = {
  getElementById: id => porId[id] || (porId[id] = nodo("div", id)),
  createElement: t => nodo(t), createTextNode: t => ({textContent: String(t)}),
  createDocumentFragment: () => nodo("frag"),
  documentElement: nodo("html"), body: nodo("body"),
  querySelector: () => null, querySelectorAll: () => [], addEventListener(){},
};
global.window = global;
global.matchMedia = () => ({matches: false, addEventListener(){}, addListener(){}});
global.localStorage = {getItem(){ return null; }, setItem(){}};
global.location = {search: "", hash: "", reload(){}};
global.history = {replaceState(){}};
global.setInterval = () => 0;
const errores = [], avisos = [];
global.console = Object.assign({}, console, {
  error: (...a) => errores.push(a.map(String).join(" ")),
  warn: (...a) => avisos.push(a.map(String).join(" ")),
});
const urls = [];
global.fetch = (url) => {
  urls.push(url);
  const cuerpo = String(url).startsWith("operativa.json")
    ? fs.readFileSync(datos, "utf8")
    : (latido ? fs.readFileSync(latido, "utf8") : null);
  if (cuerpo === null) return Promise.reject(new Error("sin latido"));
  return Promise.resolve({ok: true, status: 200, json: () => Promise.resolve(JSON.parse(cuerpo))});
};

const html = fs.readFileSync(plantilla, "utf8");
const codigo = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]).join("\n");
new Function(codigo)();

setTimeout(() => {
  const filas = id => (porId[id] ? porId[id].children.length : 0);
  const h3 = buscar(porId["broker"] || nodo("x"), "h3");
  process.stdout.write(JSON.stringify({
    errores, avisos, urls,
    semaforo: porId["semaforo"] ? porId["semaforo"].textContent : "",
    filas: {ordenes: filas("cuerpo-ord"), posiciones: filas("cuerpo-pos"),
            componentes: filas("componentes"), cuenta: filas("broker")},
    cuenta: h3 ? h3.textContent : null,
    posiciones: porId["cuerpo-pos"] ? porId["cuerpo-pos"].textContent : "",
  }));
}, 50);
