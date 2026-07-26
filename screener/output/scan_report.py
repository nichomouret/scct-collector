#!/usr/bin/env python3
"""
Rapport HTML — short-list du jour (scanner de dislocations).
============================================================
Rend les candidats du jour en cartes : ticker, ampleur du décrochage, archétype,
STAB, stop structurel et recommandation d'entrée PATH. Réutilise le système de
design de `backtest_report` (palette validée, thème clair/sombre, SVG/CSS inline).
"""
from __future__ import annotations

from typing import List

from .backtest_report import _CSS, _e

_CARD_CSS = """
.bt .intro{padding:16px 18px;border-radius:12px;border:1px solid var(--border);
  background:var(--surface);margin-bottom:24px;color:var(--ink2);font-size:14px;}
.bt .cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px;}
.bt .cand{background:var(--surface);border:1px solid var(--border);border-radius:14px;
  padding:16px 18px;display:flex;flex-direction:column;gap:12px;}
.bt .cand .top{display:flex;justify-content:space-between;align-items:baseline;gap:10px;}
.bt .cand .tk{font-size:21px;font-weight:700;letter-spacing:-.01em;}
.bt .cand .nm{font-size:12px;color:var(--muted);}
.bt .cand .px{font-size:16px;font-weight:600;font-variant-numeric:tabular-nums;}
.bt .badge{display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:600;
  padding:3px 10px;border-radius:999px;width:fit-content;}
.badge.go{background:color-mix(in srgb,var(--good) 16%,transparent);color:var(--good);}
.badge.wait{background:color-mix(in srgb,var(--warn) 22%,transparent);color:var(--warn);}
.bt .metrics{display:grid;grid-template-columns:1fr 1fr;gap:8px 14px;font-size:13px;}
.bt .metrics .m{display:flex;justify-content:space-between;border-bottom:1px solid var(--grid);padding-bottom:4px;}
.bt .metrics .k{color:var(--muted);} .bt .metrics .v{font-variant-numeric:tabular-nums;font-weight:600;}
.bt .stop{font-size:12.5px;color:var(--ink2);}
.bt .empty{padding:30px;text-align:center;color:var(--muted);}
.bt .plan{display:grid;grid-template-columns:repeat(3,1fr);border:1px solid var(--grid);
  border-radius:10px;overflow:hidden;}
.bt .plan > div{padding:7px 8px;text-align:center;border-right:1px solid var(--grid);}
.bt .plan > div:last-child{border-right:0;}
.bt .plan .lb{font-size:9px;letter-spacing:.05em;text-transform:uppercase;color:var(--muted);}
.bt .plan .pr{font-size:15px;font-weight:700;font-variant-numeric:tabular-nums;}
.bt .plan .pc{font-size:10.5px;font-variant-numeric:tabular-nums;}
.bt .plan .stop .pr,.bt .plan .stop .pc{color:var(--crit);}
.bt .plan .tgt .pr,.bt .plan .tgt .pc{color:var(--good);}
"""

_ARCH_FR = {
    "GAP_AND_FLAT": "gap puis range plat", "CAPITULATION_V": "capitulation en V",
    "GRINDING_DECLINE": "baisse continue", "STAIRCASE_DOWN": "distribution en escalier",
    "FAILED_BOUNCE": "rebond avorté",
}


def _card(c: dict) -> str:
    blocked = c.get("blocked")
    badge = ('<span class="badge wait">⏳ Attendre stabilisation</span>' if blocked
             else '<span class="badge go">✓ Entrée tranche 1 (50%)</span>')
    arch = _ARCH_FR.get(c.get("archetype", ""), c.get("archetype", ""))
    stop = ""
    if c.get("stop_level") is not None and c.get("stop_pct") is not None:
        stop = (f'<div class="stop">Stop structurel : sous <strong>{c["stop_level"]:.2f}</strong> '
                f'(−{c["stop_pct"]*100:.1f}% du cours)</div>')
    plan = ""
    if c.get("target_2r") is not None:
        px = c.get("price", 0)
        t2, t3 = c["target_2r"], c.get("target_3r")
        t2p = (t2 - px) / px if px else 0
        t3s = (f'<div class="tgt"><div class="lb">Objectif 3R</div>'
               f'<div class="pr">{t3:.2f}</div><div class="pc">+{((t3-px)/px)*100:.0f}%</div></div>'
               if t3 else '<div></div>')
        plan = (f'<div class="plan">'
                f'<div class="stop"><div class="lb">Stop</div><div class="pr">{c["stop_level"]:.2f}</div>'
                f'<div class="pc">−{c["stop_pct"]*100:.1f}%</div></div>'
                f'<div class="tgt"><div class="lb">Objectif 2R</div><div class="pr">{t2:.2f}</div>'
                f'<div class="pc">+{t2p*100:.0f}%</div></div>'
                f'{t3s}</div>')
    return f"""<div class="cand">
<div class="top"><div><div class="tk">{_e(c.get("ticker"))}</div>
<div class="nm">{_e((c.get("name") or "")[:34])}</div></div>
<div class="px">{c.get("price",0):.2f}</div></div>
{badge}
<div class="metrics">
<div class="m"><span class="k">Choc</span><span class="v">J-{_e(c.get("days_since_shock"))}</span></div>
<div class="m"><span class="k">Résidu</span><span class="v">{c.get("z_res",0):+.1f}σ</span></div>
<div class="m"><span class="k">DIS</span><span class="v">{c.get("dis",0):.1f}</span></div>
<div class="m"><span class="k">STAB</span><span class="v">{c.get("stab",0)}/9</span></div>
<div class="m"><span class="k">Volume</span><span class="v">{c.get("z_volume",0):+.1f}σ</span></div>
<div class="m"><span class="k">Archétype</span><span class="v" style="font-weight:500">{_e(arch)}</span></div>
</div>
{plan}
{stop}
</div>"""


def render_html(result: dict, standalone: bool = True) -> str:
    cands: List[dict] = result.get("candidates", [])
    n_scan = result.get("n_scanned", 0)
    asof = result.get("as_of", "")
    dmin = result.get("config", {}).get("dis_min", "?")

    if cands:
        grid = '<div class="cards">' + "".join(_card(c) for c in cands) + "</div>"
    else:
        grid = ('<div class="cand empty">Aucune dislocation fraîche aujourd\'hui.<br>'
                'C\'est normal et sain : le gisement est mince (~60-90 setups/an, §6.2).</div>')

    body = f"""<div class="bt"><div class="bt-inner">
<h1>Signaux du jour — dislocations fraîches</h1>
<p class="sub">{len(cands)} candidat(s) · {n_scan} titres scannés · au {_e(asof)} · seuil DIS ≥ {_e(dmin)}</p>
<div class="intro">
<strong>Ce sont des candidats, pas des ordres.</strong> Le scanner repère la
dislocation technique (couche 2, §4) — le <em>filtre d'entrée</em> — et pré-calcule
le <strong>stop structurel</strong> et des <strong>objectifs 2R/3R</strong> (cadre
risque/rendement, pas une thèse de valorisation). Les titres <em>entrables</em>
(tranche 1 possible) sont classés en tête ; les <em>vendeurs actifs</em> restent à
attendre. Avant de trader, confirme une <strong>raison</strong> (catalyseur, réfutation,
décote, news) : remplis le gabarit d'overlay (<code>scan --emit-overlay</code>) sur le
titre qui t'intéresse, puis <code>run --overlay</code> pour obtenir le dossier admis.
</div>
{grid}
</div></div>"""

    css = _CSS + _CARD_CSS
    if not standalone:
        return f"<style>{css}</style>\n{body}"
    return (f'<!doctype html><html lang="fr"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>Signaux du jour — dislocations</title><style>*{{margin:0}}'
            f'{css}</style></head><body>{body}</body></html>')
