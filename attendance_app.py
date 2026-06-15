import streamlit as st
import sqlite3
import qrcode
import io
import pandas as pd
from datetime import datetime, date
import os
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
    """Returns (user_name, action) or (None, None) when ID not found."""
    with sqlite3.connect(DB_PATH) as conn:
        user = conn.execute(
            "SELECT id, name FROM users WHERE employee_id = ?",
            (employee_id,)
        ).fetchone()
        if not user:
            return None, None

        last = get_last_action_today(employee_id)
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
               a.timestamp   AS "Timestamp"
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


# ── QR helpers ────────────────────────────────────────────────────────────────

def generate_qr_bytes(employee_id: str) -> bytes:
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(employee_id)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def decode_qr_from_upload(image: Image.Image):
    """Uses OpenCV QRCodeDetector — no extra system libraries needed."""
    if not CV2_AVAILABLE:
        return None
    arr = np.array(image.convert("RGB"))
    bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    data, _, _ = cv2.QRCodeDetector().detectAndDecode(bgr)
    return data if data else None


# ── Scan processing ───────────────────────────────────────────────────────────

def process_scan(raw: str):
    employee_id = raw.strip()
    name, action = record_attendance(employee_id)
    if name:
        st.session_state.last_scan = {
            "name": name,
            "action": action,
            "time": datetime.now().strftime("%H:%M:%S"),
            "id": employee_id,
        }
        st.rerun()
    else:
        st.error(f"Unknown employee ID: **{employee_id}**")


# ── Pages ─────────────────────────────────────────────────────────────────────

def page_scanner():
    st.title("QR Attendance Scanner")

    scan = st.session_state.get("last_scan")
    if scan:
        if scan["action"] == "IN":
            st.success(
                f"✅  **{scan['name']}** ({scan['id']}) checked **IN** at {scan['time']}"
            )
        else:
            st.info(
                f"👋  **{scan['name']}** ({scan['id']}) checked **OUT** at {scan['time']}"
            )

    tab_cam, tab_img = st.tabs(["📷  Camera Scan", "🖼️  Upload QR Image"])

    with tab_cam:
        if CAMERA_SCANNER:
            st.caption("Point your camera at a QR code — attendance is recorded automatically.")
            value = qrcode_scanner(key="cam_scanner")
            if value:
                process_scan(value)
        else:
            st.warning(
                "Camera scanner package not installed.  "
                "Run `pip install streamlit-qrcode-scanner` and restart."
            )

    with tab_img:
        st.caption("Upload a QR code image to record attendance.")
        uploaded = st.file_uploader("Choose QR image", type=["png", "jpg", "jpeg"])
        if uploaded:
            image = Image.open(uploaded)
            col_img, col_result = st.columns(2)
            with col_img:
                st.image(image, caption="Uploaded image", use_container_width=True)
            with col_result:
                if not CV2_AVAILABLE:
                    st.error("OpenCV not installed — image decoding unavailable.")
                else:
                    qr_data = decode_qr_from_upload(image)
                    if qr_data:
                        st.write(f"Decoded: `{qr_data}`")
                        if st.button("Record Attendance", type="primary"):
                            process_scan(qr_data)
                    else:
                        st.error("No QR code detected in this image.")


def page_users():
    st.title("Manage Users")

    with st.expander("➕  Add New User", expanded=True):
        with st.form("add_user_form", clear_on_submit=True):
            col1, col2 = st.columns(2)
            name   = col1.text_input("Full Name")
            emp_id = col2.text_input("Employee ID")
            if st.form_submit_button("Add User", type="primary"):
                if name.strip() and emp_id.strip():
                    ok, msg = add_user(name.strip(), emp_id.strip().upper())
                    if ok:
                        st.success(msg)
                        st.rerun()
                    else:
                        st.error(msg)
                else:
                    st.warning("Please fill in both fields.")

    st.subheader("All Users")
    users_df = get_users()

    if users_df.empty:
        st.info("No users yet.  Add one above.")
        return

    for _, row in users_df.iterrows():
        with st.expander(f"👤  {row['name']}  —  {row['employee_id']}"):
            left, right = st.columns([1, 2])
            qr_bytes = generate_qr_bytes(row["employee_id"])

            with left:
                st.image(qr_bytes, width=200)
                st.download_button(
                    "⬇️  Download QR",
                    data=qr_bytes,
                    file_name=f"qr_{row['employee_id']}.png",
                    mime="image/png",
                    key=f"dl_{row['id']}",
                )

            with right:
                st.markdown(f"**Name:** {row['name']}")
                st.markdown(f"**Employee ID:** {row['employee_id']}")
                st.markdown(f"**Added:** {row['created_at']}")
                st.write("")
                if st.button("🗑️  Delete User", key=f"del_{row['id']}"):
                    delete_user(int(row["id"]))
                    st.success("User deleted.")
                    st.rerun()


def page_reports():
    st.title("Attendance Reports")

    col1, col2 = st.columns(2)
    filter_date = col1.date_input("Date", value=date.today())

    users_df = get_users()
    user_list = ["All"] + sorted(users_df["name"].tolist()) if not users_df.empty else ["All"]
    filter_user = col2.selectbox("Employee", user_list)

    df = get_report(filter_date, filter_user)

    # Today summary (always for today regardless of filters)
    today_df = get_report(date.today())
    m1, m2, m3 = st.columns(3)
    m1.metric("Records (filtered)", len(df))
    m2.metric("Check-ins today", int((today_df["Action"] == "IN").sum()))
    m3.metric("Check-outs today", int((today_df["Action"] == "OUT").sum()))

    st.divider()

    if df.empty:
        st.info("No records for the selected filters.")
    else:
        st.dataframe(df, use_container_width=True, hide_index=True)
        csv = df.to_csv(index=False).encode()
        st.download_button(
            "⬇️  Export CSV",
            data=csv,
            file_name=f"attendance_{filter_date}.csv",
            mime="text/csv",
        )


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    st.set_page_config(
        page_title="Attendance System",
        page_icon="🏢",
        layout="wide",
    )
    init_db()

    with st.sidebar:
        st.title("🏢 Attendance")
        st.caption("Offline QR Code System")
        st.divider()
        page = st.radio(
            "nav",
            ["📷  Scanner", "👥  Users", "📊  Reports"],
            label_visibility="collapsed",
        )

    pages = {
        "📷  Scanner": page_scanner,
        "👥  Users":   page_users,
        "📊  Reports": page_reports,
    }
    pages[page]()


if __name__ == "__main__":
    main()
