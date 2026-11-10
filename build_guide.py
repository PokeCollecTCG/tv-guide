#!/usr/bin/env python3
"""Construit guide.json : ce qui passe sur les chaînes françaises (gratuites et payantes) pour les prochaines 36 heures.

Source : le flux XMLTV public de xmltvfr.fr (projet XML-TV-Fr, https://github.com/racacax/XML-TV-Fr). Le fichier d'origine pèse
des dizaines de Mo : ce script le lit au fil de l'eau et ne garde que les chaînes ci-dessous, pour un petit JSON que la TV lit
directement.

    python3 build_guide.py                              # télécharge le flux et écrit guide.json
    python3 build_guide.py --input guide.xml            # lit un fichier local (essais)
"""
import argparse
import gzip
import io
import json
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

FEEDS = [
    "https://xmltvfr.fr/xmltv/xmltv_fr.xml",
    "https://xmltvfr.fr/xmltv/xmltv_fr.xml.gz",
    "https://xmltvfr.fr/xmltv/xmltv.xml.gz",
]
HOURS_AHEAD = 36

# (identifiant XMLTV, nom affiché). Gratuites : TNT, dans l'ordre de la télécommande. 24 chaînes = 3 pages de 8 sur la TV.
FREE = [
    ("TF1.fr", "TF1"), ("France2.fr", "France 2"), ("France3.fr", "France 3"), ("France5.fr", "France 5"),
    ("M6.fr", "M6"), ("Arte.fr", "Arte"), ("W9.fr", "W9"), ("TMC.fr", "TMC"),
    ("NT1.fr", "TFX"), ("LaChaineParlementaire.fr", "LCP"), ("France4.fr", "France 4"), ("BFMTV.fr", "BFM TV"),
    ("CNews.fr", "CNews"), ("CStar.fr", "CStar"), ("Gulli.fr", "Gulli"), ("NOVO19.fr", "NOVO19"),
    ("TF1SeriesFilms.fr", "TF1 Séries Films"), ("LEquipe21.fr", "L'Équipe"), ("6ter.fr", "6ter"), ("Numero23.fr", "RMC Story"),
    ("RMCDecouverte.fr", "RMC Découverte"), ("LCI.fr", "LCI"), ("FranceInfo.fr", "franceinfo"), ("Cherie25.fr", "RMC Life"),
]
# Payantes : Canal+, bouquets sport et cinéma. 16 chaînes = 2 pages de 8 sur la TV.
PAID = [
    ("CanalPlus.fr", "Canal+"), ("CanalPlusSport.fr", "Canal+ Sport"), ("CanalPlusCinema.fr", "Canal+ Cinéma"),
    ("CanalPlusSeries.fr", "Canal+ Séries"), ("CanalPlusFoot.fr", "Canal+ Foot"), ("beINSPORTS1.fr", "beIN SPORTS 1"),
    ("beINSPORTS2.fr", "beIN SPORTS 2"), ("beINSPORTS3.fr", "beIN SPORTS 3"), ("RMCSport1.fr", "RMC Sport 1"),
    ("RMCSport2.fr", "RMC Sport 2"), ("Eurosport1.fr", "Eurosport 1"), ("CinePlusPremier.fr", "Ciné+ Premier"),
    ("ParisPremiere.fr", "Paris Première"), ("PlanetePlus.fr", "Planète+"), ("CanalPlusDocs.fr", "Canal+ Docs"),
    ("CanalPlusGrandEcran.fr", "Canal+ Grand Écran"),
]


def when(value):
    """XMLTV « 20261104205500 +0100 » → millisecondes depuis 1970 (UTC), ou None."""
    try:
        value = value.strip()
        dt = datetime.strptime(value[:14], "%Y%m%d%H%M%S")
        off = value[14:].strip()
        tz = timezone.utc
        if len(off) >= 5 and off[0] in "+-":
            delta = timedelta(hours=int(off[1:3]), minutes=int(off[3:5]))
            tz = timezone(delta if off[0] == "+" else -delta)
        return int(dt.replace(tzinfo=tz).timestamp() * 1000)
    except (ValueError, TypeError):
        return None


def open_feed(path):
    """Un flux ouvert (décompressé si besoin) : un fichier local, ou la première adresse qui répond."""
    if path:
        raw = open(path, "rb")
    else:
        raw, last = None, None
        for url in FEEDS:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "free-my-tv-guide/1.0", "Accept-Encoding": "gzip"})
                raw = urllib.request.urlopen(req, timeout=180)
                print("flux :", url, file=sys.stderr)
                break
            except Exception as e:   # noqa: BLE001 - on essaie l'adresse suivante
                last = e
                print("échec :", url, e, file=sys.stderr)
        if raw is None:
            sys.exit(f"Aucun flux XMLTV n'a répondu (dernière erreur : {last}).")
    head = raw.read(2)
    stream = io.BufferedReader(_Chain(head, raw))
    if head == b"\x1f\x8b":
        return gzip.GzipFile(fileobj=stream)
    return stream


class _Chain(io.RawIOBase):
    """Remet en tête les octets déjà lus pour reconnaître le gzip."""
    def __init__(self, head, rest):
        self.head, self.rest = head, rest

    def readable(self):
        return True

    def readinto(self, b):
        if self.head:
            n = min(len(b), len(self.head))
            b[:n] = self.head[:n]
            self.head = self.head[n:]
            return n
        data = self.rest.read(len(b))
        b[:len(data)] = data
        return len(data)


def text(el, tag):
    for child in el.findall(tag):
        if (child.text or "").strip():
            return child.text.strip()
    return ""


def build(stream, now_ms):
    wanted = {cid: (name, False) for cid, name in FREE}
    wanted.update({cid: (name, True) for cid, name in PAID})
    names, icons, progs = {}, {}, {cid: [] for cid in wanted}
    low, high = now_ms - 30 * 60000, now_ms + HOURS_AHEAD * 3600000
    for _, el in ET.iterparse(stream, events=("end",)):
        if el.tag == "channel":
            cid = el.get("id")
            if cid in wanted:
                names[cid] = text(el, "display-name")
                icon = el.find("icon")
                if icon is not None and icon.get("src"):
                    icons[cid] = icon.get("src")
            el.clear()
        elif el.tag == "programme":
            cid = el.get("channel")
            if cid in wanted:
                start, stop = when(el.get("start")), when(el.get("stop"))
                title = text(el, "title")
                if start and stop and stop > start and stop >= low and start <= high and title:
                    progs[cid].append([start, stop, title, text(el, "sub-title")])
            el.clear()
    out = []
    for cid, (name, paid) in wanted.items():
        items = sorted(progs[cid])[:48]
        if items:
            out.append({"id": cid, "name": name, "icon": icons.get(cid, ""), "paid": paid, "programs": items})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", help="fichier XMLTV local (au lieu du téléchargement)")
    ap.add_argument("--output", default="guide.json")
    args = ap.parse_args()
    now_ms = int(time.time() * 1000)
    channels = build(open_feed(args.input), now_ms)
    if len(channels) < 5:
        sys.exit(f"Seulement {len(channels)} chaînes trouvées : le flux a sans doute changé, rien n'est écrit.")
    payload = {"generated": now_ms, "channels": channels}
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    size = sum(len(c["programs"]) for c in channels)
    print(f"{len(channels)} chaînes, {size} programmes -> {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
