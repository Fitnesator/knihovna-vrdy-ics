#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re
import sys
import argparse
from datetime import datetime, timedelta
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE = "https://www.knihovnavrdy.cz"
LIST_URL = f"{BASE}/akce"
TZID = "Europe/Prague"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; VrdyScraper/1.0)",
}

def fetch(url):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text

def parse_list(html):
    soup = BeautifulSoup(html, "html.parser")
    # H3 > a odkazy na akce (viz /akce)
    links = []
    for h3 in soup.select("h3 a[href]"):
        href = h3.get("href", "")
        if href.startswith("/akce/"):
            links.append(urljoin(BASE, href))
    # Unikátní a v pořadí
    seen = set()
    out = []
    for l in links:
        if l not in seen:
            out.append(l)
            seen.add(l)
    return out

def clean_text(x):
    return re.sub(r"\s+\n", "\n", re.sub(r"[ \t]+", " ", x)).strip()

def parse_detail(url):
    html = fetch(url)
    soup = BeautifulSoup(html, "html.parser")
    title_el = soup.select_one("h2, h1, .content h2")
    title = title_el.get_text(strip=True) if title_el else "Akce Knihovna Vrdy"

    # v „Základní informace“ je tabulka/definice s položkami
    info_text = " ".join(el.get_text(" ", strip=True) for el in soup.find_all(text=True))
    # Kód
    code_match = re.search(r"\bKód\s+(\d+)\b", info_text)
    code = code_match.group(1) if code_match else None

    # Termín konání: „DD.MM.YYYY HH:MM - HH:MM“ nebo i rozsah dnů
    # pokryjeme formáty:
    # 10.09.2025 16:30 - 18:30
    # 26.10.2025 - 26.10.2025 (případně s časem)
    term_block = ""
    term_match = re.search(r"Termín konání\s+([0-9.\s:-]+)", info_text)
    if term_match:
        term_block = term_match.group(1).strip()

    start = end = None
    # Nejčastější varianta: 10.09.2025 16:30 - 18:30
    m = re.match(r"(\d{2}\.\d{2}\.\d{4})\s+(\d{2}:\d{2})\s*-\s*(\d{2}:\d{2})", term_block)
    if m:
        d, t1, t2 = m.groups()
        start = datetime.strptime(f"{d} {t1}", "%d.%m.%Y %H:%M")
        end = datetime.strptime(f"{d} {t2}", "%d.%m.%Y %H:%M")
    else:
        # Zkus čistě datum -> allday (DTEND = next day)
        m2 = re.match(r"(\d{2}\.\d{2}\.\d{4})\s*-\s*(\d{2}\.\d{2}\.\d{4})", term_block)
        if m2:
            d1, d2 = m2.groups()
            start = datetime.strptime(d1, "%d.%m.%Y")
            end = datetime.strptime(d2, "%d.%m.%Y") + timedelta(days=1)  # all-day range
        else:
            m3 = re.match(r"(\d{2}\.\d{2}\.\d{4})(?:\s+(\d{2}:\d{2}))?", term_block)
            if m3:
                d, t = m3.groups()
                if t:
                    start = datetime.strptime(f"{d} {t}", "%d.%m.%Y %H:%M")
                    end = start + timedelta(hours=2)  # fallback délka 2h
                else:
                    start = datetime.strptime(d, "%d.%m.%Y")
                    end = start + timedelta(days=1)  # all-day 1 den

    # Místo konání
    place = None
    pm = re.search(r"Místo konání\s+([A-Za-zÁ-ž0-9 \-\,\.\(\)\/]+)", info_text)
    if pm:
        place = pm.group(1).strip()

    # Popis – vezmeme shrnutí z hlavního obsahu
    body = soup.select_one(".content") or soup
    desc = []
    for p in body.select("p"):
        txt = clean_text(p.get_text(" ", strip=True))
        if txt:
            desc.append(txt)
    description = clean_text("\n\n".join(desc))
    description += f"\n\nURL: {url}"

    return {
        "uid": f"knihovnavrdy-{code or abs(hash(url))}@vrdy",
        "title": title,
        "start": start,  # datetime or None
        "end": end,      # datetime or None
        "allday": start is not None and (start.hour == 0 and start.minute == 0 and (end - start) in (timedelta(days=1),)),
        "location": place or "Knihovna F. V. Lorence Vrdy",
        "description": description,
        "url": url,
    }

def dt_local_ics(dt):
    # Lokální (floating) s TZID
    return dt.strftime("%Y%m%dT%H%M%S")

def build_ics(events):
    now = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "PRODID:-//Vrdy Knihovna Scraper//CZ",
        "VERSION:2.0",
        f"X-WR-TIMEZONE:{TZID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:Knihovna Vrdy – Akce",
    ]
    for ev in events:
        if ev["start"] is None or ev["end"] is None:
            # přeskoč akce bez „Termín konání“
            continue
        lines += [
            "BEGIN:VEVENT",
            f"UID:{ev['uid']}",
            f"DTSTAMP:{now}",
            f"SUMMARY:{ev['title'].replace(',', '\\,')}",
            f"LOCATION:{(ev['location'] or '').replace(',', '\\,')}",
            f"DESCRIPTION:{ev['description'].replace('\\n', '\\n').replace(',', '\\,')}",
            f"URL:{ev['url']}",
            f"DTSTART;TZID={TZID}:{dt_local_ics(ev['start'])}",
            f"DTEND;TZID={TZID}:{dt_local_ics(ev['end'])}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="Cesta k výstupnímu .ics souboru")
    args = ap.parse_args()

    listing = fetch(LIST_URL)
    detail_urls = parse_list(listing)
    events = []
    for u in detail_urls:
        try:
            ev = parse_detail(u)
            events.append(ev)
        except Exception as e:
            print(f"[WARN] {u}: {e}", file=sys.stderr)

    ics = build_ics(events)
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        f.write(ics)
    print(f"OK -> {args.out}  ({len([e for e in events if e['start'] and e['end']])} událostí)")

if __name__ == "__main__":
    main()
