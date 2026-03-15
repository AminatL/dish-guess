import ast
import io
import random
import re
from dataclasses import dataclass
from difflib import get_close_matches
from pathlib import Path
from typing import List, Optional

import base64
import openpyxl
import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components
from PIL import Image, ImageFilter


APP_DIR = Path(__file__).resolve().parent
DATA_DEFAULT_PATH = APP_DIR / "Dish Guesser.xlsx"
BLURRED_OUTPUT_DIR = APP_DIR / "Dish Photos Blurred"
BACKGROUND_PATH = APP_DIR / "Background.png"

MAX_POINTS = 10000
INGREDIENT_PENALTY = 1000
CLUE_PENALTY = 2000


@dataclass
class Dish:
    name: str
    ingredients: List[str]
    cook_time: Optional[str]
    country: Optional[str]
    country_flag: Optional[str]
    region: Optional[str]
    sweet_or_savoury: Optional[str]
    cooking_method: Optional[str]
    description: Optional[str]
    recipe_link: Optional[str]
    image_clear: Optional[str]
    image_blurred: Optional[str]


def clean_text(value) -> Optional[str]:
    if pd.isna(value):
        return None
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return None
    return text


def normalize_source(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return value.strip().strip("\"'")


def resolve_local_path(source: str) -> Path:
    source_path = Path(source)
    if source_path.is_absolute():
        return source_path
    return APP_DIR / source_path


def is_url(source: str) -> bool:
    return source.startswith("http://") or source.startswith("https://")


def get_base64_image(path: Path) -> Optional[str]:
    try:
        with open(path, "rb") as f:
            data = f.read()
        return base64.b64encode(data).decode()
    except Exception:
        return None


@st.cache_data(show_spinner=False)
def fetch_image_bytes(source: Optional[str]) -> Optional[bytes]:
    source = normalize_source(source)
    if not source:
        return None
    try:
        if is_url(source):
            headers = {
                "User-Agent": "Mozilla/5.0 (DishGuessr/1.0)",
                "Accept": "image/*,*/*;q=0.8",
            }
            response = requests.get(source, headers=headers, timeout=20)
            response.raise_for_status()
            return response.content
        path = resolve_local_path(source)
        if path.exists():
            return path.read_bytes()
    except Exception as exc:
        st.session_state["last_image_error"] = f"{source} -> {exc}"
        return None
    return None


@st.cache_data(show_spinner=False)
def generate_blurred_bytes(clear_source: Optional[str], dish_name: str, radius: int = 18) -> Optional[bytes]:
    base_image_bytes = fetch_image_bytes(clear_source)
    if not base_image_bytes:
        return None
    try:
        image = Image.open(io.BytesIO(base_image_bytes)).convert("RGB")
        blurred = image.filter(ImageFilter.GaussianBlur(radius))
        out = io.BytesIO()
        blurred.save(out, format="JPEG", quality=85)
        blurred_bytes = out.getvalue()
        BLURRED_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "_", dish_name.lower()).strip("_") or "dish"
        output_path = BLURRED_OUTPUT_DIR / f"{safe_name}_blur.jpg"
        output_path.write_bytes(blurred_bytes)
        return blurred_bytes
    except Exception:
        return None


@st.cache_data(show_spinner=False)
def load_data() -> List[Dish]:
    df = pd.read_excel(DATA_DEFAULT_PATH)
    df.columns = [str(c).strip() for c in df.columns]

    def parse_ingredients(val):
        if pd.isna(val):
            return []
        try:
            parsed = ast.literal_eval(val)
            if isinstance(parsed, list):
                return [str(x).strip() for x in parsed if str(x).strip()]
        except Exception:
            pass
        return [s.strip() for s in str(val).split(",") if s.strip()]

    dishes = []
    for _, row in df.iterrows():
        dishes.append(
            Dish(
                name=(clean_text(row.get("dish")) or "").strip(),
                ingredients=parse_ingredients(row.get("ingredients", "")),
                cook_time=clean_text(row.get("(Clue) Cook time")),
                country=clean_text(row.get("(Clue) Country of Origin")),
                country_flag=normalize_source(clean_text(row.get("Country Flag"))),
                region=clean_text(row.get("Region")),
                sweet_or_savoury=clean_text(row.get("(Clue) Sweet or Savoury")),
                cooking_method=clean_text(row.get("Cooking Method")),
                description=clean_text(row.get("Dish description")),
                recipe_link=clean_text(row.get("Reciple Link (goodfood)")),
                image_clear=normalize_source(clean_text(row.get("Image (clear)"))),
                image_blurred=normalize_source(clean_text(row.get("Image (blurred)"))),
            )
        )
    return [d for d in dishes if d.name and d.ingredients]


def init_game(dishes: List[Dish]):
    dish = random.choice(dishes)
    st.session_state.dish = dish
    st.session_state.revealed = 1
    st.session_state.guesses_left = max(3, len(dish.ingredients))
    st.session_state.score = MAX_POINTS
    st.session_state.message = ""
    st.session_state.game_over = False
    st.session_state.won = False
    st.session_state.revealed_clues = set()
    st.session_state.confetti_fired = False


def penalty(points):
    st.session_state.score = max(0, st.session_state.score - points)


def reveal_next_ingredient():
    if st.session_state.revealed < len(st.session_state.dish.ingredients):
        st.session_state.revealed += 1
        penalty(INGREDIENT_PENALTY)


def reveal_clue(clue_key: str):
    if clue_key not in st.session_state.revealed_clues:
        st.session_state.revealed_clues.add(clue_key)
        penalty(CLUE_PENALTY)


def check_guess(guess: str):
    dish = st.session_state.dish
    normalized = guess.strip().lower()
    if not normalized:
        st.session_state.message = "Type a dish name to guess."
        return
    close = get_close_matches(normalized, [dish.name.lower()], n=1, cutoff=0.6)
    if close:
        st.session_state.message = "Correct! You nailed it."
        st.session_state.won = True
        st.session_state.game_over = True
        return
    st.session_state.guesses_left -= 1
    st.session_state.message = "Not quite...try again"
    reveal_next_ingredient()
    if st.session_state.guesses_left <= 0:
        st.session_state.game_over = True
        st.session_state.message = "Out of guesses. Better luck next dish."


def show_confetti():
    components.html(
        """
        <script src="https://cdn.jsdelivr.net/npm/canvas-confetti@1.6.0/dist/confetti.browser.min.js"></script>
        <script>
            var myCanvas = window.parent.document.createElement('canvas');
            myCanvas.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;z-index:9999;pointer-events:none;';
            window.parent.document.body.appendChild(myCanvas);
            var myConfetti = confetti.create(myCanvas, { resize: true, useWorker: true });
            myConfetti({ particleCount: 180, spread: 100, origin: { x: 0.5, y: 0 },
                colors: ['#2f9e44', '#fdba74', '#f6f2ea', '#334155', '#fbbf24', '#86efac'] });
            setTimeout(() => {
                myConfetti({ particleCount: 80, angle: 60, spread: 70, origin: { x: 0, y: 0.3 },
                    colors: ['#2f9e44', '#fdba74', '#fbbf24'] });
                myConfetti({ particleCount: 80, angle: 120, spread: 70, origin: { x: 1, y: 0.3 },
                    colors: ['#2f9e44', '#fdba74', '#fbbf24'] });
            }, 500);
            var end = Date.now() + 3000;
            (function trickle() {
                myConfetti({ particleCount: 6, angle: 90, spread: 120,
                    origin: { x: Math.random(), y: 0 },
                    colors: ['#2f9e44', '#fdba74', '#f6f2ea', '#fbbf24', '#86efac'],
                    gravity: 0.8, scalar: 0.9 });
                if (Date.now() < end) requestAnimationFrame(trickle);
                else myCanvas.remove();
            }());
        </script>
        """,
        height=0,
    )


def current_dish_image(dish: Dish, show_clear: bool) -> Optional[bytes]:
    clear_source = getattr(dish, "image_clear", None)
    blurred_source = getattr(dish, "image_blurred", None)
    if show_clear:
        return fetch_image_bytes(clear_source) or fetch_image_bytes(blurred_source)
    blurred = fetch_image_bytes(blurred_source)
    if blurred:
        return blurred
    return generate_blurred_bytes(clear_source, dish.name)


def standardize_image_bytes(image_bytes: Optional[bytes], width: int = 700, height: int = 230) -> Optional[bytes]:
    if not image_bytes:
        return None
    try:
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        src_w, src_h = image.size
        target_ratio = width / height
        src_ratio = src_w / src_h
        if src_ratio > target_ratio:
            new_w = int(src_h * target_ratio)
            left = (src_w - new_w) // 2
            image = image.crop((left, 0, left + new_w, src_h))
        else:
            new_h = int(src_w / target_ratio)
            top = (src_h - new_h) // 2
            image = image.crop((0, top, src_w, top + new_h))
        image = image.resize((width, height), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        image.save(out, format="JPEG", quality=90)
        return out.getvalue()
    except Exception:
        return image_bytes


def standardize_flag_bytes(flag_bytes: Optional[bytes], width: int = 96, height: int = 64) -> Optional[bytes]:
    if not flag_bytes:
        return None
    try:
        flag = Image.open(io.BytesIO(flag_bytes)).convert("RGBA")
        flag.thumbnail((width, height), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (width, height), (255, 255, 255, 0))
        x = (width - flag.width) // 2
        y = (height - flag.height) // 2
        canvas.paste(flag, (x, y), flag)
        out = io.BytesIO()
        canvas.convert("RGB").save(out, format="PNG")
        return out.getvalue()
    except Exception:
        return flag_bytes


def render_country_card(country: Optional[str], flag_source: Optional[str], is_clue: bool = False):
    country_text = country or "Unknown"
    card_class = "stat-card clue-card" if is_clue else "stat-card"
    clue_sub = "<div class='clue-sub'>Clue</div>" if is_clue else ""
    st.markdown(
        f"""
        <div class='{card_class}'>
          {clue_sub}
          <div class='clue'>Country</div>
          <div class='big-number'>{country_text}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(page_title="Dish Guessr", page_icon="🍽️", layout="wide")

# ── Background image ──────────────────────────────────────────────────────────
bg_base64 = get_base64_image(BACKGROUND_PATH)
bg_css = (
    f"""
    [data-testid="stAppViewContainer"] {{
        background-image: url("data:image/png;base64,{bg_base64}");
        background-size: cover;
        background-position: center;
        background-attachment: fixed;
        background-repeat: no-repeat;
    }}
    [data-testid="stHeader"] {{ background: rgba(0,0,0,0); }}
    [data-testid="stToolbar"] {{ right: 2rem; }}
    """
    if bg_base64
    else ""
)

css = f"""
<style>
{bg_css}
:root {{
  --bg: #f6f2ea;
  --ink: #1e1d1a;
  --accent: #2f9e44;
  --danger: #d9480f;
  --panel: #ffffff;
  --muted: #6f6a62;
  --shadow: 0 8px 30px rgba(0,0,0,0.08);
}}
[data-testid='stAppViewContainer'] {{ color-scheme: light !important; }}
main, .main, section.main {{ background: transparent !important; }}
[data-testid='stAppViewContainer'] > .main {{ background: transparent !important; }}
.block-container {{ max-width: 80% !important; background: transparent !important; padding-top: 1rem !important; }}

.title {{
    font-family: 'Georgia', 'Times New Roman', serif;
    letter-spacing: 1px;
    font-size: clamp(42px, 6vw, 68px);
    margin-bottom: 4px;
    color: var(--ink);
    text-align: center;
}}
.panel {{ background: var(--panel); box-shadow: var(--shadow); border-radius: 14px; padding: 14px 18px; border: 2px solid #ece6dc; }}
.panel.description {{ font-size: 15px; line-height: 1.6; }}
.dish-name {{ font-family: 'Georgia', 'Times New Roman', serif; font-size: clamp(18px, 2.5vw, 24px); text-align: center; }}
.ingredients-row {{ display: flex; align-items: center; justify-content: center; gap: 8px; flex-wrap: wrap; margin: 6px 0 12px 0; }}
.ingredient {{ display: flex; align-items: center; justify-content: center; text-align: center; padding: 2px 4px; font-family: 'Trebuchet MS', sans-serif; font-size: clamp(20px, 3vw, 28px); font-weight: 700; }}
.ingredient.hidden {{ color: #c9c1b5; }}
.plus {{ font-size: clamp(16px, 2vw, 20px); font-weight: bold; color: #b7aa9a; }}
.status {{ font-size: 16px; font-weight: 600; text-align: center; }}
.status.good {{ color: var(--accent); }}
.status.bad {{ color: var(--danger); }}
.stat-card {{ border-radius: 12px; padding: 10px 12px; border: 2px solid #efe7db; background: #fff; box-shadow: var(--shadow); text-align: center; }}
.clue {{ font-weight: 700; font-size: clamp(14px, 2vw, 18px); color: #334155; text-align: center; }}
.big-number {{ font-size: clamp(18px, 2.5vw, 26px); font-weight: 700; color: var(--ink); }}
.result-title {{ font-family: 'Georgia', 'Times New Roman', serif; font-size: clamp(24px, 3vw, 32px); text-align: center; margin-bottom: 6px; }}
.result-sub {{ text-align: center; color: var(--muted); margin-bottom: 10px; }}
.fixed-image {{ width: 55%; margin: 0 auto; border-radius: 14px; overflow: hidden; border: 2px solid #ece6dc; box-shadow: var(--shadow); }}
.fixed-image img {{ width: 100% !important; height: auto !important; max-height: 38vh !important; object-fit: cover !important; display: block; }}
.clue-sub {{ font-size: 11px; letter-spacing: 1px; text-transform: uppercase; color: #92400e; margin-bottom: 6px; text-align: center; font-weight: 700; }}
.clue-card {{ background: #fff7ed; border: 2px solid #fdba74; }}
button[kind='secondary'] {{ width: 100%; border-radius: 12px !important; padding: 10px 14px !important; border: 2px solid #fdba74 !important; background: #fff7ed !important; color: #92400e !important; box-shadow: var(--shadow) !important; font-weight: 700 !important; }}
button[kind='secondary']:hover {{ background: #fed7aa !important; }}

@media (max-width: 900px) {{
    .block-container {{ max-width: 95% !important; }}
    .fixed-image {{ width: 85% !important; }}
    .stat-card {{ padding: 8px 6px !important; }}
    .ingredient {{ font-size: clamp(16px, 5vw, 22px) !important; }}
}}
</style>
"""

st.markdown(css, unsafe_allow_html=True)
st.markdown('<div class="title">Dish Guessr</div>', unsafe_allow_html=True)

if st.session_state.get("last_image_error"):
    st.warning(st.session_state["last_image_error"])

try:
    dishes = load_data()
except Exception as exc:
    dishes = []
    st.error(f"Could not load the Excel file: {exc}")

if "dish" not in st.session_state and dishes:
    init_game(dishes)
elif dishes and not hasattr(st.session_state.dish, "image_blurred"):
    init_game(dishes)

if not dishes:
    st.stop()

dish = st.session_state.dish
reveal_answer = st.session_state.game_over

top_left, top_right = st.columns([4, 1])
with top_left:
    st.caption(f"Loaded {len(dishes)} dishes")
with top_right:
    if st.button("New Dish", type="primary", use_container_width=True):
        init_game(dishes)
        st.rerun()

if reveal_answer:
    if st.session_state.won and not st.session_state.get("confetti_fired", False):
        show_confetti()
        st.session_state.confetti_fired = True

    st.markdown("<div class='result-title'>Round Complete</div>", unsafe_allow_html=True)
    result_sub = "You guessed correctly. 🎉" if st.session_state.won else "You ran out of guesses."
    st.markdown(f"<div class='result-sub'>{result_sub}</div>", unsafe_allow_html=True)

    clear_image = standardize_image_bytes(current_dish_image(dish, show_clear=True))
    if clear_image:
        st.markdown("<div class='fixed-image'>", unsafe_allow_html=True)
        st.image(clear_image, use_container_width=True)
        st.markdown("</div>", unsafe_allow_html=True)

    title = dish.name.title()
    if dish.recipe_link:
        st.markdown(
            f"<div class='panel dish-name'><a href='{dish.recipe_link}' target='_blank'>{title}</a></div>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(f"<div class='panel dish-name'>{title}</div>", unsafe_allow_html=True)

    if dish.description:
        st.markdown(f"<div class='panel description'>{dish.description}</div>", unsafe_allow_html=True)

    result_stats = st.columns(3)
    result_stats[0].markdown(
        f"<div class='stat-card'><div class='clue'>Final Score</div><div class='big-number'>{st.session_state.score}</div></div>",
        unsafe_allow_html=True,
    )
    result_stats[1].markdown(
        f"<div class='stat-card'><div class='clue'>Guesses Used</div><div class='big-number'>{max(0, len(dish.ingredients) - st.session_state.guesses_left)}</div></div>",
        unsafe_allow_html=True,
    )
    result_stats[2].markdown(
        f"<div class='stat-card'><div class='clue'>Ingredients Revealed</div><div class='big-number'>{len(dish.ingredients)}</div></div>",
        unsafe_allow_html=True,
    )

    info_row_1 = st.columns(2)
    info_row_1[0].markdown(
        f"<div class='stat-card'><div class='clue'>Cook Time</div><div class='big-number'>{dish.cook_time or 'Unknown'}</div></div>",
        unsafe_allow_html=True,
    )
    info_row_1[1].markdown(
        f"<div class='stat-card'><div class='clue'>Sweet/Savoury</div><div class='big-number'>{dish.sweet_or_savoury or 'Unknown'}</div></div>",
        unsafe_allow_html=True,
    )

    info_row_2 = st.columns(2)
    with info_row_2[0]:
        render_country_card(dish.country, dish.country_flag, is_clue=False)
    info_row_2[1].markdown(
        f"<div class='stat-card'><div class='clue'>Cooking Method</div><div class='big-number'>{dish.cooking_method or 'Unknown'}</div></div>",
        unsafe_allow_html=True,
    )

else:
    blurred_image = standardize_image_bytes(current_dish_image(dish, show_clear=False))
    if blurred_image:
        st.markdown("<div class='fixed-image'>", unsafe_allow_html=True)
        st.image(blurred_image, use_container_width=True)
        st.markdown("</div>", unsafe_allow_html=True)
    else:
        st.markdown("<div class='panel' style='text-align:center;'>Image unavailable for this dish.</div>", unsafe_allow_html=True)

    st.markdown("<div class='panel dish-name'>????????</div>", unsafe_allow_html=True)

    revealed = st.session_state.revealed
    ingredient_nodes = []
    for i, ingredient in enumerate(dish.ingredients):
        is_revealed = i < revealed
        text = ingredient if is_revealed else "?"
        cls = "ingredient" if is_revealed else "ingredient hidden"
        ingredient_nodes.append(f"<div class='{cls}'>{text}</div>")

    joined = ""
    for i, node in enumerate(ingredient_nodes):
        joined += node
        if i < len(ingredient_nodes) - 1:
            joined += "<div class='plus'>+</div>"
    st.markdown(f"<div class='ingredients-row'>{joined}</div>", unsafe_allow_html=True)

    st.markdown(f"<div class='status'>{st.session_state.message}</div>", unsafe_allow_html=True)

    info1, info2 = st.columns(2)
    with info1:
        render_country_card(dish.country, dish.country_flag, is_clue=False)
    info2.markdown(
        f"<div class='stat-card'><div class='clue'>Cooking Method</div><div class='big-number'>{dish.cooking_method or 'Unknown'}</div></div>",
        unsafe_allow_html=True,
    )

    guess = st.text_input("Your guess", value="", key="guess_input", label_visibility="collapsed", placeholder="Type your guess here...")
    if st.button("Submit Guess", type="primary"):
        check_guess(guess)
        st.rerun()

    st.markdown("<div class='clue-sub'>Clues</div>", unsafe_allow_html=True)
    clue1, clue2 = st.columns(2)
    with clue1:
        if "cook_time" in st.session_state.revealed_clues:
            st.markdown(
                f"<div class='stat-card clue-card'><div class='clue-sub'>Clue</div><div class='clue'>Cook Time</div><div class='big-number'>{dish.cook_time or 'Unknown'}</div></div>",
                unsafe_allow_html=True,
            )
        else:
            if st.button("Reveal Cook Time", type="secondary", use_container_width=True):
                reveal_clue("cook_time")
                st.rerun()

    with clue2:
        if "sweet_savoury" in st.session_state.revealed_clues:
            st.markdown(
                f"<div class='stat-card clue-card'><div class='clue-sub'>Clue</div><div class='clue'>Sweet / Savoury</div><div class='big-number'>{dish.sweet_or_savoury or 'Unknown'}</div></div>",
                unsafe_allow_html=True,
            )
        else:
            if st.button("Reveal Sweet/Savoury", type="secondary", use_container_width=True):
                reveal_clue("sweet_savoury")
                st.rerun()

    stats = st.columns(3)
    stats[0].markdown(
        f"<div class='stat-card'><div class='clue'>Guesses Left</div><div class='big-number'>{st.session_state.guesses_left}</div></div>",
        unsafe_allow_html=True,
    )
    stats[1].markdown(
        f"<div class='stat-card'><div class='clue'>Score</div><div class='big-number'>{st.session_state.score}</div></div>",
        unsafe_allow_html=True,
    )
    stats[2].markdown(
        f"<div class='stat-card'><div class='clue'>Ingredients Revealed</div><div class='big-number'>{st.session_state.revealed}/{len(dish.ingredients)}</div></div>",
        unsafe_allow_html=True,
    )
