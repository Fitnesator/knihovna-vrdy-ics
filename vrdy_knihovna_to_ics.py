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
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; VrdyScraper/1.1)"}

def fetch(url):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text

def clean_text(x: str) -> str:
    return re.sub(r"\s+\n", "\n", re.sub(r"[ \t]+", " ", x or "")).strip()

# ---- NOVĚ: parsování přímo z /akce (bez detailů) ----
def parse_listing(html):
    soup = BeautifulSoup(html, "html.parser")
    events = []

    # Každá akce má <h3> a pod ním blok textu (odstavce/seznamy) až do dalšího <h3>
    for h3 in soup.find_all("h3"):
        title = h3.get_text(strip=True)
        if not title:
            continue

        # posbírej sourozence do dalšího H3
        block = []
        for sib in h3.next_siblings:
            if getattr(sib, "name", None) == "h3":
                break
            if getattr(sib, "name", None) in ("ul", "ol", "p", "div", "span"):
                t = sib.get_text(" ", strip=True)
                if t:
                    block.append(t)

        blob = " \n".join(block)
        if not blob:
            continue

        # Místo – heuristika (knihovna / zájezd apod.)
        location = None
        loc_match = re.search(r"\b(Knihovna[^\n]*|zájezd[^\n]*)\b", blob, flags=re.IGNORECASE)
        if loc_match:
            location = clean_text(loc_match.group(1)).capitalize()

        # Najdi řádek s datem/rozsahem/časem
        term_text = None
        for line in blob.splitlines():
            line = line.strip()
            if not line:
                continue
            if "Termín" in line and "neuveden" in line.lower():
                term_text = "Termín neuveden"
                break
            if re.search(r"\d{2}\.\d{2}\.\d{4}", line):
                term_text = line
                break

        if not term_text or term_text.lower().startswith("termín neuveden"):
            # Bez termínu do ICS nedáváme
            continue

        start = end = None
        # a) "DD.MM.YYYY - DD.MM.YYYY"
        m = re.search(r"(\d{2}\.\d{2}\.\d{4})\s*-\s*(\d{2}\.\d{2}\.\d{4})", term_text)
        if m:
            d1, d2 = m.groups()
            start = datetime.strptime(d1, "%d.%m.%Y")
            end = datetime.strptime(d2, "%d.%m.%Y") + timedelta(days=1)  # all-day rozsah
        else:
            # b) "DD.MM.YYYY HH:MM - HH:MM" | "DD.MM.YYYY HH:MM" | "DD.MM.YYYY"
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
            continue

        description = clean_text(blob)
        url = LIST_URL

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
    return dt.strftime("%Y%m%dT%H%M%S")

def ics_escape(s: str) -> str:
    if not s:
        return ""
    # RFC5545: escapuje se \ ; , a newline
    return (
        s.replace("\\", "\\\\")
         .replace("\n", "\\n")
         .replace(";", "\\;")
         .replace(",", "\\,")
    )

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

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="Cesta k výstupnímu .ics souboru")
    args = ap.parse_args()

    listing = fetch(LIST_URL)
    events = parse_listing(listing)

    ics = build_ics(events)
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        f.write(ics)
    print(f"OK -> {args.out}  ({len(events)} událostí)")

if __name__ == "__main__":
    main()
