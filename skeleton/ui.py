"""
TransitFlow — Gradio Web Interface
====================================
Run with:  python skeleton/ui.py
Then open: http://localhost:7860

Students: You do NOT need to change this file.
"""

import sys
sys.path.insert(0, ".")

import gradio as gr
from skeleton.agent import run_agent
from skeleton.llm_provider import llm
from skeleton.config import GEMINI_CHAT_MODEL, OLLAMA_CHAT_MODEL
from databases.relational.queries import (
    login_user,
    register_user,
    get_user_secret_question,
    verify_secret_answer,
    update_password,
    query_user_bookings,
)
###nini fix
from databases.graph.queries import query_station_connections
###nini fix end

SECRET_QUESTIONS = [
    "What is the name of your first pet?",
    "What is your mother's maiden name?",
    "What city were you born in?",
    "What was the name of your first school?",
    "What is your favourite book?",
    "What was the make of your first car?",
]

###nini fix
# Station ID → display name mapping for the station lookup panel
STATION_CHOICES = [
    ("MS01 — Central Square (Metro)", "MS01"),
    ("MS02 — Riverside (Metro)", "MS02"),
    ("MS03 — Northgate (Metro)", "MS03"),
    ("MS04 — Elm Park (Metro)", "MS04"),
    ("MS05 — Westfield (Metro)", "MS05"),
    ("MS06 — Harbour View (Metro)", "MS06"),
    ("MS07 — Old Town (Metro)", "MS07"),
    ("MS08 — University (Metro)", "MS08"),
    ("MS09 — Queensbridge (Metro)", "MS09"),
    ("MS10 — Parkside (Metro)", "MS10"),
    ("MS11 — Greenhill (Metro)", "MS11"),
    ("MS12 — Lakeshore (Metro)", "MS12"),
    ("MS13 — Clifton (Metro)", "MS13"),
    ("MS14 — Eastwick (Metro)", "MS14"),
    ("MS15 — Ferndale (Metro)", "MS15"),
    ("MS16 — Hilltop (Metro)", "MS16"),
    ("MS17 — Broadmoor (Metro)", "MS17"),
    ("MS18 — Sunnyvale (Metro)", "MS18"),
    ("MS19 — Redwood (Metro)", "MS19"),
    ("MS20 — Thornton (Metro)", "MS20"),
    ("NR01 — Central Station (Rail)", "NR01"),
    ("NR02 — Maplewood (Rail)", "NR02"),
    ("NR03 — Old Town Junction (Rail)", "NR03"),
    ("NR04 — Ashford (Rail)", "NR04"),
    ("NR05 — Stonehaven (Rail)", "NR05"),
    ("NR06 — Bridgeport (Rail)", "NR06"),
    ("NR07 — Ferndale Halt (Rail)", "NR07"),
    ("NR08 — Coalport (Rail)", "NR08"),
    ("NR09 — Dunmore (Rail)", "NR09"),
    ("NR10 — Langford End (Rail)", "NR10"),
]
###nini fix end


# ── Chat handler ───────────────────────────────────────────────────────────────

def chat(user_message: str, history_display: list, agent_history: list,
         show_debug: bool, current_user: str):
    if not user_message.strip():
        return history_display, agent_history, gr.update()

    if show_debug:
        answer, new_agent_history, debug_text = run_agent(
            user_message, agent_history, debug=True, current_user_email=current_user
        )
    else:
        answer, new_agent_history = run_agent(
            user_message, agent_history, debug=False, current_user_email=current_user
        )
        debug_text = ""

    history_display = history_display + [
        {"role": "user",      "content": user_message},
        {"role": "assistant", "content": answer},
    ]

    debug_update = gr.update(value=debug_text, visible=show_debug)
    return history_display, new_agent_history, debug_update


def clear_conversation():
    return [], [], gr.update(value="", visible=False)


# ── Provider / model selection ────────────────────────────────────────────────

_KNOWN_OLLAMA_MODELS = ["llama3.2:1b", "llama3.1:8b"]


def get_ollama_status():
    if llm.ollama_available():
        return "🟢 Ollama is running locally"
    return "🔴 Ollama not detected — install from ollama.com and run `ollama pull " + OLLAMA_CHAT_MODEL + "`"


def get_chat_model_choices() -> list:
    available = set(llm.get_available_ollama_models())
    choices = []
    for m in _KNOWN_OLLAMA_MODELS:
        label = m if m in available else f"{m}  (not pulled)"
        choices.append((label, m))
    choices.append((f"☁️ Gemini ({GEMINI_CHAT_MODEL})", "gemini"))
    return choices


def get_initial_chat_model_value() -> str:
    return "llama3.2:1b"


def on_chat_model_change(value: str):
    if value == "gemini":
        status = llm.set_chat_provider("gemini")
        return f"**Active:** ☁️ Gemini ({GEMINI_CHAT_MODEL})\n\n{status}", get_ollama_status()
    available = set(llm.get_available_ollama_models())
    if value not in available:
        return f"⚠️ `{value}` is not pulled. Run: `ollama pull {value}`", get_ollama_status()
    llm.set_chat_provider("ollama")
    status = llm.set_chat_model(value)
    return f"**Active:** {value}\n\n{status}", get_ollama_status()


# ── Auth handlers ──────────────────────────────────────────────────────────────

def do_login(email: str, password: str):
    if not email.strip() or not password.strip():
        return (
            gr.update(value="Please enter your email and password.", visible=True),
            None,
            gr.update(), gr.update(), gr.update(), gr.update(),
            gr.update(visible=True),
        )

    user = login_user(email.strip(), password)
    if user is None:
        return (
            gr.update(value="Incorrect email or password.", visible=True),
            None,
            gr.update(), gr.update(), gr.update(), gr.update(),
            gr.update(visible=True),
        )

    display_name = f"{user['first_name']} {user['surname']}"
    return (
        gr.update(value="", visible=False),
        user["email"],
        gr.update(visible=False),
        gr.update(visible=False),
        gr.update(value=f"**Welcome, {display_name}**", visible=True),
        gr.update(visible=True),
        gr.update(visible=False),
    )


def do_logout():
    return (
        None,
        gr.update(visible=True),
        gr.update(visible=True),
        gr.update(value="", visible=False),
        gr.update(visible=False),
        gr.update(visible=False),
        gr.update(visible=False),
        gr.update(visible=False),
    )


def do_register(email, first_name, surname, year_of_birth, password, secret_question, secret_answer):
    if not all([
        str(email).strip(), str(first_name).strip(), str(surname).strip(),
        str(password).strip(), secret_question, str(secret_answer).strip(),
    ]):
        return (
            gr.update(value="All fields are required.", visible=True),
            None,
            gr.update(), gr.update(), gr.update(), gr.update(),
            gr.update(visible=True),
        )

    try:
        year = int(year_of_birth)
        if year < 1900 or year > 2015:
            raise ValueError
    except (ValueError, TypeError):
        return (
            gr.update(value="Please enter a valid year of birth (e.g. 1990).", visible=True),
            None,
            gr.update(), gr.update(), gr.update(), gr.update(),
            gr.update(visible=True),
        )

    ok, err = register_user(
        email.strip(), first_name.strip(), surname.strip(),
        year, password, secret_question, secret_answer.strip(),
    )
    if not ok:
        return (
            gr.update(value=err, visible=True),
            None,
            gr.update(), gr.update(), gr.update(), gr.update(),
            gr.update(visible=True),
        )

    display_name = f"{first_name.strip()} {surname.strip()}"
    return (
        gr.update(value="", visible=False),
        email.strip().lower(),
        gr.update(visible=False),
        gr.update(visible=False),
        gr.update(value=f"**Welcome, {display_name}**", visible=True),
        gr.update(visible=True),
        gr.update(visible=False),
    )


def forgot_find_question(email: str):
    if not email.strip():
        return (
            gr.update(value="Please enter your email address.", visible=True),
            gr.update(visible=False),
            gr.update(visible=False),
            gr.update(visible=False),
            gr.update(visible=False),
        )

    question = get_user_secret_question(email.strip())
    if question is None:
        return (
            gr.update(value="No account found with that email address.", visible=True),
            gr.update(visible=False),
            gr.update(visible=False),
            gr.update(visible=False),
            gr.update(visible=False),
        )

    return (
        gr.update(value="", visible=False),
        gr.update(value=f"**Your security question:** {question}", visible=True),
        gr.update(visible=True),
        gr.update(visible=True),
        gr.update(visible=True),
    )


def forgot_reset_password(email: str, answer: str, new_password: str):
    if not str(answer).strip() or not str(new_password).strip():
        return gr.update(value="Please fill in all fields.", visible=True)

    if not verify_secret_answer(email.strip(), answer.strip()):
        return gr.update(value="Incorrect answer. Please try again.", visible=True)

    if not update_password(email.strip(), new_password):
        return gr.update(value="Failed to update password. Please try again.", visible=True)

    return gr.update(value="**Password reset successfully. You can now log in.**", visible=True)


# ── Panel visibility toggles ──────────────────────────────────────────────────

def show_login_panel():
    return gr.update(visible=True), gr.update(visible=False), gr.update(visible=False)

def show_register_panel():
    return gr.update(visible=False), gr.update(visible=True), gr.update(visible=False)

def show_forgot_panel():
    return gr.update(visible=False), gr.update(visible=False), gr.update(visible=True)

def hide_all_panels():
    return gr.update(visible=False), gr.update(visible=False), gr.update(visible=False)


###nini fix
# ── Trip History handler ───────────────────────────────────────────────────────

def load_trip_history(current_user: str):
    """
    Fetch and format the logged-in user's booking history into two DataFrames.
    Returns formatted tables for national rail bookings and metro travels.
    Requires login — returns a message if not logged in.
    """
    if not current_user:
        return (
            gr.update(value=None, visible=False),
            gr.update(value=None, visible=False),
            gr.update(value="⚠️ Please log in to view your trip history.", visible=True),
        )

    data = query_user_bookings(current_user)

    nr = data.get("national_rail", [])
    metro = data.get("metro", [])

    # Format national rail bookings into display rows
    nr_rows = []
    for b in nr:
        nr_rows.append({
            "Booking ID":   b.get("booking_id", ""),
            "Date":         str(b.get("travel_date", "")),
            "From":         b.get("origin_name", ""),
            "To":           b.get("destination_name", ""),
            "Line":         b.get("line", ""),
            "Class":        b.get("fare_class", ""),
            "Seat":         b.get("seat_id", ""),
            "Amount (USD)": f"${b.get('amount_usd', 0):.2f}",
            "Status":       b.get("status", ""),
        })

    # Format metro travels into display rows
    metro_rows = []
    for t in metro:
        metro_rows.append({
            "Trip ID":      t.get("trip_id", ""),
            "Date":         str(t.get("travel_date", "")),
            "From":         t.get("origin_name", ""),
            "To":           t.get("destination_name", ""),
            "Line":         t.get("line", ""),
            "Ticket Type":  t.get("ticket_type", ""),
            "Amount (USD)": f"${t.get('amount_usd', 0):.2f}",
            "Status":       t.get("status", ""),
        })

    import pandas as pd
    nr_df = pd.DataFrame(nr_rows) if nr_rows else pd.DataFrame(columns=["Booking ID","Date","From","To","Line","Class","Seat","Amount (USD)","Status"])
    metro_df = pd.DataFrame(metro_rows) if metro_rows else pd.DataFrame(columns=["Trip ID","Date","From","To","Line","Ticket Type","Amount (USD)","Status"])

    nr_update = gr.update(value=nr_df, visible=True)
    metro_update = gr.update(value=metro_df, visible=True)

    if not nr_rows and not metro_rows:
        msg = gr.update(value="No bookings found for your account.", visible=True)
    else:
        msg = gr.update(value="", visible=False)

    return nr_update, metro_update, msg


# ── Station Lookup handler ─────────────────────────────────────────────────────

def load_station_connections(station_id: str):
    """
    Fetch direct connections for the selected station from Neo4j.
    Formats results into a DataFrame showing neighbour stations and travel times.
    """
    if not station_id:
        return gr.update(value=None, visible=False), gr.update(value="Please select a station.", visible=True)

    try:
        connections = query_station_connections(station_id)
    except Exception as e:
        return gr.update(value=None, visible=False), gr.update(value=f"Error: {e}", visible=True)

    if not connections:
        return (
            gr.update(value=None, visible=False),
            gr.update(value="No connections found for this station.", visible=True),
        )

    import pandas as pd
    rows = []
    for c in connections:
        rows.append({
            "Station ID":          c.get("station_id", c.get("neighbour_id", "")),
            "Station Name":        c.get("name", c.get("station_name", "")),
            "Travel Time (min)":   c.get("travel_time_min", ""),
            "Line":                c.get("line", ""),
            "Network":             c.get("network", ""),
        })

    df = pd.DataFrame(rows)
    return gr.update(value=df, visible=True), gr.update(value="", visible=False)
###nini fix end


# ── Example queries ────────────────────────────────────────────────────────────

EXAMPLES = [
    "What national rail trains run from Central (NR01) to Stonehaven (NR05)?",
    "What is the fastest metro route from MS01 to MS14?",
    "How do I get from Central Square (MS01) to Stonehaven (NR05)?",
    "If Old Town station (NR03) is closed, what alternative routes exist from NR01 to NR05?",
    "My train was delayed 45 minutes — what compensation am I entitled to?",
    "What is the company policy on travelling with a bicycle on national rail?",
]


###nini fix
# ── Custom CSS for enhanced UI aesthetics ─────────────────────────────────────
CUSTOM_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Syne:wght@400;600;700;800&family=DM+Sans:ital,wght@0,300;0,400;0,500;1,300&display=swap');

:root {
    --tf-navy:      #0f1b2d;
    --tf-blue:      #1a3a5c;
    --tf-accent:    #e8622a;
    --tf-gold:      #f0a500;
    --tf-light:     #f4f6f8;
    --tf-white:     #ffffff;
    --tf-muted:     #6b7c93;
    --tf-border:    #dce3ec;
    --tf-success:   #2e7d52;
    --tf-radius:    10px;
    --tf-shadow:    0 4px 24px rgba(15,27,45,0.10);
}

body, .gradio-container {
    font-family: 'DM Sans', sans-serif !important;
    background: var(--tf-light) !important;
}

/* Header */
.tf-header {
    background: linear-gradient(135deg, var(--tf-navy) 0%, var(--tf-blue) 100%);
    border-radius: var(--tf-radius);
    padding: 28px 32px 20px 32px;
    margin-bottom: 8px;
    box-shadow: var(--tf-shadow);
    position: relative;
    overflow: hidden;
}
.tf-header::before {
    content: '';
    position: absolute;
    top: -40px; right: -40px;
    width: 180px; height: 180px;
    border-radius: 50%;
    background: rgba(232,98,42,0.13);
    pointer-events: none;
}
.tf-header h1 {
    font-family: 'Syne', sans-serif !important;
    font-size: 2rem !important;
    font-weight: 800 !important;
    color: var(--tf-white) !important;
    letter-spacing: -0.5px;
    margin: 0 0 4px 0 !important;
}
.tf-header p {
    color: rgba(255,255,255,0.65) !important;
    font-size: 0.92rem !important;
    margin: 0 !important;
    font-weight: 300;
    letter-spacing: 0.3px;
}

/* Tab styling */
.tab-nav button {
    font-family: 'Syne', sans-serif !important;
    font-weight: 600 !important;
    font-size: 0.88rem !important;
    letter-spacing: 0.4px;
    text-transform: uppercase;
    border-radius: 8px 8px 0 0 !important;
    padding: 10px 20px !important;
    color: var(--tf-muted) !important;
    border: none !important;
    background: transparent !important;
    transition: all 0.2s ease;
}
.tab-nav button.selected {
    color: var(--tf-accent) !important;
    border-bottom: 3px solid var(--tf-accent) !important;
    background: var(--tf-white) !important;
}

/* Buttons */
button.primary {
    background: var(--tf-accent) !important;
    border: none !important;
    border-radius: var(--tf-radius) !important;
    font-family: 'Syne', sans-serif !important;
    font-weight: 700 !important;
    letter-spacing: 0.3px;
    transition: all 0.18s ease;
    box-shadow: 0 2px 8px rgba(232,98,42,0.18);
}
button.primary:hover {
    background: #c94e1e !important;
    transform: translateY(-1px);
    box-shadow: 0 4px 14px rgba(232,98,42,0.28);
}
button.secondary {
    border: 1.5px solid var(--tf-border) !important;
    border-radius: var(--tf-radius) !important;
    font-family: 'DM Sans', sans-serif !important;
    color: var(--tf-blue) !important;
    background: var(--tf-white) !important;
    transition: all 0.15s ease;
}
button.secondary:hover {
    border-color: var(--tf-accent) !important;
    color: var(--tf-accent) !important;
}

/* Chatbot */
.chatbot {
    border-radius: var(--tf-radius) !important;
    border: 1.5px solid var(--tf-border) !important;
    background: var(--tf-white) !important;
    box-shadow: var(--tf-shadow) !important;
}
.chatbot .message.user {
    background: linear-gradient(135deg, var(--tf-navy), var(--tf-blue)) !important;
    color: white !important;
    border-radius: 16px 16px 4px 16px !important;
}
.chatbot .message.bot {
    background: var(--tf-light) !important;
    border: 1px solid var(--tf-border) !important;
    border-radius: 16px 16px 16px 4px !important;
}

/* Textbox */
input[type=text], input[type=password], textarea {
    border-radius: var(--tf-radius) !important;
    border: 1.5px solid var(--tf-border) !important;
    font-family: 'DM Sans', sans-serif !important;
    font-size: 0.95rem !important;
    transition: border-color 0.15s ease;
}
input[type=text]:focus, input[type=password]:focus, textarea:focus {
    border-color: var(--tf-accent) !important;
    box-shadow: 0 0 0 3px rgba(232,98,42,0.10) !important;
}

/* Sidebar */
.sidebar-card {
    background: var(--tf-white);
    border-radius: var(--tf-radius);
    border: 1.5px solid var(--tf-border);
    padding: 16px;
    margin-bottom: 12px;
    box-shadow: 0 2px 8px rgba(15,27,45,0.05);
}
.sidebar-card h3 {
    font-family: 'Syne', sans-serif !important;
    font-size: 0.82rem !important;
    font-weight: 700 !important;
    text-transform: uppercase;
    letter-spacing: 1px;
    color: var(--tf-muted) !important;
    margin-bottom: 10px !important;
}

/* DataFrames */
table {
    font-family: 'DM Sans', sans-serif !important;
    font-size: 0.9rem !important;
}
th {
    background: var(--tf-navy) !important;
    color: var(--tf-white) !important;
    font-family: 'Syne', sans-serif !important;
    font-weight: 600 !important;
    letter-spacing: 0.3px;
    padding: 10px 14px !important;
}
tr:nth-child(even) { background: var(--tf-light) !important; }
tr:hover { background: rgba(232,98,42,0.06) !important; }

/* Auth panels */
.auth-panel {
    background: var(--tf-white);
    border: 1.5px solid var(--tf-border);
    border-radius: var(--tf-radius);
    padding: 24px;
    box-shadow: var(--tf-shadow);
    margin-bottom: 12px;
}

/* Example buttons */
.example-btn button {
    font-size: 0.82rem !important;
    text-align: left !important;
    white-space: normal !important;
    height: auto !important;
    padding: 8px 12px !important;
    border-left: 3px solid var(--tf-accent) !important;
    border-radius: 0 8px 8px 0 !important;
    background: var(--tf-white) !important;
    color: var(--tf-blue) !important;
    margin-bottom: 4px !important;
    line-height: 1.4 !important;
    transition: all 0.15s ease;
}
.example-btn button:hover {
    background: rgba(232,98,42,0.07) !important;
    transform: translateX(3px);
}

/* Status badges */
.status-confirmed { color: var(--tf-success) !important; font-weight: 600; }
.status-cancelled { color: var(--tf-accent) !important; font-weight: 600; }

/* Trip history section headers */
.history-section-title {
    font-family: 'Syne', sans-serif !important;
    font-size: 1rem !important;
    font-weight: 700 !important;
    color: var(--tf-navy) !important;
    border-left: 4px solid var(--tf-accent);
    padding-left: 10px;
    margin: 16px 0 8px 0;
}
"""
###nini fix end


# ── Build UI ───────────────────────────────────────────────────────────────────

###nini fix
with gr.Blocks(
    title="TransitFlow",
    theme=gr.themes.Base(
        primary_hue=gr.themes.colors.orange,
        neutral_hue=gr.themes.colors.slate,
        font=[gr.themes.GoogleFont("DM Sans"), "sans-serif"],
        font_mono=[gr.themes.GoogleFont("JetBrains Mono"), "monospace"],
    ),
    css=CUSTOM_CSS,
) as demo:
###nini fix end

    # ── Hidden state ──────────────────────────────────────────────────
    agent_history_state = gr.State([])
    current_user_state  = gr.State(None)

    ###nini fix
    # ── Header ────────────────────────────────────────────────────────
    with gr.Row():
        with gr.Column(scale=3):
            gr.HTML("""
            <div class="tf-header">
                <h1>🚂 TransitFlow</h1>
                <p>Intelligent Rail & Metro Assistant &nbsp;·&nbsp; Powered by PostgreSQL · pgvector · Neo4j · LLM</p>
            </div>
            """)
        with gr.Column(scale=1, min_width=220):
            with gr.Group():
                with gr.Row():
                    login_btn    = gr.Button("👤 Login",    size="sm", variant="secondary")
                    register_btn = gr.Button("📝 Register", size="sm", variant="secondary")
                user_info_display = gr.Markdown("", visible=False)
                logout_btn = gr.Button("🚪 Logout", size="sm", variant="stop", visible=False)
    ###nini fix end

    # ── Auth panels ──────────────────────────────────────────────────
    with gr.Column(visible=False) as login_panel:
        gr.Markdown("### 🔐 Login")
        login_email_in    = gr.Textbox(label="Email", placeholder="you@example.com")
        login_password_in = gr.Textbox(label="Password", type="password")
        login_error_msg   = gr.Markdown("", visible=False)
        with gr.Row():
            login_submit_btn = gr.Button("Login", variant="primary")
            forgot_link_btn  = gr.Button("Forgot password?", size="sm")
            login_cancel_btn = gr.Button("Cancel", size="sm")

    with gr.Column(visible=False) as register_panel:
        gr.Markdown("### 📝 Create an Account")
        with gr.Row():
            reg_first_name_in = gr.Textbox(label="First name")
            reg_surname_in    = gr.Textbox(label="Surname")
        reg_email_in    = gr.Textbox(label="Email", placeholder="you@example.com")
        reg_year_in     = gr.Textbox(label="Year of birth", placeholder="e.g. 1990")
        reg_password_in = gr.Textbox(label="Password", type="password")
        reg_question_in = gr.Dropdown(choices=SECRET_QUESTIONS, label="Security question")
        reg_answer_in   = gr.Textbox(label="Secret answer")
        reg_error_msg   = gr.Markdown("", visible=False)
        with gr.Row():
            reg_submit_btn = gr.Button("Register", variant="primary")
            reg_cancel_btn = gr.Button("Cancel", size="sm")

    with gr.Column(visible=False) as forgot_panel:
        gr.Markdown("### 🔑 Reset Your Password")
        forgot_email_in          = gr.Textbox(label="Email address", placeholder="you@example.com")
        forgot_check_btn         = gr.Button("Find my question", variant="secondary")
        forgot_question_display  = gr.Markdown("", visible=False)
        forgot_answer_in         = gr.Textbox(label="Your answer", visible=False)
        forgot_new_password_in   = gr.Textbox(label="New password", type="password", visible=False)
        forgot_reset_btn         = gr.Button("Reset password", variant="primary", visible=False)
        forgot_msg               = gr.Markdown("")
        forgot_back_btn          = gr.Button("← Back to login", size="sm")

    ###nini fix
    # ── Main Tabs: Chat / Trip History / Station Lookup ───────────────
    with gr.Tabs():

        # ── Tab 1: Chat ───────────────────────────────────────────────
        with gr.TabItem("💬 Assistant"):
            with gr.Row():

                # Left: chat
                with gr.Column(scale=3):
                    chatbot = gr.Chatbot(
                        label="TransitFlow Assistant",
                        height=440,
                        show_label=False,
                        avatar_images=(None, "https://api.dicebear.com/7.x/bottts/svg?seed=transitflow"),
                    )
                    with gr.Row():
                        msg = gr.Textbox(
                            placeholder="Ask e.g. 'Fastest route from MS01 to MS14?' or 'What's the refund policy?'",
                            show_label=False,
                            scale=5,
                            lines=1,
                        )
                        send_btn = gr.Button("Send ➤", variant="primary", scale=1)

                    with gr.Row():
                        clear_btn    = gr.Button("🗑️ Clear", size="sm")
                        debug_toggle = gr.Checkbox(label="🔍 Debug panel", value=True)

                    debug_panel = gr.Markdown(value="", visible=False)

                # Right: sidebar
                with gr.Column(scale=1):
                    with gr.Group(elem_classes=["sidebar-card"]):
                        gr.Markdown("### 🤖 LLM Provider")
                        chat_model_dropdown = gr.Dropdown(
                            choices=get_chat_model_choices(),
                            value=get_initial_chat_model_value(),
                            label="Chat model",
                            info="Local Ollama or Gemini cloud.",
                        )
                        provider_status = gr.Markdown(value="**Active:** llama3.2:1b")
                        ollama_status   = gr.Markdown(value=get_ollama_status())

                    with gr.Group(elem_classes=["sidebar-card"]):
                        gr.Markdown("### 💡 Example Queries")
                        for example in EXAMPLES:
                            with gr.Row(elem_classes=["example-btn"]):
                                gr.Button(example, size="sm").click(
                                    fn=lambda e=example: e,
                                    outputs=msg,
                                )

        # ── Tab 2: Trip History ────────────────────────────────────────
        with gr.TabItem("🎫 My Trip History"):
            gr.Markdown("""
## Your Booking History
View all your national rail bookings and metro travels in one place.
*You must be logged in to view your trip history.*
            """)

            load_history_btn = gr.Button("🔄 Load My Trip History", variant="primary", size="lg")
            history_msg = gr.Markdown("", visible=False)

            gr.Markdown("### 🚂 National Rail Bookings", elem_classes=["history-section-title"])
            nr_history_table = gr.DataFrame(
                headers=["Booking ID", "Date", "From", "To", "Line", "Class", "Seat", "Amount (USD)", "Status"],
                label=None,
                visible=False,
                wrap=True,
                interactive=False,
            )

            gr.Markdown("### 🚇 Metro Travels", elem_classes=["history-section-title"])
            metro_history_table = gr.DataFrame(
                headers=["Trip ID", "Date", "From", "To", "Line", "Ticket Type", "Amount (USD)", "Status"],
                label=None,
                visible=False,
                wrap=True,
                interactive=False,
            )

        # ── Tab 3: Station Lookup ──────────────────────────────────────
        with gr.TabItem("🗺️ Station Lookup"):
            gr.Markdown("""
## Station Connection Lookup
Select any station to instantly see all directly connected stations,
travel times, and which lines serve each connection.
            """)

            with gr.Row():
                with gr.Column(scale=2):
                    station_dropdown = gr.Dropdown(
                        choices=STATION_CHOICES,
                        label="Select a Station",
                        info="Choose any Metro (MS) or National Rail (NR) station",
                        interactive=True,
                    )
                with gr.Column(scale=1):
                    lookup_btn = gr.Button("🔍 Show Connections", variant="primary", size="lg")

            station_msg = gr.Markdown("", visible=False)
            connections_table = gr.DataFrame(
                headers=["Station ID", "Station Name", "Travel Time (min)", "Line", "Network"],
                label="Direct Connections",
                visible=False,
                wrap=True,
                interactive=False,
            )
    ###nini fix end

    # ── Event wiring ──────────────────────────────────────────────────

    chat_model_dropdown.change(
        fn=on_chat_model_change,
        inputs=chat_model_dropdown,
        outputs=[provider_status, ollama_status],
    )

    send_btn.click(
        fn=chat,
        inputs=[msg, chatbot, agent_history_state, debug_toggle, current_user_state],
        outputs=[chatbot, agent_history_state, debug_panel],
    ).then(fn=lambda: "", outputs=msg)

    msg.submit(
        fn=chat,
        inputs=[msg, chatbot, agent_history_state, debug_toggle, current_user_state],
        outputs=[chatbot, agent_history_state, debug_panel],
    ).then(fn=lambda: "", outputs=msg)

    clear_btn.click(
        fn=clear_conversation,
        outputs=[chatbot, agent_history_state, debug_panel],
    )

    ###nini fix
    # Trip history button
    load_history_btn.click(
        fn=load_trip_history,
        inputs=[current_user_state],
        outputs=[nr_history_table, metro_history_table, history_msg],
    )

    # Station lookup button
    lookup_btn.click(
        fn=load_station_connections,
        inputs=[station_dropdown],
        outputs=[connections_table, station_msg],
    )
    ###nini fix end

    # Panel toggle buttons
    login_btn.click(fn=show_login_panel, outputs=[login_panel, register_panel, forgot_panel])
    register_btn.click(fn=show_register_panel, outputs=[login_panel, register_panel, forgot_panel])
    login_cancel_btn.click(fn=hide_all_panels, outputs=[login_panel, register_panel, forgot_panel])
    reg_cancel_btn.click(fn=hide_all_panels, outputs=[login_panel, register_panel, forgot_panel])
    forgot_link_btn.click(fn=show_forgot_panel, outputs=[login_panel, register_panel, forgot_panel])
    forgot_back_btn.click(fn=show_login_panel, outputs=[login_panel, register_panel, forgot_panel])

    login_submit_btn.click(
        fn=do_login,
        inputs=[login_email_in, login_password_in],
        outputs=[
            login_error_msg, current_user_state,
            login_btn, register_btn, user_info_display,
            logout_btn, login_panel,
        ],
    )

    logout_btn.click(
        fn=do_logout,
        outputs=[
            current_user_state, login_btn, register_btn,
            user_info_display, logout_btn,
            login_panel, register_panel, forgot_panel,
        ],
    )

    reg_submit_btn.click(
        fn=do_register,
        inputs=[
            reg_email_in, reg_first_name_in, reg_surname_in,
            reg_year_in, reg_password_in, reg_question_in, reg_answer_in,
        ],
        outputs=[
            reg_error_msg, current_user_state,
            login_btn, register_btn, user_info_display,
            logout_btn, register_panel,
        ],
    )

    forgot_check_btn.click(
        fn=forgot_find_question,
        inputs=[forgot_email_in],
        outputs=[
            forgot_msg, forgot_question_display,
            forgot_answer_in, forgot_new_password_in, forgot_reset_btn,
        ],
    )

    forgot_reset_btn.click(
        fn=forgot_reset_password,
        inputs=[forgot_email_in, forgot_answer_in, forgot_new_password_in],
        outputs=[forgot_msg],
    )


if __name__ == "__main__":
    demo.launch(
        server_name="0.0.0.0",
        server_port=7860,
        share=False,
    )