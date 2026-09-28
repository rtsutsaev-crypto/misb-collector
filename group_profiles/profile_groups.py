#!/usr/bin/env python3
"""Procurement profiles for holding groups: head companies, subsidiaries and
their subsidiaries, from the GosPlan API (EIS data).

Usage:
  GOSPLAN_KEY=... python3 profile_groups.py targets W
  GOSPLAN_KEY=... python3 profile_groups.py fetch W [--limit N] [--workers 6] [--pause 1.0]
  python3 profile_groups.py build W

W is a work dir holding the site's database dump (ArtifactData out_dir):
  W/db/meta/holdings.json, W/db/orgdir/hier-*.json, W/db/orgdir/orgs-*.json,
  W/db/ownership/*.json, W/db/config/dictionary.json, W/db/config/sources-plan.json,
  W/db/leadsets/*.json.

targets -> W/out/group-targets.json (the doc config/group-targets)
fetch   -> W/raw/<inn>-223.json, W/raw/<inn>-44.json (resumable)
build   -> W/out/profiles/<head inn>.json (docs groupprofiles/<head inn>: group totals
           plus a members list), W/out/leads.json (new open relevant leads),
           W/out/summary.json
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "b2b_center"))
from b2b_core import STEMS, existing_leads, norm_title, words  # noqa: E402

BASE = "https://v2.gosplan.info"
INN10 = re.compile(r"^\d{10}$")
# 44-FZ is only for state/municipal bodies; commercial subsidiaries buy under 223-FZ.
STATE_RE = re.compile(r"(^|[^а-яё])(фгуп|гуп|муп|фгбу|фгау|фгку|гбу|гау|гку|мбу|мау|мку|фгбоу|гбоу|учрежден|казенн|казённ)", re.I)
LEVELS = {"group": "головная", "дочка": "дочка", "внучка": "внучка", "правнучка": "правнучка"}
REGIONS = {1: "Адыгея", 2: "Башкортостан", 3: "Бурятия", 4: "Алтай Республика", 5: "Дагестан", 6: "Ингушетия",
           7: "Кабардино-Балкарская Республика", 8: "Калмыкия", 9: "Карачаево-Черкесская Республика", 10: "Карелия",
           11: "Коми", 12: "Марий Эл", 13: "Мордовия", 14: "Саха (Якутия)", 15: "Северная Осетия — Алания",
           16: "Татарстан", 17: "Тыва", 18: "Удмуртская Республика", 19: "Хакасия", 20: "Чеченская Республика",
           21: "Чувашская Республика", 22: "Алтайский край", 23: "Краснодарский край", 24: "Красноярский край",
           25: "Приморский край", 26: "Ставропольский край", 27: "Хабаровский край", 28: "Амурская область",
           29: "Архангельская область", 30: "Астраханская область", 31: "Белгородская область", 32: "Брянская область",
           33: "Владимирская область", 34: "Волгоградская область", 35: "Вологодская область", 36: "Воронежская область",
           37: "Ивановская область", 38: "Иркутская область", 39: "Калининградская область", 40: "Калужская область",
           41: "Камчатский край", 42: "Кемеровская область", 43: "Кировская область", 44: "Костромская область",
           45: "Курганская область", 46: "Курская область", 47: "Ленинградская область", 48: "Липецкая область",
           49: "Магаданская область", 50: "Московская область", 51: "Мурманская область", 52: "Нижегородская область",
           53: "Новгородская область", 54: "Новосибирская область", 55: "Омская область", 56: "Оренбургская область",
           57: "Орловская область", 58: "Пензенская область", 59: "Пермский край", 60: "Псковская область",
           61: "Ростовская область", 62: "Рязанская область", 63: "Самарская область", 64: "Саратовская область",
           65: "Сахалинская область", 66: "Свердловская область", 67: "Смоленская область", 68: "Тамбовская область",
           69: "Тверская область", 70: "Томская область", 71: "Тульская область", 72: "Тюменская область",
           73: "Ульяновская область", 74: "Челябинская область", 75: "Забайкальский край", 76: "Ярославская область",
           77: "Москва", 78: "Санкт-Петербург", 79: "Еврейская автономная область", 83: "Ненецкий автономный округ",
           86: "Ханты-Мансийский автономный округ — Югра", 87: "Чукотский автономный округ",
           89: "Ямало-Ненецкий автономный округ", 91: "Крым", 92: "Севастополь"}


def load(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return default
    return d.get("data", d) if isinstance(d, dict) and "data" in d and "id" in d else d


def dump(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def rows_of(pattern):
    out = []
    for p in sorted(glob.glob(pattern)):
        out += (load(p, {}) or {}).get("rows", []) or []
    return out


# ---------- relevance ----------
# Forms the dictionary stems miss ("дети" does not prefix "детей").
EXTRA_NEG = ["детей", "детям", "детьми", "детях", "детск", "ребен", "ребён", "школьник", "учащихся",
             "застройк", "выставочн"]  # stand build-outs at forums carry the events OKPD2 code


# Forms too generic to count alone (software testing, attestation of workplaces,
# IT "information and consulting services"): they need a topic hit as well.
WEAK_FORMS = {"тестирование", "аттестация", "модерация", "экспертная оценка", "методическое сопровождение",
              "информационно-консультационные услуги", "корпоративная программа", "модульная программа",
              "executive", "олимпиада", "практикум", "интенсив", "кадровый резерв", "деловая программа",
              "образовательн", "корпоративн мероприяти", "оценк персонал", "развити персонал",
              "дпо"}  # «АНО ДПО …» is often the customer's own name


class GroupMatcher:
    """config/dictionary. Relevant = a training form (formats, or the shared
    STEMS, or an OKPD2 code from the dictionary) and no exclusion; a weak form
    also needs a topic. Words of up to 3 letters (ЛИН, ДПО, ГОЗ) match whole
    words, longer ones by stem."""

    def __init__(self, dic):
        dic = dic or {}
        self.topics = self._phrases(dic, ("topics",))
        # STEMS are already stems: match them as prefixes as they are
        self.forms = self._phrases(dic, ("formats",)) + [(s, [(w, w, False) for w in words(s)]) for s in STEMS]
        self.neg = self._phrases(dic, ("exclude",)) + [(s, [(s, s, False)]) for s in EXTRA_NEG]
        self.okpd = tuple(str(c) for c in dic.get("okpd2", []) or [])

    @staticmethod
    def _stem(w):
        """Drop up to two ending vowels ("обучение" -> "обучен", "экология" -> "эколог");
        a word ending in a consonant loses one letter at most ("видеокурс" -> "видеокур",
        so it does not match "видеокамеры"; "продажи" -> "продаж", not "прод")."""
        n = len(w)
        if n <= 5:
            return w
        s = w
        while len(s) > 4 and n - len(s) < 2 and s[-1] in "аеёиоуыэюяйь":
            s = s[:-1]
        return s if len(s) < n else w[:max(5, n - 1)]

    @classmethod
    def _parts(cls, phrase):
        return [(w, cls._stem(w), len(w) <= 3) for w in words(phrase)]

    def _phrases(self, dic, keys):
        out = []
        for key in keys:
            for entry in dic.get(key, []) or []:
                if key == "formats" and str(entry).lower().startswith("обязательное обучение"):
                    continue  # lists subjects (охрана труда, первая помощь), not forms; they are topics too
                for ph in str(entry).split(","):
                    ph = ph.split("(")[0].strip()
                    parts = self._parts(ph)
                    if parts and not all(w.isdigit() for w, _, _ in parts):
                        out.append((ph, parts))
        return out

    @staticmethod
    def _hit(tw, parts):
        return all(any((t == w) if exact else t.startswith(st) for t in tw) for w, st, exact in parts)

    def terms(self, title, okpd2=()):
        tw = words(title)
        forms = [ph for ph, parts in self.forms if self._hit(tw, parts)]
        topics = [ph for ph, parts in self.topics if self._hit(tw, parts)]
        codes = ["ОКПД2 " + c for c in okpd2 or [] if self.okpd and str(c).startswith(self.okpd)]
        strong = [f for f in forms if f.lower() not in WEAK_FORMS]
        excluded = any(self._hit(tw, parts) for _, parts in self.neg)
        ok = bool(strong or codes or (forms and topics)) and not excluded
        return list(dict.fromkeys(forms + topics + codes))[:6], ok


# ---------- targets ----------
def build_targets(W):
    hold = load(os.path.join(W, "db", "meta", "holdings.json"), {})
    groups = hold.get("groups", [])
    plan = load(os.path.join(W, "db", "config", "sources-plan.json"), {})
    gp = next((s for s in plan.get("sources", []) if s.get("key") == "group-profile"), {})
    rank = {x.get("inn"): i for i, x in enumerate(gp.get("groups", []))}
    groups = sorted(groups, key=lambda g: rank.get(g.get("inn"), 10_000 + (g.get("registryRank") or 0)))
    by_hier = {g.get("hierGroup"): g for g in groups if g.get("hierGroup")}
    by_name = {g["name"]: g for g in groups}
    per = {g["name"]: [] for g in groups}
    seen = {}

    def add(gname, inn, name, level, parent, src):
        inn = str(inn or "").strip()
        if not INN10.match(inn) or gname not in per:
            return
        if inn in seen:  # first (closest to the head) wins
            return
        seen[inn] = gname
        per[gname].append({"inn": inn, "name": name, "group": gname, "level": level, "parent": parent or "", "src": src})

    for g in groups:
        add(g["name"], g.get("inn"), g.get("head") or g["name"], "головная", "", "реестр холдингов")
    hier = rows_of(os.path.join(W, "db", "orgdir", "hier-*.json"))
    order = {"дочка": 1, "внучка": 2, "правнучка": 3}
    for r in sorted((r for r in hier if r.get("lvl") in order), key=lambda r: order[r["lvl"]]):
        g = by_hier.get(r.get("g")) or by_name.get(r.get("g"))
        if g:
            add(g["name"], r.get("inn"), r.get("n"), LEVELS[r["lvl"]], r.get("p"), "справочник: структура группы (заявлено)")
    orgs = rows_of(os.path.join(W, "db", "orgdir", "orgs-[0-9]*.json"))
    names = {str(o.get("inn")): o.get("n") for o in orgs if o.get("inn")}
    names.update({str(r.get("inn")): r.get("n") for r in hier if r.get("inn")})
    index = {}
    for p in sorted(glob.glob(os.path.join(W, "db", "orgdir", "index-*.json"))):
        index.update((load(p, {}) or {}).get("entries", {}) or {})
    for k, v in sorted(index.items(), key=lambda kv: order.get(kv[1].get("lvl"), 4)):
        if not k.startswith("i:") or v.get("st") == "historical" or str(v.get("lvl", "")).startswith("historical"):
            continue
        g = by_hier.get(v.get("g")) or by_name.get(v.get("g"))
        if g:
            add(g["name"], k[2:], names.get(k[2:]) or "", LEVELS.get(v.get("lvl"), "компания группы"), v.get("p"),
                "справочник: индекс организаций (" + ("проверено" if v.get("st") == "verified" else "заявлено") + ")")
    for o in orgs:
        g = by_hier.get(o.get("g")) or by_name.get(o.get("g"))
        if g:
            add(g["name"], o.get("inn"), o.get("n"), "компания группы", "", "справочник организаций (заявлено)")
    for p in sorted(glob.glob(os.path.join(W, "db", "ownership", "*.json"))):
        o = load(p, {}) or {}
        if o.get("group") in per:
            chain = o.get("chain") or []
            add(o["group"], o.get("inn"), o.get("name"), "по ЕГРЮЛ", chain[0].get("name") if chain else "", "ЕГРЮЛ (Чекко)")
    targets = [t for g in groups for t in per[g["name"]]]
    doc = {"updatedAt": dt.date.today().isoformat(), "count": len(targets),
           "note": "Компании групп для профилей закупок: головные из реестра холдингов, дочки/внучки/правнучки и компании групп из справочника организаций (связи заявленные), привязки по ЕГРЮЛ. Порядок — по реестру групп, внутри группы от головной вниз.",
           "targets": targets}
    dump(os.path.join(W, "out", "group-targets.json"), doc)
    lv = {}
    for t in targets:
        lv[t["level"]] = lv.get(t["level"], 0) + 1
    print(json.dumps({"targets": len(targets), "levels": lv, "groups": sum(1 for v in per.values() if v)}, ensure_ascii=False))


# ---------- fetch ----------
def wants44(t):
    return t["level"] == "головная" or bool(STATE_RE.search(t.get("name") or ""))


def get(url, tries=5):
    delay = 2.0
    for i in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                rem = r.headers.get("x-ratelimit-remaining-second")
                body = r.read().decode("utf-8")
                if rem is not None and int(rem) <= 1:
                    time.sleep(1.0)
                return 200, body
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and i < tries - 1:
                time.sleep(delay); delay *= 2; continue
            return e.code, e.read().decode("utf-8", "replace")[:500]
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            if i < tries - 1:
                time.sleep(delay); delay *= 2; continue
            return 0, str(e)[:300]
    return 0, "retries exhausted"


def fetch(W, limit=None, workers=6, pause=1.0):
    """Resumable; `workers` parallel requests, each followed by `pause` s
    (about 4 req/s at the observed 1-2 s latency; GosPlan allows 10/s, 12000/h)."""
    from concurrent.futures import ThreadPoolExecutor
    key = os.environ.get("GOSPLAN_KEY")
    if not key:
        sys.exit("GOSPLAN_KEY is not set")
    doc = load(os.path.join(W, "out", "group-targets.json"), {})
    raw = os.path.join(W, "raw")
    os.makedirs(raw, exist_ok=True)
    jobs = [(t["inn"], law) for t in doc.get("targets", [])[: limit or None]
            for law in (["223", "44"] if wants44(t) else ["223"])
            if not os.path.exists(os.path.join(raw, f"{t['inn']}-{law}.json"))]

    def one(job):
        inn, law = job
        q = urllib.parse.urlencode({"customer": inn, "published_forpast": "12m", "limit": 100,
                                    "sort": "published_at_desc", "apikey": key})
        code, body = get(f"{BASE}/fz{law}/purchases?{q}")
        path = os.path.join(raw, f"{inn}-{law}.json")
        if code == 200:
            with open(path, "w", encoding="utf-8") as f:
                f.write(body)
        else:
            dump(path + ".err", {"code": code, "body": body.replace(key, "***")})
        time.sleep(pause)
        return code == 200

    n = errs = 0
    with ThreadPoolExecutor(workers) as ex:
        for ok in ex.map(one, jobs):
            n += 1
            errs += not ok
            if n % 100 == 0:
                print(f"requests {n}/{len(jobs)}, errors {errs}", flush=True)
    print(json.dumps({"requests": n, "errors": errs}))


# ---------- build ----------
LEVEL_ORDER = {"головная": 0, "дочка": 1, "внучка": 2, "правнучка": 3, "компания группы": 4, "по ЕГРЮЛ": 5}


def company(W, t, M, today, ids, pairs, leads):
    """Profile of one company from its raw answers, or None if not fetched yet."""
    rows, laws_done, err = [], [], []
    for law in ("223", "44"):
        ok = os.path.join(W, "raw", f"{t['inn']}-{law}.json")
        if os.path.exists(ok):
            data = load(ok, [])
            if isinstance(data, list):
                rows += [(law, r) for r in data]
                laws_done.append(law)
            else:
                err.append(f"{law}-ФЗ: неожиданный ответ")
        elif os.path.exists(ok + ".err"):
            err.append(f"{law}-ФЗ: HTTP {(load(ok + '.err', {}) or {}).get('code')}")
    if not laws_done and not err:
        return None, []
    plat, rel, ex = {}, 0, []
    for law, r in rows:
        pt = r.get("purchase_type")
        if pt:
            plat[pt] = plat.get(pt, 0) + 1
        title = " ".join(str(r.get("object_info") or "").split())
        terms, hit = M.terms(title, r.get("okpd2"))
        if not hit:
            continue
        rel += 1
        num = str(r.get("purchase_number") or "")
        url = f"https://zakupki.gov.ru/epz/order/extendedsearch/results.html?searchString={num}"
        close = str((r.get("submission_close_at") if law == "223" else r.get("collecting_finished_at")) or "")[:10]
        ex.append({"id": num, "title": title[:240], "date": str(r.get("published_at") or "")[:10], "law": f"{law}-ФЗ",
                   "url": url, "company": t["name"], "deadline": close, "terms": terms[:3]})
        if close and close >= today and num and num not in ids and (norm_title(title), close) not in pairs:
            reg = r.get("region")
            leads.append({"collectedAt": today, "customer": t["name"] or f"ИНН {t['inn']}", "customerInn": t["inn"],
                          "deadline": close, "flags": [], "id": num, "law": f"{law}-ФЗ", "price": r.get("max_price"),
                          "region": REGIONS.get(int(reg), "") if str(reg or "").isdigit() else "",
                          "source": "gosplan:group", "title": title, "url": url,
                          "note": f"Профиль группы {t['group']}: {t['level']}; совпало: {', '.join(terms[:3])}"})
            ids.add(num)
    n223 = sum(1 for law, _ in rows if law == "223")
    n44 = sum(1 for law, _ in rows if law == "44")
    m = {"inn": t["inn"], "name": t["name"], "level": t["level"], "parent": t["parent"], "total": n223 + n44,
         "t223": n223, "t44": n44 if "44" in laws_done else None, "relevant": rel}
    if err and not rows:
        m["note"] = "ошибка запроса: " + "; ".join(err)
    elif max(n223, n44) >= 100:
        m["note"] = "в выборке 100 последних закупок по закону, на деле больше"
    return (m, plat), ex


def build(W):
    today = dt.date.today().isoformat()
    doc = load(os.path.join(W, "out", "group-targets.json"), {})
    M = GroupMatcher(load(os.path.join(W, "db", "config", "dictionary.json"), {}))
    ids, pairs = existing_leads(W)
    leads, groups = [], {}
    for t in doc.get("targets", []):
        groups.setdefault(t["group"], []).append(t)
    hold = {g["name"]: g for g in (load(os.path.join(W, "db", "meta", "holdings.json"), {}) or {}).get("groups", [])}
    outp = os.path.join(W, "out", "profiles")
    os.makedirs(outp, exist_ok=True)
    for f in glob.glob(os.path.join(outp, "*.json")):
        os.remove(f)
    summary = {"groups": 0, "companies": 0, "withPurchases": 0, "purchases": 0, "relevant": 0, "errors": 0}
    for gname, ts in groups.items():
        head = hold.get(gname, {}).get("inn") or ts[0]["inn"]
        members, plat, ex = [], {}, []
        for t in ts:
            res, e = company(W, t, M, today, ids, pairs, leads)
            if not res:
                continue
            m, pl = res
            members.append(m)
            ex += e
            for k, v in pl.items():
                plat[k] = plat.get(k, 0) + v
        if not members:
            continue
        members.sort(key=lambda m: (LEVEL_ORDER.get(m["level"], 9), -m["total"], m["name"] or ""))
        ex.sort(key=lambda x: x["date"], reverse=True)
        hm = next((m for m in members if m["inn"] == head), None)
        withp = [m for m in members if m["total"]]
        tot = sum(m["total"] for m in members)
        rel = sum(m["relevant"] for m in members)
        errs = sum(1 for m in members if str(m.get("note", "")).startswith("ошибка"))
        parts = [f"проверено компаний: {len(members)}, с закупками в ЕИС за год: {len(withp)}"]
        if hm is not None and not hm["total"]:
            parts.append("головная компания закупки в ЕИС не публикует")
        if errs:
            parts.append(f"ошибок запроса: {errs}")
        prof = {"group": gname, "inn": head, "checkedAt": today, "scope": "группа",
                "companies": len(members), "withPurchases": len(withp),
                "total": tot, "total223": sum(m["t223"] for m in members),
                "total44": sum(m["t44"] or 0 for m in members), "relevant": rel,
                "platforms": dict(sorted(plat.items(), key=lambda kv: -kv[1])[:12]),
                "examples": ex[:8], "members": members, "note": "; ".join(parts) + ".",
                "via": "ГосПлан: закупки по ИНН заказчика за 12 месяцев (223-ФЗ; 44-ФЗ для головных и учреждений), до 100 последних по закону; отбор по словарю сайта"}
        dump(os.path.join(outp, f"{head}.json"), prof)
        summary["groups"] += 1
        summary["companies"] += len(members)
        summary["withPurchases"] += len(withp)
        summary["purchases"] += tot
        summary["relevant"] += rel
        summary["errors"] += errs
    leads.sort(key=lambda l: l["deadline"])
    dump(os.path.join(W, "out", "leads.json"), leads)
    summary["newLeads"] = len(leads)
    dump(os.path.join(W, "out", "summary.json"), summary)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    cmd, W = sys.argv[1], sys.argv[2]
    if cmd == "targets":
        build_targets(W)
    elif cmd == "fetch":
        opt = lambda k, d, f: f(sys.argv[sys.argv.index(k) + 1]) if k in sys.argv else d  # noqa: E731
        fetch(W, opt("--limit", None, int), opt("--workers", 6, int), opt("--pause", 1.0, float))
    elif cmd == "build":
        build(W)
    else:
        sys.exit(__doc__)
