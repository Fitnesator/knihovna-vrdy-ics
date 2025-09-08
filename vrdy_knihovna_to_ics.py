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

# --- NOVÉ: parser výpisu /akce ---------------------------------------------

def parse_listing(html):
    """
    Vrátí list eventů přímo z výpisu /akce (bez prokliků do detailu).
    Struktura na webu: bloky s <h3> NÁZEV </h3> a pod nimi řádky:
      * Věk …
      * Místo …
      * "DD.MM.YYYY - DD.MM.YYYY" nebo "Termín neuveden"
      + 1–N odstavců popisu
    """
    soup = BeautifulSoup(html, "html.parser")
    events = []

    # vezmeme všechny H3 v sekci "Připravované akce"
    # (na stránce je jen jeden hlavní seznam, takže bereme všechny h3 v obsahu)
    for h3 in soup.find_all("h3"):
        title = h3.get_text(strip=True)
        block = []

        # posbírej sourozence až do dalšího H3 nebo konce sekce
        for sib in h3.next_siblings:
            if getattr(sib, "name", None) == "h3":
                break
            if getattr(sib, "name", None) in ("ul", "p", "div", "span"):
                text = sib.get_text(" ", strip=True)
                if text:
                    block.append(text)

        # spojíme do jednoho textu pro regexy
        blob = " \n".join(block)

        # Místo (řádek, kde je jen "Knihovna", "zájezd" apod.)
        location = None
        loc_match = re.search(r"\b(Knihovna|zájezd)\b", blob, flags=re.IGNORECASE)
        if loc_match:
            location = loc_match.group(1).capitalize()

        # Termín
        #  a) 10.09.2025 - 10.09.2025
        #  b) 18.09.2025 19:00 - 21:10  (někdy může být i čas)
        #  c) Termín neuveden  -> přeskočíme
        start = end = None
        term_text = None

        # najdi první něco, co vypadá jako datumový řádek
        for line in blob.splitlines():
            if "Termín" in line and "neuveden" in line.lower():
                term_text = "Termín neuveden"
                break
            if re.search(r"\d{2}\.\d{2}\.\d{4}", line):
                term_text = line.strip()
                break

        if term_text and term_text.lower().startswith("termín neuveden"):
            # akce bez termínu do ICS nedáváme
            continue

        if term_text:
            # pokus 1: "DD.MM.YYYY - DD.MM.YYYY"
            m = re.search(r"(\d{2}\.\d{2}\.\d{4})\s*-\s*(\d{2}\.\d{2}\.\d{4})", term_text)
            if m:
                d1, d2 = m.groups()
                start = datetime.strptime(d1, "%d.%m.%Y")
                # all-day rozsah: DTEND je o den později za poslední den
                end = datetime.strptime(d2, "%d.%m.%Y") + timedelta(days=1)
            else:
                # pokus 2: "DD.MM.YYYY HH:MM - HH:MM" nebo "DD.MM.YYYY"
                m2 = re.search(r"(\d{2}\.\d{2}\.\d{4})(?:\s+(\d{2}:\d{2}))?\s*(?:-\s*(\d{2}:\d{2}))?", term_text)
                if m2:
                    d, t1, t2 = m2.groups()
                    if t1 and t2:
                        start = datetime.strptime(f"{d} {t1}", "%d.%m.%Y %H:%M")
                        end = datetime.strptime(f"{d} {t2}", "%d.%m.%Y %H:%M")
                    elif t1:
                        start = datetime.strptime(f"{d} {t1}", "%d.%m.%Y %H:%M")
                        end = start + timedelta(hours=2)
                    else:
                        start = datetime.strptime(d, "%d.%m.%Y")
                        end = start + timedelta(days=1)

        if not (start and end):
            # bezpečnostní brzda – bez data do ICS nedáváme
            continue

        # Popis: vezmeme všechny odstavce nasbírané v bloku
        description = blob.strip()
        url = LIST_URL  # nemáme detail, odkazujeme aspoň na výpis

        events.append({
            "uid": f"knihovnavrdy-{abs(hash(title + term_text))}@vrdy",
            "title": title,
            "start": start,
            "end": end,
            "location": location or "Knihovna F. V. Lorence Vrdy",
            "description": description,
            "url": url,
        })

    return events


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
        "X-WR-CALNAME:Knihovna Vrdy – Akce",
    ]
    for ev in events:
        if ev["start"] is None or ev["end"] is None:
            continue

        summary = ics_escape(ev["title"])
        location = ics_escape(ev.get("location") or "")
        description = ics_escape(ev.get("description") or "")
        url = ev.get("url") or ""

        lines += [
            "BEGIN:VEVENT",
            f"UID:{ev['uid']}",
            f"DTSTAMP:{now}",
            f"SUMMARY:{summary}",
            f"LOCATION:{location}",
            f"DESCRIPTION:{description}",
            f"URL:{url}",
            f"DTSTART;TZID={TZID}:{dt_local_ics(ev['start'])}",
            f"DTEND;TZID={TZID}:{dt_local_ics(ev['end'])}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines)
    return "\r\n".join(lines)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="Cesta k výstupnímu .ics souboru")
    args = ap.parse_args()

    listing = fetch(LIST_URL)
    events = parse_listing(listing)   # << místo původního parse_list + parse_detail

    ics = build_ics(events)
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        f.write(ics)
    print(f"OK -> {args.out}  ({len(events)} událostí)")


if __name__ == "__main__":
    main()
