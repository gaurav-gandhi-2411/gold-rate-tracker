// scripts/hindi_audit_extract.js -- inventories every en/hi catalogue string (i18n.js and
// how-we-know-strings.js) into JSON for the Hindi wording audit (reports/hindi_audit_2026-10/).
//
// Function-valued entries are rendered with a Proxy that returns a visible placeholder for every
// parameter ("{delta}"), plus one extra variant per parameter with that parameter nulled, so the
// conditional branches (e.g. verdictReasonUp with/without avgDelta) are each inventoried.
//
// Usage: node scripts/hindi_audit_extract.js <out.json>
// Dep-free (node builtins only).
"use strict";
const fs = require("fs");
const path = require("path");

// HINDI_AUDIT_ROOT lets the "before" inventory be built from a checkout of master.
const ROOT = process.env.HINDI_AUDIT_ROOT || path.resolve(__dirname, "..");
const SOURCES = [
  { file: "i18n.js", catalogue: "STRINGS", screen: "main page (index.html)" },
  { file: "how-we-know-strings.js", catalogue: "STRINGS_HWK", screen: "how-we-know.html" },
];

function loadCatalogues() {
  const i18n = fs.readFileSync(path.join(ROOT, "i18n.js"), "utf8");
  const hwk = fs.readFileSync(path.join(ROOT, "how-we-know-strings.js"), "utf8");
  const stub = "const localStorage={getItem(){return null},setItem(){}};" +
    "const navigator={languages:['en']};const document={documentElement:{}};";
  // eslint-disable-next-line no-new-func
  // fractionOutOf10Phrase gets a representative numeric input (the proxy would hand it a
  // "{pct}" string -> NaN); setLang switches the language the language-aware helper reads.
  const tail = "\nconst _f = fractionOutOf10Phrase; fractionOutOf10Phrase = () => _f(75);" +
    "\nreturn {STRINGS, STRINGS_HWK, setLang: (l) => { currentLang = l; }};";
  return new Function(stub + i18n + "\n" + hwk + tail)();
}

function render(fn) {
  const seen = [];
  const make = (nullKey) =>
    new Proxy({}, {
      get(_t, name) {
        if (typeof name !== "string") return undefined;
        if (!seen.includes(name)) seen.push(name);
        return name === nullKey ? null : `{${name}}`;
      },
    });
  const variants = [];
  const run = (nullKey) => {
    try {
      const out = fn(make(nullKey));
      // A null/undefined/NaN in the output means the param was nulled where the string never
      // expects null (e.g. "₹null/gram"): not a real branch, so not inventoried.
      if (typeof out === "string" && !/null|undefined|NaN/.test(out) && !variants.some((v) => v.text === out)) {
        variants.push({ variant: nullKey ? `${nullKey}=null` : "full", text: out });
      }
    } catch (_e) { /* a variant that throws on null is simply not a real branch */ }
  };
  run(null);
  for (const k of [...seen]) run(k);
  return variants;
}

// key -> {line, section} for one `xx: {` block of a catalogue source file.
function keyLocations(file, lang) {
  const lines = fs.readFileSync(path.join(ROOT, file), "utf8").split(/\r?\n/);
  const out = {};
  let inBlock = false;
  let section = "";
  for (let i = 0; i < lines.length; i++) {
    const l = lines[i];
    if (new RegExp(`^  ${lang}: \\{`).test(l)) { inBlock = true; section = ""; continue; }
    if (inBlock && /^  \},?\s*$/.test(l)) { inBlock = false; continue; }
    if (!inBlock) continue;
    const s = l.match(/^\s{4}\/\/ ── (.*?) ─/);
    if (s) section = s[1];
    const k = l.match(/^\s{4}(\w+):/);
    if (k) out[k[1]] = { line: i + 1, section };
  }
  return out;
}

const PLACEHOLDER = /\{[A-Za-z0-9_]+\}|\$\{|₹\s?\{/;
const DEVANAGARI = /[ऀ-ॿ]/;

function kindOf(key, text, section) {
  if (/AriaLabel|Aria$|^pv2AriaLabel$/.test(key)) return "aria-label";
  if (/Heading$|Eyebrow$|^eyebrow$/.test(key)) return "heading";
  if (/Label$|Btn|^nav|^th[A-Z]|^rel|^cmp|^avgLabel|^karat/.test(key) && text.length < 40) return "label";
  if (/^banner|^offline|^err/.test(key)) return "banner/message";
  if (text.length < 28) return "short label";
  return "sentence";
}

function main() {
  const outPath = process.argv[2];
  if (!outPath) { console.error("usage: node scripts/hindi_audit_extract.js <out.json>"); process.exit(2); }
  const cats = loadCatalogues();
  const rows = [];
  for (const src of SOURCES) {
    const cat = cats[src.catalogue];
    const enLoc = keyLocations(src.file, "en");
    const hiLoc = keyLocations(src.file, "hi");
    for (const key of Object.keys(cat.en)) {
      const en = cat.en[key];
      const hi = cat.hi[key];
      cats.setLang("en");
      const enV = typeof en === "function" ? render(en) : [{ variant: "full", text: en }];
      cats.setLang("hi");
      const hiV = hi === undefined ? [] : typeof hi === "function" ? render(hi) : [{ variant: "full", text: hi }];
      cats.setLang("en");
      rows.push({
        key,
        file: src.file,
        catalogue: src.catalogue,
        screen: src.screen,
        section: (enLoc[key] || {}).section || "",
        en_line: (enLoc[key] || {}).line || null,
        hi_line: (hiLoc[key] || {}).line || null,
        has_hindi: hi !== undefined,
        is_function: typeof en === "function",
        en_variants: enV,
        hi_variants: hiV,
        hi_chars: hiV.map((v) => [...v.text].length),
        has_placeholder: hiV.some((v) => PLACEHOLDER.test(v.text)) || enV.some((v) => PLACEHOLDER.test(v.text)),
        kind: kindOf(key, (hiV[0] || enV[0]).text, ""),
        hi_has_devanagari: hiV.some((v) => DEVANAGARI.test(v.text)),
      });
    }
    for (const key of Object.keys(cat.hi)) {
      if (!(key in cat.en)) rows.push({ key, file: src.file, orphan_hi_key: true });
    }
  }
  fs.writeFileSync(outPath, JSON.stringify(rows, null, 1) + "\n", "utf8");
  const withHi = rows.filter((r) => r.has_hindi).length;
  console.log(`keys=${rows.length} with_hindi=${withHi} without_hindi=${rows.length - withHi}`);
}

main();
