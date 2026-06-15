import streamlit as st
import sqlite3
import qrcode
import io
import pandas as pd
from datetime import datetime, date
import os
import base64
from PIL import Image

try:
    from streamlit_qrcode_scanner import qrcode_scanner
    CAMERA_SCANNER = True
except ImportError:
    CAMERA_SCANNER = False

try:
    import cv2
    import numpy as np
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "attendance.db")


# ── Database ──────────────────────────────────────────────────────────────────

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT    NOT NULL,
                employee_id TEXT    UNIQUE NOT NULL,
                created_at  TEXT    DEFAULT (datetime('now','localtime'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS attendance (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                employee_id TEXT    NOT NULL,
                action      TEXT    NOT NULL CHECK(action IN ('IN','OUT')),
                timestamp   TEXT    DEFAULT (datetime('now','localtime')),
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        conn.commit()


def add_user(name: str, employee_id: str) -> tuple:
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT INTO users (name, employee_id) VALUES (?, ?)",
                (name, employee_id)
            )
            conn.commit()
        return True, "User added successfully."
    except sqlite3.IntegrityError:
        return False, f"Employee ID '{employee_id}' already exists."


def get_users() -> pd.DataFrame:
    with sqlite3.connect(DB_PATH) as conn:
        return pd.read_sql(
            "SELECT id, name, employee_id, created_at FROM users ORDER BY name",
            conn
        )


def delete_user(user_id: int):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM attendance WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()


def get_last_action_today(employee_id: str):
    today = date.today().isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            """SELECT action FROM attendance
               WHERE employee_id = ? AND date(timestamp) = ?
               ORDER BY timestamp DESC, id DESC LIMIT 1""",
            (employee_id, today)
        ).fetchone()
    return row[0] if row else None


def record_attendance(employee_id: str) -> tuple:
    with sqlite3.connect(DB_PATH) as conn:
        user = conn.execute(
            "SELECT id, name FROM users WHERE employee_id = ?",
            (employee_id,)
        ).fetchone()
        if not user:
            return None, None
        last   = get_last_action_today(employee_id)
        action = "OUT" if last == "IN" else "IN"
        conn.execute(
            "INSERT INTO attendance (user_id, employee_id, action) VALUES (?, ?, ?)",
            (user[0], employee_id, action)
        )
        conn.commit()
    return user[1], action


def get_report(filter_date=None, filter_user=None) -> pd.DataFrame:
    query = """
        SELECT u.name        AS "Name",
               a.employee_id AS "Employee ID",
               a.action      AS "Action",
               a.timestamp   AS "Time"
        FROM attendance a
        JOIN users u ON a.user_id = u.id
        WHERE 1=1
    """
    params = []
    if filter_date:
        query += " AND date(a.timestamp) = ?"
        params.append(filter_date.isoformat())
    if filter_user and filter_user != "All":
        query += " AND u.name = ?"
        params.append(filter_user)
    query += " ORDER BY a.timestamp DESC"
    with sqlite3.connect(DB_PATH) as conn:
        return pd.read_sql(query, conn, params=params)


def get_currently_present() -> pd.DataFrame:
    today = date.today().isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        return pd.read_sql("""
            SELECT u.name, a.employee_id, a.timestamp AS check_in_time
            FROM attendance a
            JOIN users u ON a.user_id = u.id
            WHERE date(a.timestamp) = ?
              AND a.action = 'IN'
              AND a.id = (
                  SELECT MAX(a2.id) FROM attendance a2
                  WHERE a2.employee_id = a.employee_id
                    AND date(a2.timestamp) = ?
              )
            ORDER BY a.timestamp DESC
        """, conn, params=[today, today])


# ── QR helpers ────────────────────────────────────────────────────────────────

def generate_qr_bytes(employee_id: str, box_size: int = 10) -> bytes:
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_H,  # high correction — reads well off screens
        box_size=box_size,
        border=4,
    )
    qr.add_data(employee_id)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def get_user_by_name_or_id(query: str):
    q = f"%{query.strip()}%"
    with sqlite3.connect(DB_PATH) as conn:
        return conn.execute(
            "SELECT name, employee_id FROM users WHERE name LIKE ? OR employee_id LIKE ? ORDER BY name",
            (q, q)
        ).fetchall()


def decode_qr_from_upload(image: Image.Image):
    if not CV2_AVAILABLE:
        return None
    arr = np.array(image.convert("RGB"))
    bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    data, _, _ = cv2.QRCodeDetector().detectAndDecode(bgr)
    return data if data else None


def img_to_b64(img_bytes: bytes) -> str:
    return base64.b64encode(img_bytes).decode()


# ── Styles ────────────────────────────────────────────────────────────────────

def apply_styles():
    st.markdown("""
    <style>
    /* Hide Streamlit chrome */
    #MainMenu, footer, header {visibility: hidden;}
    .block-container {padding-top: 2rem; padding-bottom: 2rem;}

    /* ── Sidebar ── */
    [data-testid="stSidebar"] {
        background: linear-gradient(160deg, #0f1f5c 0%, #1e3a9e 100%);
    }
    [data-testid="stSidebar"] p,
    [data-testid="stSidebar"] span,
    [data-testid="stSidebar"] label,
    [data-testid="stSidebar"] div {
        color: rgba(255,255,255,0.9) !important;
    }
    [data-testid="stSidebar"] .stRadio > label {display: none;}
    [data-testid="stSidebar"] .stRadio label {
        border-radius: 12px !important;
        padding: 12px 18px !important;
        font-size: 15px !important;
        font-weight: 500 !important;
    }
    [data-testid="stSidebar"] hr {border-color: rgba(255,255,255,0.15);}

    /* ── Metric cards ── */
    [data-testid="metric-container"] {
        background: white;
        border-radius: 16px;
        padding: 20px 24px !important;
        box-shadow: 0 2px 16px rgba(0,0,0,0.07);
        border: 1px solid #f0f2f6;
    }
    [data-testid="stMetricLabel"] p { color: #6b7280 !important; font-size: 13px !important; font-weight: 600 !important; text-transform: uppercase; letter-spacing: 0.5px; }
    [data-testid="stMetricValue"]   { color: #111827 !important; font-size: 36px !important; font-weight: 800 !important; }

    /* ── Inputs & selects ── */
    .stTextInput input, .stSelectbox select {
        border-radius: 10px !important;
        border: 2px solid #e5e7eb !important;
        font-size: 15px !important;
    }
    .stTextInput input:focus {
        border-color: #2563eb !important;
        box-shadow: 0 0 0 3px rgba(37,99,235,0.1) !important;
    }

    /* ── Buttons ── */
    .stButton > button {
        border-radius: 12px !important;
        font-weight: 600 !important;
        font-size: 15px !important;
        padding: 10px 24px !important;
        transition: all 0.15s ease !important;
    }
    .stButton > button[kind="primary"] {
        background: linear-gradient(135deg, #1e3a9e, #2563eb) !important;
        border: none !important;
        box-shadow: 0 4px 12px rgba(37,99,235,0.3) !important;
    }
    .stButton > button[kind="primary"]:hover {
        box-shadow: 0 6px 20px rgba(37,99,235,0.45) !important;
        transform: translateY(-1px) !important;
    }

    /* ── Tabs ── */
    .stTabs [data-baseweb="tab-list"] {
        background: #f3f4f6;
        border-radius: 12px;
        padding: 4px;
        gap: 4px;
    }
    .stTabs [data-baseweb="tab"] {
        border-radius: 10px !important;
        font-weight: 500 !important;
        font-size: 14px !important;
    }
    .stTabs [aria-selected="true"] {
        background: white !important;
        box-shadow: 0 1px 4px rgba(0,0,0,0.1) !important;
    }

    /* ── Headings ── */
    h1 { font-size: 26px !important; font-weight: 800 !important; color: #111827 !important; }
    h2 { font-size: 20px !important; font-weight: 700 !important; color: #1f2937 !important; }
    h3 { font-size: 16px !important; font-weight: 600 !important; color: #374151 !important; }

    /* ── Dataframe ── */
    [data-testid="stDataFrame"] { border-radius: 12px; overflow: hidden; }
    </style>
    """, unsafe_allow_html=True)


# ── Reusable UI components ─────────────────────────────────────────────────────

def scan_banner(scan: dict):
    if scan["action"] == "IN":
        gradient = "linear-gradient(135deg, #16a34a 0%, #22c55e 100%)"
        shadow   = "rgba(22,163,74,0.35)"
        icon     = "✅"
        label    = "CHECKED IN"
    else:
        gradient = "linear-gradient(135deg, #ea580c 0%, #fb923c 100%)"
        shadow   = "rgba(234,88,12,0.35)"
        icon     = "👋"
        label    = "CHECKED OUT"

    st.markdown(f"""
    <div style="
        background:{gradient};
        border-radius:20px;
        padding:32px 24px;
        text-align:center;
        margin:12px 0 24px;
        box-shadow: 0 8px 32px {shadow};
    ">
        <div style="font-size:56px;line-height:1.2;">{icon}</div>
        <div style="color:white;font-size:32px;font-weight:800;margin:8px 0 4px;">{scan['name']}</div>
        <div style="color:rgba(255,255,255,0.85);font-size:14px;letter-spacing:3px;font-weight:700;">{label}</div>
        <div style="color:rgba(255,255,255,0.7);font-size:13px;margin-top:6px;">{scan['time']}</div>
    </div>
    """, unsafe_allow_html=True)


def user_card(row, qr_bytes: bytes):
    qr_b64 = img_to_b64(qr_bytes)
    st.markdown(f"""
    <div style="
        background:white;
        border-radius:16px;
        padding:24px 20px;
        box-shadow:0 2px 16px rgba(0,0,0,0.08);
        border:1px solid #f0f2f6;
        text-align:center;
        height:100%;
    ">
        <img src="data:image/png;base64,{qr_b64}"
             style="width:140px;height:140px;border-radius:10px;border:3px solid #f3f4f6;" />
        <div style="font-size:17px;font-weight:700;color:#111827;margin-top:14px;">{row['name']}</div>
        <div style="
            display:inline-block;
            background:#eff6ff;
            color:#1d4ed8;
            font-size:12px;
            font-weight:600;
            padding:3px 12px;
            border-radius:20px;
            margin-top:6px;
            letter-spacing:0.5px;
        ">{row['employee_id']}</div>
    </div>
    """, unsafe_allow_html=True)


def action_badge(action: str) -> str:
    if action == "IN":
        return "🟢 IN"
    return "🟠 OUT"


# ── Scan processing ───────────────────────────────────────────────────────────

def process_scan(raw: str):
    employee_id = raw.strip()
    name, action = record_attendance(employee_id)
    if name:
        st.session_state.last_scan = {
            "name":   name,
            "action": action,
            "time":   datetime.now().strftime("%I:%M:%S %p"),
            "id":     employee_id,
        }
        st.rerun()
    else:
        st.error(f"⚠️  Unknown QR code: **{employee_id}** — make sure this employee is registered.")


# ── Pages ─────────────────────────────────────────────────────────────────────

def page_scanner():
    # Header row: title + live date
    col_title, col_date = st.columns([3, 1])
    col_title.markdown("## 📷  Attendance Scanner")
    col_date.markdown(
        f"<div style='text-align:right;color:#6b7280;font-size:13px;padding-top:10px;'>"
        f"{datetime.now().strftime('%A, %B %d %Y')}</div>",
        unsafe_allow_html=True,
    )

    # Last scan result
    scan = st.session_state.get("last_scan")
    if scan:
        scan_banner(scan)
    else:
        st.markdown("""
        <div style="
            background:#f8fafc;
            border:2px dashed #cbd5e1;
            border-radius:16px;
            padding:24px;
            text-align:center;
            color:#94a3b8;
            margin-bottom:24px;
        ">
            <div style="font-size:36px;">📋</div>
            <div style="font-size:15px;font-weight:500;margin-top:6px;">No scan yet — waiting for first QR code</div>
        </div>
        """, unsafe_allow_html=True)

    tab_cam, tab_img = st.tabs(["📷  Camera Scan", "🖼️  Upload QR Image"])

    with tab_cam:
        if CAMERA_SCANNER:
            st.markdown(
                "<p style='color:#6b7280;font-size:14px;text-align:center;margin-bottom:12px;'>"
                "Hold the QR code badge up to the camera — it records automatically.</p>",
                unsafe_allow_html=True,
            )
            value = qrcode_scanner(key="cam_scanner")
            if value:
                process_scan(value)
        else:
            st.warning("Camera scanner not installed. Run: `pip install streamlit-qrcode-scanner`")

    with tab_img:
        st.markdown(
            "<p style='color:#6b7280;font-size:14px;'>Take a photo of the QR badge and upload it below.</p>",
            unsafe_allow_html=True,
        )
        uploaded = st.file_uploader("Upload QR image", type=["png", "jpg", "jpeg"],
                                    label_visibility="collapsed")
        if uploaded:
            image = Image.open(uploaded)
            col_img, col_result = st.columns([1, 1])
            with col_img:
                st.image(image, caption="Uploaded QR", use_container_width=True)
            with col_result:
                if not CV2_AVAILABLE:
                    st.error("OpenCV not installed — image decoding unavailable.")
                else:
                    qr_data = decode_qr_from_upload(image)
                    if qr_data:
                        st.success(f"QR detected: `{qr_data}`")
                        st.button("✅  Record Attendance", type="primary",
                                  on_click=process_scan, args=(qr_data,))
                    else:
                        st.error("No QR code found in this image. Try a clearer photo.")

    # Who's currently present
    st.divider()
    present_df = get_currently_present()
    total_users = len(get_users())

    m1, m2, m3 = st.columns(3)
    m1.metric("Currently Present", len(present_df))
    m2.metric("Total Employees",   total_users)
    m3.metric("Absent Today",      max(0, total_users - len(present_df)))

    if not present_df.empty:
        st.markdown("### 🟢  Currently In Office")
        present_df["check_in_time"] = pd.to_datetime(present_df["check_in_time"]).dt.strftime("%I:%M %p")
        present_df = present_df.rename(columns={"name": "Name", "employee_id": "ID", "check_in_time": "Checked In"})
        st.dataframe(present_df, use_container_width=True, hide_index=True)


def page_users():
    st.markdown("## 👥  Manage Employees")

    # Add user card
    st.markdown("""
    <div style="background:white;border-radius:16px;padding:24px 28px;
                box-shadow:0 2px 16px rgba(0,0,0,0.07);border:1px solid #f0f2f6;margin-bottom:24px;">
        <h3 style="margin:0 0 16px;">➕  Add New Employee</h3>
    """, unsafe_allow_html=True)

    with st.form("add_user_form", clear_on_submit=True):
        col1, col2, col3 = st.columns([2, 1, 1])
        name   = col1.text_input("Full Name", placeholder="e.g. Maria Santos")
        emp_id = col2.text_input("Employee ID", placeholder="e.g. EMP001")
        col3.markdown("<div style='padding-top:28px;'></div>", unsafe_allow_html=True)
        submitted = col3.form_submit_button("Add Employee", type="primary", use_container_width=True)
        if submitted:
            if name.strip() and emp_id.strip():
                ok, msg = add_user(name.strip(), emp_id.strip().upper())
                if ok:
                    st.success(f"✅  {msg}")
                    st.rerun()
                else:
                    st.error(f"❌  {msg}")
            else:
                st.warning("Please fill in both the name and employee ID.")

    st.markdown("</div>", unsafe_allow_html=True)

    # Employee list
    users_df = get_users()
    if users_df.empty:
        st.markdown("""
        <div style="background:#f8fafc;border:2px dashed #cbd5e1;border-radius:16px;
                    padding:40px;text-align:center;color:#94a3b8;">
            <div style="font-size:40px;">👤</div>
            <div style="font-size:16px;font-weight:500;margin-top:8px;">No employees yet</div>
            <div style="font-size:13px;margin-top:4px;">Add your first employee using the form above</div>
        </div>
        """, unsafe_allow_html=True)
        return

    st.markdown(f"### All Employees &nbsp;<span style='color:#6b7280;font-size:14px;font-weight:400;'>({len(users_df)} total)</span>", unsafe_allow_html=True)

    # 2-column card grid
    cols = st.columns(2)
    for i, (_, row) in enumerate(users_df.iterrows()):
        qr_bytes = generate_qr_bytes(row["employee_id"])
        with cols[i % 2]:
            user_card(row, qr_bytes)
            btn_col1, btn_col2 = st.columns(2)
            btn_col1.download_button(
                "⬇️  Download QR",
                data=qr_bytes,
                file_name=f"qr_{row['employee_id']}.png",
                mime="image/png",
                key=f"dl_{row['id']}",
                use_container_width=True,
            )
            if btn_col2.button("🗑️  Remove", key=f"del_{row['id']}", use_container_width=True):
                delete_user(int(row["id"]))
                st.rerun()
            st.markdown("<div style='height:16px;'></div>", unsafe_allow_html=True)


def page_reports():
    st.markdown("## 📊  Attendance Reports")

    # Filters row
    col1, col2, col3 = st.columns([1, 1, 1])
    filter_date = col1.date_input("📅  Date", value=date.today())

    users_df  = get_users()
    user_list = ["All"] + sorted(users_df["name"].tolist()) if not users_df.empty else ["All"]
    filter_user = col2.selectbox("👤  Employee", user_list)

    df = get_report(filter_date, filter_user)

    # Always show today's summary in metrics
    today_df   = get_report(date.today())
    in_today   = int((today_df["Action"] == "IN").sum())
    out_today  = int((today_df["Action"] == "OUT").sum())
    present_df = get_currently_present()

    st.markdown("<div style='height:16px;'></div>", unsafe_allow_html=True)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Records Found",      len(df))
    m2.metric("Check-ins Today",    in_today)
    m3.metric("Check-outs Today",   out_today)
    m4.metric("Currently In Office", len(present_df))

    st.divider()

    if df.empty:
        st.markdown("""
        <div style="background:#f8fafc;border:2px dashed #cbd5e1;border-radius:16px;
                    padding:40px;text-align:center;color:#94a3b8;">
            <div style="font-size:36px;">🗂️</div>
            <div style="font-size:15px;font-weight:500;margin-top:8px;">No records found</div>
            <div style="font-size:13px;margin-top:4px;">Try a different date or employee filter</div>
        </div>
        """, unsafe_allow_html=True)
        return

    # Style the Action column
    df["Action"] = df["Action"].map(action_badge)

    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Name":        st.column_config.TextColumn("Name",        width="medium"),
            "Employee ID": st.column_config.TextColumn("Employee ID", width="small"),
            "Action":      st.column_config.TextColumn("Action",      width="small"),
            "Time":        st.column_config.TextColumn("Time",        width="medium"),
        },
    )

    csv = df.to_csv(index=False).encode()
    st.download_button(
        "⬇️  Export as CSV",
        data=csv,
        file_name=f"attendance_{filter_date}.csv",
        mime="text/csv",
    )


def page_my_qr():
    st.markdown("## 📱  Get My QR Code")
    st.markdown(
        "<p style='color:#6b7280;font-size:15px;'>Type your name below, then <b>screenshot</b> "
        "your QR code and save it to your phone. Show it at the scanner every day.</p>",
        unsafe_allow_html=True,
    )

    query = st.text_input("🔍  Search your name or ID", placeholder="e.g. Maria or EMP001")

    if not query.strip():
        st.markdown("""
        <div style="background:#f0f7ff;border-radius:16px;padding:32px;text-align:center;
                    color:#3b82f6;margin-top:16px;">
            <div style="font-size:48px;">📲</div>
            <div style="font-size:16px;font-weight:600;margin-top:10px;">Start typing your name above</div>
            <div style="font-size:13px;color:#6b7280;margin-top:6px;">Your QR code will appear here</div>
        </div>
        """, unsafe_allow_html=True)
        return

    results = get_user_by_name_or_id(query)

    if not results:
        st.error("No employee found. Ask your admin to add you first.")
        return

    for name, employee_id in results:
        qr_bytes = generate_qr_bytes(employee_id, box_size=14)  # bigger for phone screens
        qr_b64   = img_to_b64(qr_bytes)

        st.markdown(f"""
        <div style="
            background: white;
            border-radius: 24px;
            padding: 36px 28px;
            box-shadow: 0 4px 32px rgba(0,0,0,0.10);
            text-align: center;
            max-width: 380px;
            margin: 20px auto;
            border: 1px solid #f0f2f6;
        ">
            <div style="font-size:22px;font-weight:800;color:#111827;">{name}</div>
            <div style="
                display:inline-block;
                background:#eff6ff;color:#1d4ed8;
                font-size:13px;font-weight:700;
                padding:4px 14px;border-radius:20px;margin:6px 0 20px;
            ">{employee_id}</div>
            <br/>
            <img src="data:image/png;base64,{qr_b64}"
                 style="width:220px;height:220px;border-radius:12px;
                        border:4px solid #f3f4f6;" />
            <div style="color:#6b7280;font-size:13px;margin-top:20px;line-height:1.6;">
                📸 <b>Screenshot this page</b> and save to your photos.<br/>
                Show this QR code at the scanner each time you arrive or leave.
            </div>
        </div>
        """, unsafe_allow_html=True)

        st.download_button(
            "⬇️  Save QR to phone",
            data=qr_bytes,
            file_name=f"my_qr_{employee_id}.png",
            mime="image/png",
            use_container_width=True,
            key=f"myqr_{employee_id}",
        )
        st.markdown("<div style='height:8px;'></div>", unsafe_allow_html=True)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    st.set_page_config(
        page_title="Attendance System",
        page_icon="🏢",
        layout="wide",
    )
    apply_styles()
    init_db()

    with st.sidebar:
        st.markdown("""
        <div style="text-align:center;padding:12px 0 20px;">
            <div style="font-size:40px;">🏢</div>
            <div style="font-size:20px;font-weight:800;color:white;margin-top:6px;">Attendance</div>
            <div style="font-size:12px;color:rgba(255,255,255,0.55);margin-top:2px;">Offline QR System</div>
        </div>
        """, unsafe_allow_html=True)

        st.divider()

        page = st.radio(
            "nav",
            ["📷  Scanner", "📱  My QR Code", "👥  Employees", "📊  Reports"],
            label_visibility="collapsed",
        )

        st.divider()
        st.markdown(
            f"<div style='font-size:11px;color:rgba(255,255,255,0.4);text-align:center;'>"
            f"{datetime.now().strftime('%b %d, %Y  %I:%M %p')}</div>",
            unsafe_allow_html=True,
        )

    pages = {
        "📷  Scanner":   page_scanner,
        "📱  My QR Code": page_my_qr,
        "👥  Employees": page_users,
        "📊  Reports":   page_reports,
    }
    pages[page]()


if __name__ == "__main__":
    main()
