"""
Civil Takeoff — calibrated, click-to-measure quantity takeoff from blueprints.

Workflow:
    1. Upload a PDF or image of a drawing.
    2. Calibrate: draw a line over a known dimension and enter its real length.
       This converts on-screen pixels into real-world units.
    3. Measure: draw lines (linear), rectangles/polygons (area), or drop points
       (counts) on the drawing. Each measurement is added to the takeoff.
    4. (Optional) Ask the AI to suggest likely line items to measure.
    5. Review/edit the takeoff table and export to Excel or CSV.

The AI never produces the measured quantities — geometry does. The AI only
suggests *what* to measure so the engineer stays in control of accuracy.
"""

import base64
import json
import math
from io import BytesIO

import pandas as pd
import streamlit as st
from PIL import Image
from streamlit_drawable_canvas import st_canvas

try:
    import fitz  # PyMuPDF, for rendering PDF pages
except Exception:  # pragma: no cover - optional until installed
    fitz = None


st.set_page_config(page_title="Civil Takeoff", page_icon="📐", layout="wide")

# Max width (display pixels) the drawing is rendered at. Calibration and
# measurement happen at this same size, so the pixel->unit scale stays valid.
MAX_DISPLAY_WIDTH = 950


# --------------------------------------------------------------------------- #
# Session state
# --------------------------------------------------------------------------- #
def init_state():
    st.session_state.setdefault("scale_upp", None)   # real units per display pixel
    st.session_state.setdefault("scale_unit", "ft")  # base linear unit
    st.session_state.setdefault("items", [])         # list of takeoff line items
    st.session_state.setdefault("ai_suggestions", [])


# --------------------------------------------------------------------------- #
# File loading
# --------------------------------------------------------------------------- #
@st.cache_data(show_spinner=False)
def load_pages(file_bytes: bytes, name: str, dpi: int):
    """Return a list of PIL images, one per page (single image -> one page)."""
    ext = name.rsplit(".", 1)[-1].lower()
    if ext == "pdf":
        if fitz is None:
            raise RuntimeError("PyMuPDF is not installed; cannot read PDFs.")
        pages = []
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        zoom = dpi / 72.0
        matrix = fitz.Matrix(zoom, zoom)
        for page in doc:
            pix = page.get_pixmap(matrix=matrix)
            pages.append(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
        doc.close()
        return pages
    return [Image.open(BytesIO(file_bytes)).convert("RGB")]


def fit_for_display(img: Image.Image):
    """Resize so width <= MAX_DISPLAY_WIDTH. Returns (image, scale_factor)."""
    if img.width <= MAX_DISPLAY_WIDTH:
        return img, 1.0
    factor = MAX_DISPLAY_WIDTH / img.width
    size = (MAX_DISPLAY_WIDTH, int(img.height * factor))
    return img.resize(size), factor


# --------------------------------------------------------------------------- #
# Geometry helpers (operate on display pixels)
# --------------------------------------------------------------------------- #
def _wh(obj):
    return (
        obj.get("width", 0) * obj.get("scaleX", 1),
        obj.get("height", 0) * obj.get("scaleY", 1),
    )


def line_length_px(obj):
    w, h = _wh(obj)
    return math.hypot(w, h)


def rect_area_px(obj):
    w, h = _wh(obj)
    return abs(w * h)


def polygon_area_px(obj):
    """Shoelace area from a fabric.js path. Translation-invariant, so any
    constant offset fabric applies to path points does not affect the result."""
    pts = []
    for cmd in obj.get("path", []):
        if len(cmd) >= 3 and isinstance(cmd[1], (int, float)) and isinstance(cmd[2], (int, float)):
            pts.append((cmd[1], cmd[2]))
    if len(pts) < 3:
        return 0.0
    area = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        area += x1 * y2 - x2 * y1
    return abs(area) / 2.0


def count_marks(objects):
    return sum(1 for o in objects if o.get("type") == "circle")


# --------------------------------------------------------------------------- #
# Unit labels
# --------------------------------------------------------------------------- #
def linear_unit(base):
    return "LF" if base == "ft" else base


def area_unit(base):
    return "SF" if base == "ft" else f"{base}²"


def volume_unit(base):
    return "CY" if base == "ft" else f"{base}³"


def to_volume(area_in_base2, depth_in_base, base):
    """area * depth, converted to CY when working in feet."""
    vol = area_in_base2 * depth_in_base
    return vol / 27.0 if base == "ft" else vol


# --------------------------------------------------------------------------- #
# Takeoff item management
# --------------------------------------------------------------------------- #
def add_item(description, category, qty, unit, waste_pct):
    total = qty * (1 + waste_pct / 100.0)
    st.session_state["items"].append(
        {
            "Description": description or "(unnamed)",
            "Category": category or "General",
            "Quantity": round(qty, 3),
            "Unit": unit,
            "Waste %": waste_pct,
            "Total": round(total, 3),
        }
    )


# --------------------------------------------------------------------------- #
# AI suggestions (assist only — does not measure)
# --------------------------------------------------------------------------- #
def suggest_items(api_key, image: Image.Image):
    from openai import OpenAI

    buf = BytesIO()
    image.save(buf, format="JPEG", quality=70)
    encoded = base64.b64encode(buf.getvalue()).decode("utf-8")

    prompt = (
        "You are assisting a civil engineer with a construction takeoff. "
        "Look at this engineering drawing and list the line items that would "
        "typically need to be quantified. Do NOT estimate quantities. "
        "Return ONLY a JSON array of objects with keys: description, category "
        '(e.g. "Earthwork", "Utilities", "Paving", "Concrete", "Erosion Control"), '
        'and unit (one of "LF", "SF", "SY", "CY", "EA", "TON"). '
        "Return at most 20 items."
    )
    client = OpenAI(api_key=api_key)
    resp = client.chat.completions.create(
        model="gpt-4o",
        temperature=0.0,
        max_tokens=900,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{encoded}",
                            "detail": "high",
                        },
                    },
                ],
            }
        ],
    )
    text = resp.choices[0].message.content or "[]"
    # Strip markdown fences if present.
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        text = text[4:] if text.lower().startswith("json") else text
    try:
        data = json.loads(text)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


# --------------------------------------------------------------------------- #
# UI
# --------------------------------------------------------------------------- #
init_state()

st.title("📐 Civil Takeoff")
st.caption(
    "Calibrated, click-to-measure quantity takeoff. Measurements come from "
    "geometry you draw — not from an AI guess."
)

with st.sidebar:
    st.header("1 · Drawing")
    uploaded = st.file_uploader(
        "Upload PDF or image", type=["pdf", "png", "jpg", "jpeg", "webp"]
    )
    dpi = st.slider("PDF render quality (DPI)", 100, 300, 150, 25)

    st.divider()
    st.header("AI assist (optional)")
    api_key = st.text_input("OpenAI API key", type="password")

if not uploaded:
    st.info(
        "Upload a drawing to begin. Then: **calibrate** a known dimension, "
        "**measure**, and **export**."
    )
    st.stop()

# Load pages
file_bytes = uploaded.getvalue()
try:
    pages = load_pages(file_bytes, uploaded.name, dpi)
except Exception as exc:  # noqa: BLE001
    st.error(f"Could not read file: {exc}")
    st.stop()

page_idx = 0
if len(pages) > 1:
    page_idx = st.sidebar.number_input(
        "Page", min_value=1, max_value=len(pages), value=1, step=1
    ) - 1

display_img, _ = fit_for_display(pages[page_idx])
canvas_w, canvas_h = display_img.size

# ---- Calibration -------------------------------------------------------- #
st.subheader("2 · Calibrate scale")
scale = st.session_state["scale_upp"]
if scale:
    st.success(
        f"Scale set: 1 px = {scale:.5g} {st.session_state['scale_unit']}  "
        f"(draw a new line below to recalibrate)."
    )
else:
    st.warning("Not calibrated yet. Draw a line over a known dimension below.")

with st.expander("Set / change scale", expanded=not scale):
    cc1, cc2 = st.columns([3, 2])
    with cc1:
        st.write("Draw **one line** along a dimension you know the length of:")
        calib_canvas = st_canvas(
            background_image=display_img,
            drawing_mode="line",
            stroke_width=3,
            stroke_color="#FF0000",
            height=canvas_h,
            width=canvas_w,
            key=f"calib_{page_idx}",
        )
    with cc2:
        known_len = st.number_input("Known real length", min_value=0.0, value=0.0, step=1.0)
        base_unit = st.selectbox(
            "Unit", ["ft", "in", "yd", "m", "cm", "mm"],
            index=["ft", "in", "yd", "m", "cm", "mm"].index(st.session_state["scale_unit"]),
        )
        if st.button("Set scale", type="primary"):
            objs = (calib_canvas.json_data or {}).get("objects", [])
            lines = [o for o in objs if o.get("type") == "line"]
            if not lines:
                st.error("Draw a line first.")
            elif known_len <= 0:
                st.error("Enter the known length.")
            else:
                px = line_length_px(lines[-1])
                if px <= 0:
                    st.error("Line has zero length.")
                else:
                    st.session_state["scale_upp"] = known_len / px
                    st.session_state["scale_unit"] = base_unit
                    st.rerun()

if not st.session_state["scale_upp"]:
    st.stop()

upp = st.session_state["scale_upp"]
base = st.session_state["scale_unit"]

# ---- Measure ------------------------------------------------------------ #
st.subheader("3 · Measure")
mc1, mc2 = st.columns([3, 2])
with mc2:
    mtype = st.radio(
        "Measurement type",
        ["Linear", "Area", "Count"],
        help="Linear: draw line segments. Area: rectangles or polygons. "
        "Count: drop points.",
    )
    mode = {"Linear": "line", "Area": "polygon", "Count": "point"}[mtype]
    if mtype == "Area":
        area_mode = st.radio("Shape", ["Polygon", "Rectangle"], horizontal=True)
        mode = "polygon" if area_mode == "Polygon" else "rect"

    description = st.text_input("Description", placeholder="e.g. 12\" RCP storm pipe")
    category = st.selectbox(
        "Category",
        ["Earthwork", "Utilities", "Paving", "Concrete", "Erosion Control", "General"],
    )
    waste = st.number_input("Waste / contingency %", min_value=0.0, value=0.0, step=1.0)

    depth = 0.0
    if mtype == "Area":
        depth = st.number_input(
            f"Depth ({base}) — for volume (optional)", min_value=0.0, value=0.0, step=0.5,
            help="If > 0, the area is multiplied by depth to give a volume.",
        )

with mc1:
    measure_canvas = st_canvas(
        background_image=display_img,
        drawing_mode=mode,
        stroke_width=3,
        stroke_color="#00A2FF",
        fill_color="rgba(0, 162, 255, 0.25)",
        point_display_radius=4,
        height=canvas_h,
        width=canvas_w,
        key=f"measure_{page_idx}_{mtype}_{mode}",
    )

if st.button("➕ Add measurement to takeoff", type="primary"):
    objs = (measure_canvas.json_data or {}).get("objects", [])
    if not objs:
        st.warning("Nothing drawn yet.")
    elif mtype == "Linear":
        px = sum(line_length_px(o) for o in objs if o.get("type") == "line")
        add_item(description, category, px * upp, linear_unit(base), waste)
        st.rerun()
    elif mtype == "Area":
        px2 = sum(
            polygon_area_px(o) if o.get("type") == "path" else rect_area_px(o)
            for o in objs
            if o.get("type") in ("path", "rect")
        )
        area_val = px2 * (upp ** 2)
        if depth > 0:
            add_item(description, category, to_volume(area_val, depth, base),
                     volume_unit(base), waste)
        else:
            add_item(description, category, area_val, area_unit(base), waste)
        st.rerun()
    else:  # Count
        add_item(description, category, count_marks(objs), "EA", waste)
        st.rerun()

# ---- AI suggestions ----------------------------------------------------- #
with st.expander("💡 AI: suggest line items to measure"):
    st.caption(
        "The AI proposes *what* to measure (description, category, unit). "
        "You still measure each one — quantities are never AI-generated."
    )
    if st.button("Suggest line items"):
        if not api_key:
            st.error("Enter your OpenAI API key in the sidebar.")
        else:
            with st.spinner("Asking the model..."):
                try:
                    st.session_state["ai_suggestions"] = suggest_items(
                        api_key, pages[page_idx]
                    )
                except Exception as exc:  # noqa: BLE001
                    st.error(f"AI request failed: {exc}")
    if st.session_state["ai_suggestions"]:
        sug_df = pd.DataFrame(st.session_state["ai_suggestions"])
        st.dataframe(sug_df, use_container_width=True, hide_index=True)
        if st.button("Add all as zero-qty placeholders"):
            for s in st.session_state["ai_suggestions"]:
                add_item(
                    s.get("description", ""), s.get("category", "General"),
                    0.0, s.get("unit", "EA"), 0.0,
                )
            st.session_state["ai_suggestions"] = []
            st.rerun()

# ---- Takeoff table & export -------------------------------------------- #
st.subheader("4 · Takeoff")
if not st.session_state["items"]:
    st.info("No items yet. Measure something above to populate the takeoff.")
    st.stop()

df = pd.DataFrame(st.session_state["items"])
edited = st.data_editor(
    df,
    use_container_width=True,
    num_rows="dynamic",
    key="takeoff_editor",
)
# Recompute Total from possibly-edited Quantity / Waste, persist edits.
if not edited.empty:
    edited["Total"] = (
        edited["Quantity"].fillna(0) * (1 + edited["Waste %"].fillna(0) / 100.0)
    ).round(3)
st.session_state["items"] = edited.to_dict("records")

# Totals by unit
if not edited.empty:
    summary = (
        edited.groupby("Unit", dropna=False)["Total"].sum().reset_index()
    )
    st.markdown("**Totals by unit**")
    st.dataframe(summary, use_container_width=True, hide_index=True)

ec1, ec2 = st.columns(2)
with ec1:
    csv = edited.to_csv(index=False).encode("utf-8")
    st.download_button("⬇️ Download CSV", csv, "takeoff.csv", "text/csv")
with ec2:
    xbuf = BytesIO()
    with pd.ExcelWriter(xbuf, engine="openpyxl") as writer:
        edited.to_excel(writer, index=False, sheet_name="Takeoff")
        if not edited.empty:
            summary.to_excel(writer, index=False, sheet_name="Totals")
    st.download_button(
        "⬇️ Download Excel",
        xbuf.getvalue(),
        "takeoff.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
