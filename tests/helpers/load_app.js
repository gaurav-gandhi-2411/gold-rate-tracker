// tests/helpers/load_app.js — load the REAL i18n.js + app.js into a Node vm context.
//
// Why: the unit tests used to paste copies of app.js functions into each test file ("must match
// app.js"). Inverting isToday in the real app.js left all 115 of them passing, and 6 of the 15
// copies that map to a real function had already diverged (2026-09-21 audit, instance #21).
// Tests now call the functions defined in app.js itself.
//
// app.js is a plain browser script (no module system). Its only load-time execution is a Sentry
// guard and a self-invoking async init(); init() is kept inert by a fetch that never resolves,
// and the DOM is a tolerant stub. Top-level `function` declarations become properties of the
// returned context; top-level const/let are read with ctx.run("NAME").

import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");

// A stub that accepts any property read / call / assignment and never throws, so init()'s
// synchronous DOM setup does not crash when there is no DOM. Text/hidden state set on an element
// is remembered so tests can read what a render function wrote.
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
      if (prop === "then") return undefined; // never look thenable
      return makeElement(`${name}.${String(prop)}`);
    },
    set(_t, prop, value) { store[prop] = value; return true; },
    apply() { return makeElement(`${name}()`); },
  });
}

// Elements that index.html ships with the `hidden` attribute start hidden in the stub too;
// otherwise code such as renderStaleBanner's "offline banner visible -> return" sees a DOM the
// real page never has.
const HIDDEN_IDS = new Set(
  [...fs.readFileSync(path.join(ROOT, "index.html"), "utf8").matchAll(/<[^>]*\bid="([^"]+)"[^>]*>/g)]
    .filter((m) => /\shidden(\s|>|=|\/)/.test(m[0]))
    .map((m) => m[1]),
);

// `globals` are placed on the sandbox BEFORE app.js runs (e.g. a fake `Sentry`, to test load-time init).
export function loadApp({ nowMs, lang = "en", globals = {} } = {}) {
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
  // Optional fixed clock for tests that depend on "today" (IST day keys etc.).
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

  for (const file of ["i18n.js", "app.js"]) {
    vm.runInContext(fs.readFileSync(path.join(ROOT, file), "utf8"), ctx, { filename: file });
  }
  ctx.run = (expr) => vm.runInContext(expr, ctx);
  // Bind a real app.js function for use in assertions. Results are structured-cloned into the
  // caller's realm: objects built inside the vm have a different Object.prototype, which makes
  // assert.deepStrictEqual fail even when the contents are identical.
  ctx.pure = (name) => {
    const fn = ctx[name] ?? ctx.run(`typeof ${name} === "function" ? ${name} : undefined`);
    if (typeof fn !== "function") throw new Error(`app.js has no function named ${name}`);
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
