#!/usr/bin/env python3
"""
AMF · AGENT 1 — Franchissements de seuils (BDIF)
================================================
Collecte les déclarations de franchissement de seuils publiées sur la Base des
décisions et informations financières de l'AMF (https://bdif.amf-france.org).

Voie de données (API interne du front BDIF, découverte par rétro-ingénierie du
bundle Angular du site) :
    GET /back/api/v1/informations?TypesInformation=SPDE&DateDebut=...&DateFin=...&From=0&Size=...
        -> JSON { total, result:[ { numeroConcatene, dateInformation,
                                    typesInformation, typesDocument,
                                    documents:[{path,...}], societes:[{raisonSociale,role}] } ] }
    GET /back/api/v1/documents/{path}   -> le PDF de la déclaration

SPDE = « Seuils, pactes, dérogations et examens ». On ne garde que les documents
dont le PDF est effectivement une déclaration de franchissement de seuils
(détectée au parsing). Le PDF suit un gabarit AMF standard, parsé par regex :
  - déclarant (nom du détenteur)
  - sens du franchissement (hausse / baisse)
  - seuils franchis (5%, 10%, 1/3, …)
  - % de capital et de droits de vote détenus après l'opération
  - ISIN de l'émetteur (présent dans l'en-tête du document)
  - société / titre concerné (fourni par l'API)

Le texte brut extrait est renvoyé pour alimenter le résumé (amf_summary.py).

Usage CLI (test) :
    python amf_bdif.py --days 3
    python amf_bdif.py --date 2026-08-04
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import re
import sys
from typing import Optional

try:
    import requests
except ImportError:
    sys.exit("pip install requests")

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

BASE = "https://bdif.amf-france.org/back/api/v1"
UA = {"User-Agent": "Mozilla/5.0 (AMF-collector)"}
TIMEOUT = 30

# Mots (minuscules) des formes juridiques / nationalités qui précèdent le nom
# propre du déclarant — on les retire par la tête jusqu'au premier nom propre.
_LEAD_STOP = {
    "la", "le", "les", "société", "societe", "fonds", "groupe", "personne",
    "physique", "morale", "de", "du", "des", "d", "droit", "à", "a",
    "responsabilité", "responsabilite", "limitée", "limitee", "anonyme", "par",
    "actions", "simplifiée", "simplifiee", "en", "commandite", "gestion",
    "investissement", "sas", "sa", "sarl", "scr", "sca", "civile", "holding",
    "fiducie", "trust", "fund", "l", "l'", "au", "capital", "variable",
    # adjectifs de nationalité fréquents dans les descripteurs juridiques
    "français", "française", "americain", "américain", "américaine",
    "britannique", "luxembourgeois", "luxembourgeoise", "néerlandais",
    "néerlandaise", "neerlandais", "allemand", "allemande", "suisse",
    "italien", "italienne", "espagnol", "espagnole", "belge", "anglais",
    "anglaise", "irlandais", "irlandaise", "danois", "suédois", "suedois",
    "norvégien", "japonais", "chinois", "canadien", "canadienne", "portugais",
}


def _iso(d: dt.date) -> str:
    return d.strftime("%Y-%m-%d")


def search(date_debut: dt.date, date_fin: dt.date, size: int = 200,
           types_information: str = "SPDE") -> list[dict]:
    """Interroge l'API BDIF et renvoie les items bruts sur la fenêtre de dates."""
    params = {
        "TypesInformation": types_information,
        "DateDebut": _iso(date_debut),
        "DateFin": _iso(date_fin),
        "From": 0,
        "Size": size,
    }
    r = requests.get(f"{BASE}/informations", params=params, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    return data.get("result", []) or []


def pdf_url(item: dict) -> Optional[str]:
    docs = item.get("documents") or []
    for d in docs:
        path = d.get("path")
        if path and (d.get("format") == "PDF" or path.lower().endswith(".pdf")):
            return f"{BASE}/documents/{path}"
    return None


def issuer_name(item: dict) -> Optional[str]:
    for s in item.get("societes") or []:
        if s.get("role") == "SocieteConcernee" and s.get("raisonSociale"):
            return s["raisonSociale"]
    socs = item.get("societes") or []
    return socs[0]["raisonSociale"] if socs and socs[0].get("raisonSociale") else None


def extract_pdf_text(url: str) -> str:
    if fitz is None:
        raise RuntimeError("PyMuPDF (fitz) requis : pip install pymupdf")
    r = requests.get(url, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    doc = fitz.open(stream=r.content, filetype="pdf")
    try:
        return "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()


def _clean_declarant(raw: Optional[str]) -> Optional[str]:
    """Retire adresse entre parenthèses et descripteurs juridiques de tête."""
    if not raw:
        return None
    s = re.sub(r"\([^)]*\)", "", raw)          # adresse
    s = re.sub(r"\s+", " ", s).strip(" ,.")
    tokens = s.split(" ")
    # retire par la tête les mots de forme juridique (minuscules) jusqu'au nom propre
    while tokens:
        t = tokens[0].strip(".,'’").lower()
        if t and (t in _LEAD_STOP):
            tokens.pop(0)
        else:
            break
    name = " ".join(tokens).strip(" ,.")
    name = re.sub(r"([A-Za-z.])\d+$", r"\1", name)   # marqueur de note collé (S.a.r.l1)
    return name or None


_RE_SENS = re.compile(r"franchi\s+(?:individuellement\s+|de concert\s+)?en\s+(hausse|baisse)", re.I)
_RE_DATE = re.compile(r"franchi[^,]*?,\s*le\s+(\d{1,2}\s+[a-zà-ÿ]+\s+\d{4})", re.I)
_RE_ISIN = re.compile(r"\b([A-Z]{2}\d{10})\b")
_RE_SEUILS = re.compile(r"les\s+seuils?\s+de\s+(.{2,180}?)\s+du\s+capital", re.I)
_RE_DETENUS = re.compile(
    r"d[ée]tenir\b.{0,260}?soit\s+([\d.,]+)\s*%\s+du\s+capital\s+et\s+([\d.,]+)\s*%\s+des\s+droits\s+de\s+vote",
    re.I)
_RE_DECL = re.compile(
    r"((?:la\s+soci[ée]t[ée]|le\s+fonds|le\s+groupe|la\s+personne|M\.|Mme|Mlle)\b.{0,220}?)"
    r"\s+a\s+d[ée]clar[ée]\s+avoir\s+franchi", re.I)


def parse_franchissement(text: str) -> dict:
    """Extrait les champs structurés du texte d'une déclaration de franchissement."""
    flat = re.sub(r"\s+", " ", text or "")
    out: dict = {
        "is_franchissement": False,
        "declarant": None,
        "declarant_raw": None,
        "sens": None,
        "seuils": [],
        "pct_capital": None,
        "pct_droits_vote": None,
        "isin": None,
        "date_franchissement": None,
    }
    if not flat:
        return out

    m = _RE_ISIN.search(flat)
    if m:
        out["isin"] = m.group(1)

    m = _RE_SENS.search(flat)
    if m:
        out["is_franchissement"] = True
        out["sens"] = m.group(1).lower()

    m = _RE_DATE.search(flat)
    if m:
        out["date_franchissement"] = m.group(1)

    m = _RE_DECL.search(flat)
    if m:
        out["declarant_raw"] = m.group(1).strip()
        out["declarant"] = _clean_declarant(m.group(1))

    m = _RE_SEUILS.search(flat)
    if m:
        out["seuils"] = re.findall(r"\d+/\d+|\d+(?:,\d+)?\s*%", m.group(1))

    m = _RE_DETENUS.search(flat)
    if m:
        out["pct_capital"] = _to_float(m.group(1))
        out["pct_droits_vote"] = _to_float(m.group(2))

    # marqueur de franchissement même si le sens n'a pas matché
    if not out["is_franchissement"] and "franchi" in flat.lower() and "seuil" in flat.lower():
        out["is_franchissement"] = True
    return out


def _to_float(s: str) -> Optional[float]:
    try:
        return float(s.replace(",", ".").replace(" ", ""))
    except (ValueError, AttributeError):
        return None


def collect(date_debut: dt.date, date_fin: dt.date,
            max_docs: int = 60) -> list[dict]:
    """Renvoie les franchissements de seuils enrichis (structurés) de la fenêtre.

    Chaque enregistrement :
        { numero, date, issuer, isin, declarant, sens, seuils,
          pct_capital, pct_droits_vote, pdf_url, raw_text }
    """
    items = search(date_debut, date_fin)
    records: list[dict] = []
    for it in items[:max_docs]:
        url = pdf_url(it)
        issuer = issuer_name(it)
        rec = {
            "numero": it.get("numeroConcatene") or it.get("numero"),
            "date": (it.get("dateInformation") or "")[:10],
            "date_publication": (it.get("datePublication") or "")[:10],
            "issuer": issuer,
            "isin": None,
            "declarant": None,
            "sens": None,
            "seuils": [],
            "pct_capital": None,
            "pct_droits_vote": None,
            "date_franchissement": None,
            "pdf_url": url,
            "raw_text": "",
            "parse_ok": False,
        }
        if url:
            try:
                text = extract_pdf_text(url)
                parsed = parse_franchissement(text)
                rec.update({k: parsed[k] for k in
                            ("declarant", "sens", "seuils", "pct_capital",
                             "pct_droits_vote", "date_franchissement")})
                rec["isin"] = parsed.get("isin")
                rec["raw_text"] = text[:6000]
                rec["parse_ok"] = True
                rec["is_franchissement"] = parsed.get("is_franchissement", False)
            except Exception as e:  # noqa: BLE001 — best-effort, ne casse pas le lot
                rec["error"] = str(e)
        records.append(rec)
    # priorise les vrais franchissements en tête
    records.sort(key=lambda r: (not r.get("is_franchissement", False), r.get("date") or ""),
                 reverse=False)
    return records


def _cli():
    ap = argparse.ArgumentParser(description="Franchissements de seuils AMF (BDIF)")
    ap.add_argument("--days", type=int, default=2, help="fenêtre en jours (défaut 2)")
    ap.add_argument("--date", help="date unique YYYY-MM-DD (prioritaire sur --days)")
    ap.add_argument("--max", type=int, default=40)
    args = ap.parse_args()

    if args.date:
        d = dt.date.fromisoformat(args.date)
        deb, fin = d, d
    else:
        fin = dt.date.today()
        deb = fin - dt.timedelta(days=args.days - 1)

    recs = collect(deb, fin, max_docs=args.max)
    print(f"# {len(recs)} document(s) SPDE sur {deb}..{fin}\n", file=sys.stderr)
    for r in recs:
        flag = "✓" if r.get("is_franchissement") else "·"
        print(f"{flag} {r['numero']} {r['date']} | {r['issuer']} ({r['isin']}) | "
              f"{r['declarant']} | {r['sens']} | {', '.join(r['seuils'])} | "
              f"cap {r['pct_capital']}% dv {r['pct_droits_vote']}%")
    print("\n--- JSON (premier) ---", file=sys.stderr)
    if recs:
        r0 = dict(recs[0]); r0["raw_text"] = r0["raw_text"][:120] + "…"
        print(json.dumps(r0, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _cli()
