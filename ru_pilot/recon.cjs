#!/usr/bin/env node
// Recon of the public JSON requests a list page makes, like a visitor: open the page, then type ONE search word into the public search box.
//
//   node recon.cjs URL [word]
//
// Rules: robots.txt of the site is read first (group *), TLS verification stays on, no login, no captcha or protection is bypassed
// (a captcha page is reported as such), request headers, cookies and tokens are never printed.
// Output: one line per XHR/fetch answer: phase | METHOD status size items | url | request body | top-level numbers | keys of the first item.
const path = require("path");
const [url, word] = [process.argv[2], process.argv[3] || ""];
if (!/^https?:\/\//.test(url || "")) { console.error("usage: node recon.cjs URL [word]"); process.exit(2); }
const UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36 misb-collector";
const SKIP = /(mc\.yandex|google|doubleclick|facebook|metrika|hotjar|sentry|mail\.ru|vk\.com|top-fwz|counter)/i;

function robotsAllows(txt, p) {
  let inStar = false, rules = [];
  for (const raw of txt.split(/\r?\n/)) {
    const m = /^([A-Za-z-]+)\s*:\s*(.*)$/.exec(raw.replace(/#.*/, "").trim());
    if (!m) continue;
    const k = m[1].toLowerCase(), v = m[2].trim();
    if (k === "user-agent") inStar = v === "*";
    else if (inStar && (k === "disallow" || k === "allow") && v) rules.push([k, v]);
  }
  let best = null;
  for (const [k, v] of rules) {
    const re = new RegExp("^" + v.replace(/[.+?^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*"));
    if (re.test(p) && (!best || v.length > best[1].length || (v.length === best[1].length && k === "allow"))) best = [k, v];
  }
  return !best || best[0] === "allow";
}

(async () => {
  const u = new URL(url);
  try {
    const r = await fetch(u.origin + "/robots.txt", { headers: { "user-agent": UA }, signal: AbortSignal.timeout(20000) });
    const t = r.ok ? await r.text() : "";
    if (t && !/<html/i.test(t.slice(0, 200)) && !robotsAllows(t, u.pathname + u.search)) { console.log("robots.txt запрещает этот раздел: " + url); return; }
  } catch (e) { /* no robots.txt reachable */ }
  const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ userAgent: UA, locale: "ru-RU", viewport: { width: 1366, height: 900 } });
  const page = await ctx.newPage();
  let phase = "load";
  const pending = [];
  page.on("response", (resp) => {
    const rq = resp.request();
    if (!["xhr", "fetch"].includes(rq.resourceType()) || SKIP.test(resp.url())) return;
    pending.push((async () => {
      let size = 0, items = "-", top = "", keys = "";
      try {
        const body = await resp.text(); size = body.length;
        if (/json/i.test(resp.headers()["content-type"] || "")) {
          const j = JSON.parse(body);
          const arr = Array.isArray(j) ? j : (j && typeof j === "object" ? Object.values(j).find(Array.isArray) : null);
          if (arr) { items = arr.length; if (arr[0] && typeof arr[0] === "object") keys = Object.keys(arr[0]).slice(0, 16).join(","); }
          if (j && !Array.isArray(j) && typeof j === "object")
            top = Object.entries(j).filter(([, v]) => typeof v === "number" || typeof v === "string" && v.length < 30).slice(0, 6).map(([k, v]) => k + "=" + v).join(" ");
        }
      } catch (e) { /* not json */ }
      console.log(`${phase} | ${rq.method()} ${resp.status()} ${size}b items=${items} | ${resp.url().slice(0, 170)} | body=${(rq.postData() || "").slice(0, 200)} | ${top} | ${keys}`);
    })());
  });
  try {
    await page.goto(url, { waitUntil: "domcontentloaded", timeout: 45000 });
    await page.waitForLoadState("networkidle", { timeout: 20000 }).catch(() => {});
    await page.waitForTimeout(4000);
    const title = await page.title();
    const text = (await page.evaluate(() => document.body ? document.body.innerText : "")).replace(/\s+/g, " ").slice(0, 160);
    console.log(`page: ${title} | ${text}`);
    if (word) {
      phase = "search";
      const box = page.locator('input[type=search], input[placeholder*="оиск" i], input[placeholder*="айти" i], input[name*="search" i], input[name*="query" i], input[type=text]').first();
      if (await box.count()) {
        await box.fill(word, { timeout: 8000 });
        await box.press("Enter");
        await page.waitForLoadState("networkidle", { timeout: 15000 }).catch(() => {});
        await page.waitForTimeout(5000);
        console.log(`search done: word=${word} url=${page.url().slice(0, 170)}`);
      } else console.log("search box not found");
    }
  } catch (e) { console.log("error: " + String(e && e.message || e).slice(0, 200)); }
  await Promise.all(pending);
  await browser.close();
})();
