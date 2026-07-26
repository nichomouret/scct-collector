#!/usr/bin/env python3
"""
Rapport HTML de backtest — data-viz autonome (§10).
===================================================
Rend les résultats du backtest en une page HTML autonome (CSS/SVG inline, aucune
dépendance externe) : bandeau de verdict, tuiles d'acceptation §10.9 (avec pastille
d'état ✓/✗), courbe d'équité, distribution des rendements, motifs de sortie,
décomposition par route, comparatif placebo, garde-fous.

Palette catégorielle validée (colorblind-safe) et neutres issus du skill dataviz.
Thème clair/sombre via tokens CSS (media query + data-theme). Les graphiques sont
du SVG inline généré ici — les `<title>` fournissent des info-bulles natives.

`render_html(result, standalone=True)` : document complet (fichier local ouvrable).
`standalone=False` : contenu seul (pour publication en Artifact, wrappé au publish).
"""
from __future__ import annotations

import html
from typing import Dict, List

# --- Palette catégorielle validée (slots 1-5), diverging & neutres (dataviz) ---
_CSS = """
:root .bt {
  color-scheme: light;
  --plane:#f9f9f7; --surface:#fcfcfb; --ink:#0b0b0b; --ink2:#52514e; --muted:#898781;
  --grid:#e1e0d9; --axis:#c3c2b7; --border:rgba(11,11,11,.10);
  --s1:#2a78d6; --s2:#eb6834; --s3:#1baf7a; --s4:#eda100; --s5:#e87ba4;
  --pos:#2a78d6; --neg:#e34948;
  --good:#0ca30c; --warn:#fab219; --crit:#d03b3b;
}
@media (prefers-color-scheme:dark){ :root:where(:not([data-theme=light])) .bt{
  color-scheme:dark;
  --plane:#0d0d0d; --surface:#1a1a19; --ink:#fff; --ink2:#c3c2b7; --muted:#898781;
  --grid:#2c2c2a; --axis:#383835; --border:rgba(255,255,255,.10);
  --s1:#3987e5; --s2:#d95926; --s3:#199e70; --s4:#c98500; --s5:#d55181;
  --pos:#3987e5; --neg:#e66767;
}}
:root[data-theme=dark] .bt{
  color-scheme:dark;
  --plane:#0d0d0d; --surface:#1a1a19; --ink:#fff; --ink2:#c3c2b7; --muted:#898781;
  --grid:#2c2c2a; --axis:#383835; --border:rgba(255,255,255,.10);
  --s1:#3987e5; --s2:#d95926; --s3:#199e70; --s4:#c98500; --s5:#d55181;
  --pos:#3987e5; --neg:#e66767;
}
.bt{background:var(--plane);color:var(--ink);
  font-family:system-ui,-apple-system,"Segoe UI",sans-serif;line-height:1.5;
  min-height:100vh;padding:clamp(16px,4vw,40px);}
.bt-inner{max-width:1040px;margin:0 auto;}
.bt *{box-sizing:border-box;}
.bt h1{font-size:clamp(22px,3.4vw,30px);margin:0 0 4px;letter-spacing:-.01em;text-wrap:balance;}
.bt .sub{color:var(--ink2);margin:0 0 20px;font-size:15px;}
.bt .verdict{display:flex;gap:14px;align-items:flex-start;padding:16px 18px;border-radius:12px;
  border:1px solid var(--border);background:var(--surface);margin-bottom:24px;}
.bt .verdict .tag{font-weight:700;font-size:13px;letter-spacing:.04em;text-transform:uppercase;
  padding:4px 10px;border-radius:999px;white-space:nowrap;}
.bt .verdict p{margin:0;color:var(--ink2);font-size:14.5px;}
.bt .verdict strong{color:var(--ink);}
.tag.no{background:color-mix(in srgb,var(--crit) 16%,transparent);color:var(--crit);}
.tag.go{background:color-mix(in srgb,var(--good) 18%,transparent);color:var(--good);}
.bt .kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:28px;}
.bt .kpi{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:14px 15px;}
.bt .kpi .lab{font-size:11.5px;letter-spacing:.05em;text-transform:uppercase;color:var(--muted);}
.bt .kpi .val{font-size:26px;font-weight:650;margin:4px 0 8px;font-variant-numeric:tabular-nums;}
.bt .pill{display:inline-flex;align-items:center;gap:5px;font-size:12px;font-weight:600;
  padding:2px 9px;border-radius:999px;}
.pill.ok{background:color-mix(in srgb,var(--good) 16%,transparent);color:var(--good);}
.pill.no{background:color-mix(in srgb,var(--crit) 16%,transparent);color:var(--crit);}
.bt section{margin-bottom:30px;}
.bt h2{font-size:16px;margin:0 0 12px;letter-spacing:.01em;}
.bt .card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:18px;}
.bt .grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px;}
@media (max-width:720px){.bt .grid2{grid-template-columns:1fr;}}
.bt svg{width:100%;height:auto;display:block;}
.bt .cap{font-size:12.5px;color:var(--muted);margin-top:8px;}
.bt table{width:100%;border-collapse:collapse;font-size:13.5px;font-variant-numeric:tabular-nums;}
.bt th,.bt td{text-align:right;padding:8px 10px;border-bottom:1px solid var(--grid);}
.bt th:first-child,.bt td:first-child{text-align:left;}
.bt thead th{color:var(--muted);font-weight:600;font-size:11.5px;letter-spacing:.04em;text-transform:uppercase;}
.bt .legend{display:flex;flex-wrap:wrap;gap:12px;font-size:12.5px;color:var(--ink2);margin-top:10px;}
.bt .legend i{width:11px;height:11px;border-radius:3px;display:inline-block;margin-right:5px;vertical-align:-1px;}
.bt footer{border-top:1px solid var(--grid);padding-top:16px;color:var(--muted);font-size:12.5px;}
.bt footer ul{margin:8px 0 0;padding-left:18px;} .bt footer li{margin:3px 0;}
"""

_SLOTS = ["var(--s1)", "var(--s2)", "var(--s3)", "var(--s4)", "var(--s5)"]


def _e(x) -> str:
    return html.escape(str(x))


def _fmt_pct(x, d=1):
    return f"{x*100:.{d}f}%"


# --------------------------------------------------------------------------- #
# Graphiques SVG                                                               #
# --------------------------------------------------------------------------- #
def _svg_equity(trades: List[dict]) -> str:
    ordered = sorted(trades, key=lambda t: t.get("entry_date", ""))
    eq, cur = [1.0], 1.0
    for t in ordered:
        cur *= (1.0 + float(t.get("net_return", 0.0)))
        eq.append(cur)
    if len(eq) < 2:
        return '<p class="cap">Pas assez de trades pour une courbe.</p>'
    W, H, pad = 720, 240, 34
    lo, hi = min(eq), max(eq)
    span = (hi - lo) or 1.0
    pw, ph = W - 2 * pad, H - 2 * pad
    xs = [pad + i / (len(eq) - 1) * pw for i in range(len(eq))]
    ys = [pad + (1 - (v - lo) / span) * ph for v in eq]
    line = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(zip(xs, ys)))
    area = f"M{xs[0]:.1f},{pad+ph:.1f} " + " ".join(f"L{x:.1f},{y:.1f}" for x, y in zip(xs, ys)) + f" L{xs[-1]:.1f},{pad+ph:.1f} Z"
    # ligne de capital initial (équité = 1)
    y1 = pad + (1 - (1.0 - lo) / span) * ph
    grid = (f'<line x1="{pad}" y1="{y1:.1f}" x2="{W-pad}" y2="{y1:.1f}" stroke="var(--axis)" '
            f'stroke-dasharray="3 3" stroke-width="1"/>'
            f'<text x="{W-pad}" y="{y1-5:.1f}" text-anchor="end" font-size="10.5" fill="var(--muted)">capital initial</text>')
    end = (f'<circle cx="{xs[-1]:.1f}" cy="{ys[-1]:.1f}" r="4" fill="var(--s1)">'
           f'<title>Équité finale ×{eq[-1]:.2f}</title></circle>')
    return (f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="Courbe d\'équité">'
            f'<text x="{pad}" y="{pad-12}" font-size="11" fill="var(--muted)">×{hi:.2f}</text>'
            f'<text x="{pad}" y="{H-pad+16}" font-size="11" fill="var(--muted)">×{lo:.2f} · {len(eq)-1} trades</text>'
            f'{grid}<path d="{area}" fill="var(--s1)" opacity="0.12"/>'
            f'<path d="{line}" fill="none" stroke="var(--s1)" stroke-width="2" '
            f'stroke-linejoin="round" stroke-linecap="round"/>{end}</svg>')


def _svg_exit_bars(reasons: Dict[str, int]) -> str:
    items = sorted(reasons.items(), key=lambda kv: -kv[1])
    if not items:
        return ""
    total = sum(v for _, v in items) or 1
    W, rowh, pad = 720, 30, 8
    H = pad * 2 + rowh * len(items)
    lw = 150
    bw = W - lw - 70
    mx = max(v for _, v in items) or 1
    rows = []
    for i, (name, v) in enumerate(items):
        y = pad + i * rowh
        w = bw * v / mx
        col = _SLOTS[i % len(_SLOTS)]
        rows.append(
            f'<text x="{lw-10}" y="{y+rowh/2+4:.0f}" text-anchor="end" font-size="12.5" fill="var(--ink2)">{_e(name)}</text>'
            f'<rect x="{lw}" y="{y+5:.0f}" width="{max(w,2):.1f}" height="{rowh-12}" rx="3" fill="{col}">'
            f'<title>{_e(name)} : {v} ({v/total:.0%})</title></rect>'
            f'<text x="{lw+w+8:.1f}" y="{y+rowh/2+4:.0f}" font-size="12.5" font-weight="600" '
            f'fill="var(--ink)">{v}</text>')
    return f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="Motifs de sortie">{"".join(rows)}</svg>'


def _svg_hist(trades: List[dict]) -> str:
    rets = sorted(float(t.get("net_return", 0.0)) for t in trades)
    if len(rets) < 2:
        return ""
    lo, hi = min(rets), max(rets)
    span = (hi - lo) or 1.0
    nb = 11
    bins = [0] * nb
    for r in rets:
        k = min(nb - 1, int((r - lo) / span * nb))
        bins[k] += 1
    W, H, pad = 720, 220, 30
    pw, ph = W - 2 * pad, H - 2 * pad
    bw = pw / nb
    mx = max(bins) or 1
    x0 = pad + (0 - lo) / span * pw       # position de 0
    bars = []
    for k, c in enumerate(bins):
        binlo = lo + k / nb * span
        col = "var(--neg)" if binlo < 0 else "var(--pos)"
        h = (c / mx) * ph
        x = pad + k * bw
        y = pad + ph - h
        lab = f"{binlo:+.0%}"
        bars.append(f'<rect x="{x+1:.1f}" y="{y:.1f}" width="{bw-2:.1f}" height="{h:.1f}" rx="2" fill="{col}">'
                    f'<title>{lab} : {c} trades</title></rect>')
    zero = (f'<line x1="{x0:.1f}" y1="{pad}" x2="{x0:.1f}" y2="{pad+ph}" stroke="var(--axis)" stroke-width="1.5"/>'
            f'<text x="{x0:.1f}" y="{H-8}" text-anchor="middle" font-size="10.5" fill="var(--muted)">0%</text>')
    return (f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="Distribution des rendements nets">'
            f'{"".join(bars)}{zero}'
            f'<text x="{pad}" y="{H-8}" font-size="10.5" fill="var(--muted)">{lo:+.0%}</text>'
            f'<text x="{W-pad}" y="{H-8}" text-anchor="end" font-size="10.5" fill="var(--muted)">{hi:+.0%}</text></svg>')


# --------------------------------------------------------------------------- #
# Sections                                                                     #
# --------------------------------------------------------------------------- #
_LABELS = {
    "hit_rate>50%": "Hit rate", "gain/perte>1.8": "Gain / perte",
    "sharpe_net>1.0": "Sharpe net", "drawdown<20%": "Drawdown max",
}


def _kpis(m: dict) -> str:
    acc = m["acceptance"]
    tiles = [
        ("Trades", f"{m['n']}", acc.get(f"trades>={_trades_min(acc)}")),
        ("Hit rate", _fmt_pct(m["hit_rate"]), acc["hit_rate>50%"]),
        ("Gain / perte", f"{m['gain_loss_ratio']:.2f}", acc["gain/perte>1.8"]),
        ("Sharpe (annuel)", f"{m['sharpe_annual']:.2f}", acc["sharpe_net>1.0"]),
        ("Drawdown max", _fmt_pct(m["max_drawdown"]), acc["drawdown<20%"]),
        ("Rendement moyen", f"{m['mean_return']*100:+.2f}%", None),
    ]
    out = []
    for lab, val, ok in tiles:
        pill = ""
        if ok is True:
            pill = '<span class="pill ok">✓ §10.9</span>'
        elif ok is False:
            pill = '<span class="pill no">✗ §10.9</span>'
        out.append(f'<div class="kpi"><div class="lab">{_e(lab)}</div>'
                   f'<div class="val">{_e(val)}</div>{pill}</div>')
    return '<div class="kpis">' + "".join(out) + "</div>"


def _trades_min(acc: dict) -> int:
    for k in acc:
        if k.startswith("trades>="):
            return int(k.split(">=")[1])
    return 150


def _routes_table(routes: Dict[str, dict], min_route: int) -> str:
    rows = []
    for name, m in routes.items():
        ok = m["n"] >= min_route
        state = ('<span class="pill ok">validable</span>' if ok
                 else '<span class="pill no">désactivée</span>')
        rows.append(f'<tr><td>{_e(name)} {state}</td><td>{m["n"]}</td>'
                    f'<td>{_fmt_pct(m["hit_rate"],0)}</td><td>{m["gain_loss_ratio"]:.2f}</td>'
                    f'<td>{m["sharpe_annual"]:.2f}</td><td>{_fmt_pct(m["max_drawdown"],0)}</td></tr>')
    return ('<table><thead><tr><th>Route</th><th>Trades</th><th>Hit</th>'
            '<th>Gain/perte</th><th>Sharpe</th><th>DD</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table>')


def render_html(result: dict, standalone: bool = True) -> str:
    m = result["metrics"]
    passed = all(m["acceptance"].values())
    beats = result.get("placebo_sharpe", 0.0) < m["sharpe_annual"]
    min_route = result.get("min_route_trades", 40)

    tag = ('<span class="tag go">Go</span>' if passed
           else '<span class="tag no">No-go — route A isolée</span>')
    verdict_txt = (
        f'Sur <strong>{m["n"]} trades</strong> ({_e(result.get("range",""))}, '
        f'{result.get("n_titres","?")} titres), le signal price-derived '
        + ("<strong>franchit</strong> les seuils §10.9."
           if passed else "<strong>ne franchit pas</strong> les seuils §10.9 : "
           f'hit {_fmt_pct(m["hit_rate"])}, gain/perte {m["gain_loss_ratio"]:.2f}, '
           f'Sharpe {m["sharpe_annual"]:.2f}, DD {_fmt_pct(m["max_drawdown"])}.')
        + (f' Il <strong>bat toutefois le placebo</strong> (Sharpe {m["sharpe_annual"]:.2f} '
           f'vs {result.get("placebo_sharpe",0):.2f}) — donc du signal, pas du bruit.'
           if beats else "")
        + ' La dislocation technique seule n’est pas un moteur de rendement (§0-principe n°3) : '
          'elle filtre l’entrée ; le rendement vient des routes qualitatives (catalyseur, réfutation, décote).'
    )

    legend = "".join(
        f'<span><i style="background:{_SLOTS[i%len(_SLOTS)]}"></i>{_e(n)}</span>'
        for i, n in enumerate(sorted(result.get("exit_reasons", {}), key=lambda k: -result["exit_reasons"][k])))

    body = f"""<div class="bt"><div class="bt-inner">
<h1>Backtest — screener de dislocation</h1>
<p class="sub">Signal price-derived (proxy route A) · protocole §10 · dis_min {_e(result.get('config',{}).get('dis_min','?'))} · {_e(result.get('universe',''))}</p>
<div class="verdict">{tag}<p>{verdict_txt}</p></div>
{_kpis(m)}
<section><h2>Courbe d'équité <span style="color:var(--muted);font-weight:400">(cumul des rendements nets, trades ordonnés)</span></h2>
<div class="card">{_svg_equity(result.get('trades', []))}</div></section>
<section class="grid2">
<div><h2>Motifs de sortie</h2><div class="card">{_svg_exit_bars(result.get('exit_reasons', {}))}
<div class="legend">{legend}</div></div></div>
<div><h2>Distribution des rendements nets</h2><div class="card">{_svg_hist(result.get('trades', []))}
<p class="cap">Bleu = gains · rouge = pertes · trait = 0 %.</p></div></div>
</section>
<section><h2>Décomposition par route <span style="color:var(--muted);font-weight:400">(§10.11 · min {min_route} trades)</span></h2>
<div class="card">{_routes_table(result.get('routes', {}), min_route)}</div></section>
<footer>
<strong>À lire avec ces garde-fous.</strong>
<ul>
<li><strong>Biais de survivance (§10.4)</strong> — univers d'émetteurs encore cotés → résultat optimiste.</li>
<li><strong>Périmètre</strong> — seule la route A (technique) est backtestée ; B/C/E attendent des inputs historiques déterministes (anti-fuite LLM §10.3), D des catalyseurs archivés PIT.</li>
<li><strong>Significativité</strong> — {m['n']} trades {'≥' if m['n']>=_trades_min(m['acceptance']) else '&lt;'} {_trades_min(m['acceptance'])} (§10.9) ; viser plusieurs centaines de small/mid pour conclure.</li>
</ul>
</footer>
</div></div>"""

    if not standalone:
        return f"<style>{_CSS}</style>\n{body}"
    return (f'<!doctype html><html lang="fr"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>Backtest — screener de dislocation</title><style>'
            f'*{{margin:0}}body{{margin:0}}{_CSS}</style></head><body>{body}</body></html>')
