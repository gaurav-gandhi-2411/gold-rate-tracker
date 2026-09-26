// tests/helpers/load_how_we_know.js — load the REAL i18n.js + how-we-know-strings.js +
// how-we-know.js into a Node vm context, same pattern as load_app.js (see that file's
// own header comment for the full "why" -- tests call the functions defined in the
// real files instead of pasting copies of them).
//
// how-we-know.js is a plain browser script sharing i18n.js's globals (currentLang,
// t(), setLang()) the same way app.js does, plus its own STRINGS_HWK/tHwk() catalogue
// from how-we-know-strings.js -- loaded in that order, matching how-we-know.html's own
// <script> tags. Its only load-time execution is a self-invoking async init(), kept
// inert the same way app.js's is: a fetch that never resolves.

import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");

// Same tolerant DOM-element stub as load_app.js.
function makeElement(name = "el") {
  const store = { hidden: false, textContent: "", innerHTML: "", children: [] };
  const fn = function () {};
  return new Proxy(fn, {
    get(_t, prop) {
      if (prop in store) return store[prop];
      if (prop === "classList") return { add() {}, remove() {}, toggle() {}, contains: () => false };
      if (prop === "style" || prop === "dataset") return new Proxy({}, { get: () => "", set: () => true });
      if (prop === "getBoundingClientRect") return () => ({ width: 0, height: 0, top: 0, left: 0 });
      if (prop === Symbol.toPrimitive) return () => "";
      if (prop === "length") return 0;
      if (prop === "then") return undefined;
      return makeElement(`${name}.${String(prop)}`);
    },
    set(_t, prop, value) { store[prop] = value; return true; },
    apply() { return makeElement(`${name}()`); },
  });
}

const HIDDEN_IDS = new Set(
  [...fs.readFileSync(path.join(ROOT, "how-we-know.html"), "utf8").matchAll(/<[^>]*\bid="([^"]+)"[^>]*>/g)]
    .filter((m) => /\shidden(\s|>|=|\/)/.test(m[0]))
    .map((m) => m[1]),
);

export function loadHowWeKnow({ nowMs, lang = "en", globals = {} } = {}) {
  const elements = new Map();
  const document = {
    getElementById(id) {
      if (!elements.has(id)) {
        const el = makeElement(id);
        if (HIDDEN_IDS.has(id)) el.hidden = true;
        elements.set(id, el);
      }
      return elements.get(id);
    },
    querySelector: () => makeElement("q"),
    querySelectorAll: () => [],
    createElement: () => makeElement("created"),
    addEventListener() {},
    documentElement: makeElement("html"),
    body: makeElement("body"),
    readyState: "complete",
  };
  const storage = new Map([["lang", lang]]);
  const localStorage = {
    getItem: (k) => (storage.has(k) ? storage.get(k) : null),
    setItem: (k, v) => storage.set(k, String(v)),
    removeItem: (k) => storage.delete(k),
  };
  const RealDate = Date;
  const FakeDate = nowMs == null ? RealDate : class extends RealDate {
    constructor(...a) { if (a.length === 0) super(nowMs); else super(...a); }
    static now() { return nowMs; }
  };
  const sandbox = {
    document, localStorage, Date: FakeDate,
    navigator: { onLine: true, language: "en-IN", serviceWorker: undefined },
    location: { href: "http://localhost/", search: "", hash: "" },
    matchMedia: () => ({ matches: false, addEventListener() {}, addListener() {} }),
    fetch: () => new Promise(() => {}), // never resolves: init() parks at its first await
    setTimeout, clearTimeout, setInterval: () => 0, clearInterval() {},
    requestAnimationFrame: () => 0, cancelAnimationFrame() {},
    console, Intl, URL, URLSearchParams, AbortController, Response,
    addEventListener() {}, removeEventListener() {},
  };
  Object.assign(sandbox, globals);
  sandbox.window = sandbox;
  const ctx = vm.createContext(sandbox);

  const initErrors = [];
  const onRejection = (e) => initErrors.push(e);
  process.on("unhandledRejection", onRejection);

  for (const file of ["i18n.js", "how-we-know-strings.js", "how-we-know.js"]) {
    vm.runInContext(fs.readFileSync(path.join(ROOT, file), "utf8"), ctx, { filename: file });
  }
  ctx.run = (expr) => vm.runInContext(expr, ctx);
  ctx.pure = (name) => {
    const fn = ctx[name] ?? ctx.run(`typeof ${name} === "function" ? ${name} : undefined`);
    if (typeof fn !== "function") throw new Error(`how-we-know.js has no function named ${name}`);
    return (...args) => {
      const out = fn(...args);
      return out === undefined ? out : structuredClone(out);
    };
  };
  ctx.element = (id) => document.getElementById(id);
  ctx.initErrors = initErrors;
  ctx.dispose = () => process.off("unhandledRejection", onRejection);
  return ctx;
}
