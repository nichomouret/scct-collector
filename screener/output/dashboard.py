#!/usr/bin/env python3
"""
Tableau de bord — short-list interactive (couche 4, §0 / §6.7 / §7.5).
=====================================================================
« Comprimer le temps de décision — de 3 heures d'instruction manuelle à 60
secondes de lecture d'une fiche pré-instruite. » Une carte-fiche par titre,
classée par route, avec l'essentiel sous les yeux : route(s), conviction, taille,
catalyseur, dislocation, confirmations, ce qui invalide, entrée PATH.

Interactif (JS inline, compatible Artifact) : filtres par route et par statut,
tri, cartes dépliables. « Rafraîchir » = régénérer via `run --dashboard`.

Réutilise le système de design de `backtest_report` (palette validée, thèmes).
"""
from __future__ import annotations

from typing import List

from .backtest_report import _CSS, _e
from . import dossier as _dossier
from ..engine import EvaluationResult
from ..models import LossBoundMechanism, Route

_ROUTE_NAME = {Route.A: "dislocation technique", Route.B: "sur-réaction",
               Route.C: "réfutation", Route.D: "binaire daté", Route.E: "décote + catalyseur"}
_ROUTE_SLOT = {Route.A: 1, Route.B: 2, Route.C: 3, Route.D: 4, Route.E: 5}

_DASH_CSS = """
.bt .bar{display:flex;flex-wrap:wrap;gap:10px 16px;align-items:center;
  padding:12px 16px;border:1px solid var(--border);border-radius:12px;
  background:var(--surface);margin-bottom:20px;position:sticky;top:8px;z-index:5;}
.bt .chips{display:flex;flex-wrap:wrap;gap:6px;}
.bt .chip{font-size:12.5px;font-weight:600;padding:5px 11px;border-radius:999px;cursor:pointer;
  border:1px solid var(--border);color:var(--ink2);background:transparent;user-select:none;}
.bt .chip[aria-pressed=true]{background:var(--ink);color:var(--plane);border-color:var(--ink);}
.bt .bar select{font:inherit;font-size:12.5px;padding:5px 8px;border-radius:8px;
  border:1px solid var(--border);background:var(--surface);color:var(--ink);}
.bt .count{margin-left:auto;font-size:12.5px;color:var(--muted);font-variant-numeric:tabular-nums;}
.bt .cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:14px;align-items:start;}
.bt .cand{background:var(--surface);border:1px solid var(--border);border-radius:14px;overflow:hidden;}
.bt .cand.hide{display:none;}
.bt .chead{display:flex;justify-content:space-between;gap:10px;align-items:flex-start;
  padding:14px 16px;cursor:pointer;}
.bt .chead .tk{font-size:19px;font-weight:700;letter-spacing:-.01em;}
.bt .chead .nm{font-size:11.5px;color:var(--muted);}
.bt .conv{text-align:right;} .bt .conv .n{font-size:22px;font-weight:700;font-variant-numeric:tabular-nums;line-height:1;}
.bt .conv .l{font-size:10px;color:var(--muted);letter-spacing:.05em;text-transform:uppercase;}
.bt .rbadges{display:flex;flex-wrap:wrap;gap:5px;padding:0 16px 10px;}
.bt .rb{font-size:11px;font-weight:700;padding:3px 8px;border-radius:6px;}
.rb.r1{background:color-mix(in srgb,var(--s1) 18%,transparent);color:var(--s1);}
.rb.r2{background:color-mix(in srgb,var(--s2) 20%,transparent);color:var(--s2);}
.rb.r3{background:color-mix(in srgb,var(--s3) 20%,transparent);color:var(--s3);}
.rb.r4{background:color-mix(in srgb,var(--s4) 24%,transparent);color:var(--s4);}
.rb.r5{background:color-mix(in srgb,var(--s5) 20%,transparent);color:var(--s5);}
.bt .status{font-size:10.5px;font-weight:700;padding:3px 8px;border-radius:6px;margin-left:auto;}
.status.adm{background:color-mix(in srgb,var(--good) 16%,transparent);color:var(--good);}
.status.wat{background:color-mix(in srgb,var(--warn) 22%,transparent);color:var(--warn);}
.bt .rows{padding:0 16px 14px;display:flex;flex-direction:column;gap:7px;font-size:12.5px;}
.bt .row{display:flex;gap:8px;}.bt .row .k{color:var(--muted);min-width:96px;flex:none;}
.bt .row .v{color:var(--ink);}.bt .row .v b{font-variant-numeric:tabular-nums;}
.bt .conf i{font-style:normal;margin-right:8px;white-space:nowrap;}
.bt .more{border-top:1px solid var(--grid);padding:12px 16px;display:none;font-size:12.5px;color:var(--ink2);}
.bt .cand.open .more{display:block;}
.bt .more h4{margin:0 0 4px;font-size:11px;letter-spacing:.05em;text-transform:uppercase;color:var(--muted);}
.bt .more ul{margin:0 0 10px;padding-left:16px;}.bt .more li{margin:2px 0;}
.bt .caret{color:var(--muted);font-size:12px;}
.bt .empty{padding:34px;text-align:center;color:var(--muted);border:1px dashed var(--border);border-radius:12px;}
"""


def _yn(ok: bool, label: str) -> str:
    return f'<i>{"✓" if ok else "✗"} {_e(label)}</i>'


def _card(res: EvaluationResult) -> str:
    c = res.candidate
    conv = min(res.conviction.conv, 10.0)
    routes = res.route_labels
    rstr = " ".join(r.value for r in routes) or "-"
    badges = "".join(
        f'<span class="rb r{_ROUTE_SLOT[r]}">{r.value} · {_e(_ROUTE_NAME[r])}</span>' for r in routes)
    status = ('<span class="status adm">ADMIS</span>' if res.admitted
              else '<span class="status wat">surveiller</span>')

    rows = []
    if c.catalyst:
        cat = c.catalyst
        em = f" · move ±{cat.expected_move_pct:.0f}%" if cat.expected_move_pct else ""
        up = ""
        if c.upside_thesis_pct and cat.expected_move_pct:
            r = c.upside_thesis_pct / cat.expected_move_pct
            up = f' · upside +{c.upside_thesis_pct:.0f}% ({"✓" if r>1.5 else "✗"} {r:.1f}×)'
        dtc = f" (J-{cat.days_to_catalyst})" if cat.days_to_catalyst is not None else ""
        rows.append(("Catalyseur", f'{_e(cat.catalyst_type)} · {_e(cat.date_expected)}{dtc}{em}{up}'))
    disloc = []
    if c.z_res:
        disloc.append(f"{c.z_res:+.1f}σ")
    if c.days_since_shock is not None:
        disloc.append(f"il y a {c.days_since_shock}j")
    disloc.append(f"DIS {c.dis:.1f}")
    if res.path.archetype:
        disloc.append(res.path.archetype.value)
    if res.path.stab is not None:
        disloc.append(f"STAB {res.path.stab}/10")
    rows.append(("Dislocation", " · ".join(disloc)))
    val = []
    if c.coussin is not None:
        val.append(f"coussin {c.coussin:+.1%}")
    if c.val_z is not None:
        val.append(f"VAL_z {c.val_z:+.1f}")
    mech = _dossier._MECH_LABEL.get(res.floor.mechanism, "")
    val.append(f"{mech} ({res.floor.size_factor:.1f}×)")
    rows.append(("Bornage", " · ".join(val)))
    if c.cause_class:
        res_str = f" · résol. ~{c.expected_resolution_days}j" if c.expected_resolution_days else ""
        rows.append(("Cause", f"{c.cause_class.value}{res_str}"))

    conf = []
    if c.aqs is not None:
        conf.append(_yn(c.aqs > 1.0, f"AQS {c.aqs:.1f}"))
    conf.append(_yn(c.insider_buy, "initié"))
    if c.pms is not None:
        conf.append(_yn(abs(c.pms) >= 0.20, f"PMS {c.pms:+.2f}"))
    if c.borrow_jump_bps_3d is not None:
        conf.append(_yn(c.borrow_jump_bps_3d >= 200, f"emprunt +{c.borrow_jump_bps_3d:.0f}bps"))
    if c.float_utilization is not None:
        conf.append(_yn(c.float_utilization >= 0.90, f"float {c.float_utilization:.0%}"))
    rows.append(("Confirm.", '<span class="conf">' + "".join(conf) + "</span>"))

    if c.short_interest_pct is not None or c.days_of_adv is not None:
        pos = []
        if c.short_interest_pct is not None:
            pos.append(f"SI {c.short_interest_pct:.1%}")
        if c.borrow_fee is not None:
            pos.append(f"emprunt {c.borrow_fee:.1%}")
        if c.days_of_adv is not None:
            pos.append(f"{c.days_of_adv:.1f} j d'ADV")
        rows.append(("Position", " · ".join(pos)))

    rowhtml = "".join(f'<div class="row"><span class="k">{k}</span><span class="v">{v}</span></div>'
                      for k, v in rows)

    invs = "".join(f"<li>{_e(x)}</li>" for x in _dossier._invalidations(res))
    analysis = f'<h4>Analyse (Claude)</h4><p>{_e(c.analysis)}</p>' if c.analysis else ""
    entry = ""
    if res.path.entry_plan:
        p = res.path.entry_plan
        entry = (f'<h4>Entrée (PATH)</h4><p>{"Tranche 1 (50%) immédiate" if p.tranche1_ok else "Bloquée : "+_e(p.blocked_reason)}'
                 f'{" · tranche 2 armée (STAB≥6)" if p.tranche2_armed else ""}</p>')

    cap = f"{c.market_cap/1e6:.0f} M" if c.market_cap else ""
    horizon = c.expected_resolution_days if c.expected_resolution_days is not None else 999
    disv = round(c.dis, 2)
    return f"""<article class="cand" data-routes="{_e(rstr)}" data-admitted="{1 if res.admitted else 0}"
  data-conv="{conv:.2f}" data-disloc="{disv}" data-horizon="{horizon}">
<div class="chead">
  <div><div class="tk">{_e(c.ticker)}</div><div class="nm">{_e(c.name)} · {_e(c.sector)} · {_e(cap)} · {_e(c.place)}</div></div>
  <div class="conv"><div class="n">{conv:.1f}</div><div class="l">conviction</div></div>
</div>
<div class="rbadges">{badges}<span style="font-size:11px;color:var(--muted);align-self:center">n={res.n_routes} ×{res.conviction.multiplier:.2f} · taille {res.sizing.final_size:.2f}×</span>{status}</div>
<div class="rows">{rowhtml}<div class="row"><span class="k"></span><span class="v caret">▾ détails · invalidation</span></div></div>
<div class="more">{analysis}{entry}<h4>Ce qui invalide</h4><ul>{invs}</ul></div>
</article>"""


def render_html(results: List[EvaluationResult], as_of: str = "",
                universe: str = "", standalone: bool = True) -> str:
    ordered = sorted(results, key=lambda r: (r.admitted, r.conviction.conv), reverse=True)
    n_adm = sum(1 for r in ordered if r.admitted)
    cards = "".join(_card(r) for r in ordered) or \
        '<div class="empty">Aucun candidat aujourd\'hui. Régénère avec les couches de données branchées (voir .env.example).</div>'
    chips = ('<button class="chip" data-f="all" aria-pressed="true">Tous</button>'
             '<button class="chip" data-f="adm">Admis</button>'
             + "".join(f'<button class="chip" data-f="{r.value}">{r.value}</button>' for r in Route))

    body = f"""<div class="bt"><div class="bt-inner">
<h1>Short-list du jour</h1>
<p class="sub">{len(ordered)} candidats · {n_adm} admis · au {_e(as_of)}{(' · '+_e(universe)) if universe else ''} — clique une carte pour déplier l'analyse et l'invalidation.</p>
<div class="bar"><div class="chips">{chips}</div>
<label>Tri <select id="sort"><option value="conv">conviction</option><option value="disloc">dislocation</option><option value="horizon">horizon</option></select></label>
<span class="count" id="count"></span></div>
<div class="cards" id="cards">{cards}</div>
</div></div>
<script>
(function(){{
 var cards=document.getElementById('cards'), count=document.getElementById('count');
 var filter='all';
 function apply(){{
   var vis=0, arr=[].slice.call(cards.children);
   arr.forEach(function(c){{
     if(!c.dataset) return;
     var routes=(c.dataset.routes||'').split(' ');
     var ok = filter==='all' || (filter==='adm' ? c.dataset.admitted==='1' : routes.indexOf(filter)>=0);
     c.classList.toggle('hide', !ok); if(ok) vis++;
   }});
   count.textContent=vis+' affiché'+(vis>1?'s':'');
 }}
 [].forEach.call(document.querySelectorAll('.chip'),function(b){{
   b.addEventListener('click',function(){{
     document.querySelectorAll('.chip').forEach(function(x){{x.setAttribute('aria-pressed','false');}});
     b.setAttribute('aria-pressed','true'); filter=b.dataset.f; apply();
   }});
 }});
 document.getElementById('sort').addEventListener('change',function(e){{
   var key=e.target.value, arr=[].slice.call(cards.children).filter(function(c){{return c.dataset;}});
   arr.sort(function(a,b){{
     if(key==='horizon') return (+a.dataset.horizon)-(+b.dataset.horizon);
     return (+b.dataset[key])-(+a.dataset[key]);
   }});
   arr.forEach(function(c){{cards.appendChild(c);}});
 }});
 cards.addEventListener('click',function(e){{
   var art=e.target.closest('.cand'); if(art && art.dataset) art.classList.toggle('open');
 }});
 apply();
}})();
</script>"""

    css = _CSS + _DASH_CSS
    if not standalone:
        return f"<style>{css}</style>\n{body}"
    return (f'<!doctype html><html lang="fr"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>Short-list du jour</title><style>*{{margin:0}}{css}</style></head>'
            f'<body>{body}</body></html>')
