"""
SoloQ Rank Tracker (scraping op.gg)
------------------------------------
Leaderboard en vivo del rango de SoloQ (League of Legends) para una lista
de jugadores de EUW, sacando los datos de op.gg (sin API key).

⚠️ Aviso: esto es scraping. op.gg puede cambiar su estructura interna o
bloquear peticiones automatizadas en cualquier momento. Si deja de
funcionar, revisa la sección "Si se rompe" del README.

Uso:
1. Edita la lista PLAYERS más abajo con tus jugadores (formato "Nombre-TAG",
   tal cual aparece en la URL de op.gg).
2. streamlit run app.py
"""

import re
import time
from dataclasses import dataclass
from urllib.parse import quote

import requests
import streamlit as st
import streamlit.components.v1 as components
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# CONFIGURACIÓN
# ---------------------------------------------------------------------------

# Formato: "Nombre-TAG" (el # de tu Riot ID se sustituye por un guion en la
# URL de op.gg). Ej: si tu Riot ID es "Caps#EUW", pon "Caps-EUW".
PLAYERS: list[str] = [
    "GLS Khael-7714",
    "GLS luihjy5-EUW",
    "Wachakuky-Wacha",
    "Calvo-Diego",
    "T1 El Goat-Calvo",
    "starsSergio-EUW",
    "coolest guy ever-Jeff",
    "Asterius TF-EUW",
    "Ryan Gosling-PhyX",
    "Sesu-NIER",
    "guillespia-1111"
]

REGION = "euw"  # euw, eune, na, lan, las, kr, br, ...
REFRESH_MINUTES = 5

RANK_ORDER = [
    "IRON", "BRONZE", "SILVER", "GOLD", "PLATINUM", "EMERALD",
    "DIAMOND", "MASTER", "GRANDMASTER", "CHALLENGER",
]
DIVISION_ORDER = {"4": 0, "3": 1, "2": 2, "1": 3}

# RANK_EMOJI = {
#     "IRON": "⚙️", "BRONZE": "🥉", "SILVER": "⚪", "GOLD": "🥇",
#     "PLATINUM": "🔷", "EMERALD": "💚", "DIAMOND": "💎",
#     "MASTER": "🔮", "GRANDMASTER": "🔴", "CHALLENGER": "🌟",
# }

RANK_ES = {
    "IRON": "Hierro", "BRONZE": "Bronce", "SILVER": "Plata", "GOLD": "Oro",
    "PLATINUM": "Platino", "EMERALD": "Esmeralda", "DIAMOND": "Diamante",
    "MASTER": "Maestro", "GRANDMASTER": "Gran Maestro", "CHALLENGER": "Aspirante",
}

RANK_COLOR = {
    "IRON": "#5b5548", "BRONZE": "#8c5a2b", "SILVER": "#7f8a93",
    "GOLD": "#c8922a", "PLATINUM": "#2b9d8f", "EMERALD": "#20a86b",
    "DIAMOND": "#4f8ff0", "MASTER": "#a259e6", "GRANDMASTER": "#e14b4b",
    "CHALLENGER": "#f0c419",
}

DIVISION_ROMAN = {"1": "I", "2": "II", "3": "III", "4": "IV"}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
}

# ---------------------------------------------------------------------------
# SCRAPING
# ---------------------------------------------------------------------------


@dataclass
class PlayerRank:
    player_slug: str
    tier: str | None
    division: str | None
    lp: int
    wins: int
    losses: int
    error: str | None = None

    @property
    def winrate(self) -> float:
        total = self.wins + self.losses
        return (self.wins / total * 100) if total else 0.0

    @property
    def sort_key(self):
        if self.tier is None:
            return (-1, -1, -1)
        tier_idx = RANK_ORDER.index(self.tier) if self.tier in RANK_ORDER else -1
        div_idx = DIVISION_ORDER.get(self.division, 0) if self.division else 0
        return (tier_idx, div_idx, self.lp)

    @property
    def display_rank(self) -> str:
        if self.tier is None:
            return "Sin clasificar"
        # emoji = RANK_EMOJI.get(self.tier, "")
        label = RANK_ES.get(self.tier, self.tier.capitalize())
        if self.tier in ("MASTER", "GRANDMASTER", "CHALLENGER"):
            # return f"{emoji} {label} · {self.lp} LP"
            return f"{label} · {self.lp} LP"
        div_roman = DIVISION_ROMAN.get(self.division, "")
        # return f"{emoji} {label} {div_roman} · {self.lp} LP"
        return f"{label} {div_roman} · {self.lp} LP"

    @property
    def opgg_url(self) -> str:
        return f"https://op.gg/lol/summoners/{REGION}/{quote(self.player_slug)}"

    @property
    def display_name(self) -> str:
        return self.player_slug.replace("-", "#")


RANKED_DESC_RE = re.compile(
    r"current SOLORANKED rank is ([A-Za-z]+) (\d+) Division \d+ (\d+) LP "
    r"with (\d+) wins, (\d+) losses",
)

UNRANKED_DESC_RE = re.compile(r"is (?:currently )?unranked", re.IGNORECASE)


@st.cache_data(ttl=60 * REFRESH_MINUTES, show_spinner=False)
def _fetch_player_rank_data(player_slug: str) -> dict:
    """Hace la petición HTTP y el parseo. Devuelve solo tipos primitivos
    (dict) porque st.cache_data necesita poder serializar el valor
    devuelto, y algunos entornos (p. ej. Streamlit Cloud) fallan al
    intentarlo con instancias de dataclass."""
    url = f"https://op.gg/lol/summoners/{REGION}/{quote(player_slug)}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
    except requests.RequestException as exc:
        return {"error": f"Error de red: {exc}"}

    if resp.status_code == 404:
        return {"error": "Jugador no encontrado"}
    if resp.status_code != 200:
        return {"error": f"HTTP {resp.status_code}"}

    text = resp.text

    # Fuente principal: la meta-descripción SEO que op.gg genera en texto
    # plano, del tipo:
    # "...current SOLORANKED rank is emerald 4 Division 4 15 LP with 93
    # wins, 84 losses, and a 53% win rate..."
    # Es más estable que depender de la estructura interna de React/Next.js.
    match = RANKED_DESC_RE.search(text)
    if match:
        tier, division, lp, wins, losses = match.groups()
        return {
            "tier": tier.upper(),
            "division": division,
            "lp": int(lp),
            "wins": int(wins),
            "losses": int(losses),
        }

    # Sin rango de SoloQ esta temporada
    near_ranked_text = "SOLORANKED" in text or "solo" in text.lower()
    if near_ranked_text:
        return {}

    # Ni siquiera se encuentra la sección esperada: guarda la respuesta
    # para poder diagnosticar (bloqueo, cambio de estructura, etc.)
    try:
        debug_path = f"debug_{player_slug}.html"
        with open(debug_path, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError:
        debug_path = None

    soup = BeautifulSoup(text, "html.parser")
    title = soup.title.string.strip() if soup.title and soup.title.string else "(sin título)"
    hint = f" [título de la página: {title}]"
    if debug_path:
        hint += f" [guardado en {debug_path}]"

    return {"error": f"No se encontraron los datos de rango{hint}"}


def fetch_player_rank(player_slug: str) -> PlayerRank:
    data = _fetch_player_rank_data(player_slug)
    return PlayerRank(
        player_slug=player_slug,
        tier=data.get("tier"),
        division=data.get("division"),
        lp=data.get("lp", 0),
        wins=data.get("wins", 0),
        losses=data.get("losses", 0),
        error=data.get("error"),
    )


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

st.set_page_config(page_title="SoloQ Rank Tracker", page_icon="⚔️", layout="wide")

LOL_ICON_SVG = """
<svg width="40" height="40" viewBox="0 0 40 40" xmlns="http://www.w3.org/2000/svg" style="vertical-align:middle;margin-right:10px;">
  <circle cx="20" cy="20" r="19" fill="#0a1428" stroke="#c8aa6e" stroke-width="2"/>
  <g stroke="#c8aa6e" stroke-width="2.4" stroke-linecap="round">
    <line x1="10" y1="28" x2="27" y2="11"/>
    <line x1="10" y1="11" x2="27" y2="28"/>
    <line x1="7" y1="25" x2="12" y2="30"/>
    <line x1="28" y1="9" x2="33" y2="14"/>
    <line x1="7" y1="14" x2="12" y2="9"/>
    <line x1="28" y1="30" x2="33" y2="25"/>
  </g>
</svg>
"""

st.markdown(
    f'<div style="display:flex;align-items:center;">{LOL_ICON_SVG}'
    f'<h1 style="margin:0;">SoloQ Rank Tracker</h1></div>',
    unsafe_allow_html=True,
)
st.caption(
    f"EUW (op.gg) · Actualiza automáticamente cada {REFRESH_MINUTES} min · "
    f"Última carga: {time.strftime('%H:%M:%S')}"
)

with st.spinner("Consultando op.gg..."):
    ranks = [fetch_player_rank(p) for p in PLAYERS]

ranks.sort(key=lambda r: r.sort_key, reverse=True)

any_broken = any(r.error and "no se encontraron los datos" in (r.error or "").lower() for r in ranks)
if any_broken:
    st.warning(
        "Parece que op.gg ha cambiado su estructura interna y el scraper no "
        "puede leer los datos. Revisa la sección 'Si se rompe' del README."
    )

for r in ranks:
    if r.error:
        st.caption(f"⚠️ {r.display_name}: {r.error}")

def _winrate_cell(r: PlayerRank) -> str:
    if r.error or (r.wins + r.losses) == 0:
        return '<span class="wr-empty">—</span>'
    pct = r.winrate
    bar_color = "#20a86b" if pct >= 50 else "#e1544b"
    return (
        f'<div class="wr-wrap" data-sort="{pct:.1f}">'
        f'<div class="wr-bar-bg"><div class="wr-bar-fill" '
        f'style="width:{pct:.0f}%;background:{bar_color};"></div></div>'
        f'<span class="wr-pct">{pct:.0f}%</span></div>'
    )


def _rank_cell(r: PlayerRank) -> str:
    if r.tier is None:
        return f'<span class="rank-pill rank-unranked" data-sort="-1">{r.display_rank}</span>'
    color = RANK_COLOR.get(r.tier, "#888")
    sort_val = r.sort_key[0] * 1000 + r.sort_key[1] * 100 + r.sort_key[2]
    return (
        f'<span class="rank-pill" style="background:{color}22;color:{color};'
        f'border:1px solid {color}55;" data-sort="{sort_val}">{r.display_rank}</span>'
    )


rows_html = ""
for r in ranks:
    wins = r.wins if not r.error else "—"
    losses = r.losses if not r.error else "—"
    win_sort = r.wins if not r.error else -1
    loss_sort = r.losses if not r.error else -1
    rows_html += (
        "<tr>"
        f'<td>{r.display_name}</td>'
        f'<td>{_rank_cell(r)}</td>'
        f'<td data-sort="{r.winrate if not r.error else -1}">{_winrate_cell(r)}</td>'
        f'<td class="num-cell" data-sort="{win_sort}">{wins}</td>'
        f'<td class="num-cell" data-sort="{loss_sort}">{losses}</td>'
        f'<td><a class="opgg-link" href="{r.opgg_url}" target="_blank">Ver perfil ↗</a></td>'
        "</tr>"
    )

table_html = f"""
<style>
  .soloq-wrap {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    width: 100%;
    padding: 0 8px;
    box-sizing: border-box;
  }}
  table.soloq-table {{
    width: 100%;
    table-layout: fixed;
    border-collapse: separate;
    border-spacing: 0;
    background: #12151c;
    border-radius: 12px;
    overflow: hidden;
    box-shadow: 0 4px 18px rgba(0,0,0,0.25);
  }}
  table.soloq-table thead th {{
    background: linear-gradient(180deg, #1d2230, #171b26);
    color: #cfd6e6;
    text-align: left;
    padding: 12px 16px;
    font-size: 13px;
    letter-spacing: 0.03em;
    text-transform: uppercase;
    cursor: pointer;
    user-select: none;
    border-bottom: 2px solid #2b3142;
    white-space: nowrap;
  }}
  table.soloq-table thead th:hover {{ color: #ffffff; background: #232a3b; }}
  table.soloq-table thead th::after {{ content: " ⇅"; opacity: 0.35; font-size: 11px; }}
  table.soloq-table thead th.sorted-asc::after {{ content: " ↑"; opacity: 1; color: #4f8ff0; }}
  table.soloq-table thead th.sorted-desc::after {{ content: " ↓"; opacity: 1; color: #4f8ff0; }}
  table.soloq-table thead th.no-sort {{
    cursor: default;
  }}
  table.soloq-table thead th.no-sort::after {{ content: ""; }}
  table.soloq-table thead th.no-sort:hover {{ background: #1d2230; color: #cfd6e6; }}
  table.soloq-table tbody td {{
    padding: 12px 16px;
    color: #e6e9f0;
    font-size: 14px;
    border-bottom: 1px solid #232838;
  }}
  table.soloq-table tbody tr:last-child td {{ border-bottom: none; }}
  table.soloq-table tbody tr:hover {{ background: #1a2030; }}
  table.soloq-table tbody tr:nth-child(even) {{ background: #151923; }}
  table.soloq-table tbody tr:nth-child(even):hover {{ background: #1a2030; }}
  .num-cell {{ text-align: center; }}
  .rank-pill {{
    display: inline-block;
    padding: 4px 10px;
    border-radius: 999px;
    font-weight: 600;
    font-size: 13px;
    white-space: nowrap;
  }}
  .rank-unranked {{ background: #2a2f3b; color: #8b93a6; border: 1px solid #3a4152; }}
  .wr-wrap {{ display: flex; align-items: center; gap: 8px; }}
  .wr-bar-bg {{ background: #262c3b; border-radius: 6px; width: 90px; height: 8px; overflow: hidden; }}
  .wr-bar-fill {{ height: 100%; border-radius: 6px; }}
  .wr-pct {{ font-size: 13px; color: #cfd6e6; min-width: 34px; }}
  .wr-empty {{ color: #5a6272; }}
  .opgg-link {{
    color: #7fb2ff; text-decoration: none; font-weight: 500; font-size: 13.5px;
  }}
  .opgg-link:hover {{ text-decoration: underline; }}
</style>

<div class="soloq-wrap">
<table class="soloq-table" id="soloq-table">
  <thead>
    <tr>
      <th class="no-sort">Nombre</th>
      <th class="no-sort">Rango</th>
      <th data-type="num" data-col="2">Winrate</th>
      <th data-type="num" data-col="3">Victorias</th>
      <th data-type="num" data-col="4">Derrotas</th>
      <th class="no-sort">op.gg</th>
    </tr>
  </thead>
  <tbody>
    {rows_html}
  </tbody>
</table>
</div>

<script>
(function() {{
  const table = document.getElementById("soloq-table");
  const headers = table.querySelectorAll("thead th[data-type]");
  let sortState = {{ col: -1, dir: 1 }};

  headers.forEach((th) => {{
    const colIndex = parseInt(th.getAttribute("data-col"), 10);
    th.addEventListener("click", () => {{
      const tbody = table.querySelector("tbody");
      const rows = Array.from(tbody.querySelectorAll("tr"));
      const type = th.getAttribute("data-type");

      if (sortState.col === colIndex) {{
        sortState.dir *= -1;
      }} else {{
        sortState = {{ col: colIndex, dir: 1 }};
      }}

      headers.forEach(h => h.classList.remove("sorted-asc", "sorted-desc"));
      th.classList.add(sortState.dir === 1 ? "sorted-asc" : "sorted-desc");

      rows.sort((a, b) => {{
        const cellA = a.children[colIndex];
        const cellB = b.children[colIndex];
        let valA = cellA.getAttribute("data-sort");
        let valB = cellB.getAttribute("data-sort");

        if (valA === null) valA = cellA.textContent.trim();
        if (valB === null) valB = cellB.textContent.trim();

        if (type === "num") {{
          valA = parseFloat(valA);
          valB = parseFloat(valB);
          return (valA - valB) * sortState.dir;
        }}
        return valA.localeCompare(valB) * sortState.dir;
      }});

      rows.forEach(row => tbody.appendChild(row));
    }});
  }});
}})();
</script>
"""

components.html(table_html, height=90 + 54 * len(ranks), scrolling=False)

st.caption(
    "Datos obtenidos de op.gg mediante scraping (no oficial, no afiliado a "
    "op.gg ni a Riot Games). Puede dejar de funcionar si op.gg cambia su web."
)

# ---------------------------------------------------------------------------
# AUTO-REFRESCO NATIVO (sin dependencias externas)
# ---------------------------------------------------------------------------
# Espera en background y relanza el script. No usa componentes de terceros,
# así que no arrastra el problema de versiones de pyarrow/numpy.
time.sleep(60 * REFRESH_MINUTES)
st.rerun()
