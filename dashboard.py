#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════
THE INSTITUTION — OPERATIONS CENTER (DASHBOARD)
═══════════════════════════════════════════════════════════════
Full Flask operations dashboard:
- Overview: revenue, runway, health, agents, compliance
- Per-stream panels: unit economics, kill criteria, sparklines
- Agent workforce: tasks, error rates, heartbeats
- Oracle: prediction calibration, active predictions, scenarios
- Governance: constitutional audits, approval queue
- Human interface: check-in form, one-action, emergency stop
Dark theme. Mobile-responsive. Auto-refresh 60s. No CDN.
═══════════════════════════════════════════════════════════════
"""

import os
import json
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from functools import wraps

from flask import Flask, request, jsonify, session, redirect, url_for, render_template_string, Response

from common import get_db, get_config, get_logger, INSTITUTION_ROOT, now_iso, today_str, safe_json_loads
from constitutional_court import get_court
from agents.security_buffer import SecurityBufferAgent

logger = get_logger("dashboard")

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "institution-change-me")

db = get_db()
config = get_config()
court = get_court()
security_buffer = SecurityBufferAgent()

DASH_USER = os.environ.get("DASHBOARD_USER", "founder")
DASH_PASS = os.environ.get("DASHBOARD_PASS", "")
AUTH_ENABLED = bool(DASH_PASS)

STOP_MARKER = INSTITUTION_ROOT / "STOP"


# ─── AUTH ─────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if AUTH_ENABLED and not session.get("authed"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "unauthorized"}), 401
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


@app.route("/login", metho
ds=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        if request.form.get("user") == DASH_USER and request.form.get("pass") == DASH_PASS:
            session["authed"] = True
            session.permanent = True
            app.permanent_session_lifetime = timedelta(hours=24)
            return redirect(url_for("index"))
        error = "Wrong credentials."
    return render_template_string(LOGIN_TEMPLATE, error=error)


@app.route("/logout")
def logout():
    session.pop("authed", None)
    return redirect(url_for("login"))


# ─── DATA GATHERING ───────────────────────────────────────────
def sparkline_svg(values, width=120, height=32, color="#3fb950"):
    """Render an inline SVG sparkline."""
    if not values or all(v == 0 for v in values):
        return f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}"><line x1="0" y1="{height-2}" x2="{width}" y2="{height-2}" stroke="#2d333b" stroke-width="2"/></svg>'
    max_v = max(values) or 1
    n = len(values)
    step = width / max(n - 1, 1)
    points = []
    for i, v in enumerate(values):
        x = i * step
        y = height - 3 - (v / max_v) * (height - 8)
        points.append(f"{x:.1f},{y:.1f}")
    poly = " ".join(points)
    area = f"0,{height} {poly} {width},{height}"
    return (
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" class="sparkline">'
        f'<polygon points="{area}" fill="{color}" opacity="0.12"/>'
        f'<polyline points="{poly}" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'
        f'</svg>'
    )


def stream_sparkline(slug):
    """Last 7 days revenue for a stream."""
    rows = db.fetchall(
        """SELECT DATE(recorded_at) as d, COALESCE(SUM(amount),0) as s
           FROM revenue WHERE stream = ? AND recorded_at > datetime('now','-7 days')
           GROUP BY d ORDER BY d""",
        (slug,)
    )
    by_day = {r["d"]: r["s"] for r in rows}

    values = []
    for i in range(6, -1, -1):
        d = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        values.append(by_day.get(d, 0))
    return values


def calibration_data():
    """Compute prediction calibration buckets."""
    rows = db.fetchall(
        "SELECT predicted_confidence, error_magnitude FROM predictions WHERE error_magnitude IS NOT NULL"
    )
    buckets = {}
    for r in rows:
        b = (r["predicted_confidence"] // 20) * 20
        buckets.setdefault(b, []).append(r["error_magnitude"])
    result = []
    for b in sorted(buckets):
        errs = buckets[b]
        avg_err = sum(errs) / len(errs)
        result.append({
            "bucket": f"{b}-{b+19}%",
            "count": len(errs),
            "accuracy": round(max(0, 100 - avg_err), 1),
        })
    return result


def service_status(name):
    try:
        r = subprocess.run(["systemctl", "is-active", name], capture_output=True, text=True, timeout=5)
        return r.stdout.strip()
    except Exception:
        return "unknown"


def compute_single_action(energy, pain, approvals, ranked_actions):
    """The ONE action for today, adapted to founder state."""
    if energy is None:
        return {"text": "Check in below — tell the Institution how you are. That's the only task.", "minutes": 1, "type": "checkin"}
    if energy <= 1 or pain <= 1:
        return {"text": "Rest. The Institution is running. No action needed today. We've got this.", "minutes": 0, "type": "rest"}
    if ranked_actions:
        top = ranked_actions[0]
        if energy == 2 and top["time_minutes"] > 5:
            return {"text": "No tiny tasks available. Rest or browse the dashboard — no pressure.", "minutes": 0, "type": "rest"}
        return {
            "text": top["description"],
            "minutes": top["time_minutes"],
            "expected_runway": top["expected_runway_days"],
            "risk": top["risk"],
            "kind": top["kind"],
            "id": top["id"],
      
      "type": "action",
        }
    if energy == 2:
        return {"text": "Nothing needs you. The machine is working. Maybe glance at the digest.", "minutes": 2, "type": "optional"}
    return {"text": "System is autonomous. Optional: review the weekly report for strategic insight.", "minutes": 10, "type": "optional"}


def gather_status():
    """Gather all dashboard data into one dict."""
    now = datetime.now()

    # ── Overview ──
    rev_all = db.fetchone("SELECT COALESCE(SUM(amount),0) as t FROM revenue")
    rev_month = db.fetchone("SELECT COALESCE(SUM(amount),0) as t FROM revenue WHERE recorded_at > datetime('now','start of month')")
    runway = security_buffer.calculate_runway()
    history = security_buffer.get_runway_history(30)
    trend = security_buffer.record_runway_snapshot()

    agents = db.get_all_agents()
    active_agents = [a for a in agents if a.get("status") in ("idle", "running")]

    metrics = db.get_latest_metrics()
    checkin = db.get_latest_checkin()
    audit_stats = court.get_violation_stats()

    halted = STOP_MARKER.exists()

    # ── Streams ──
    streams = db.fetchall("SELECT * FROM streams ORDER BY name")
    stream_panels = []
    for s in streams:
        rev30 = db.fetchone("SELECT COALESCE(SUM(amount),0) as t FROM revenue WHERE stream=? AND recorded_at > datetime('now','-30 days')", (s["slug"],))
        ai_calls = db.fetchone("SELECT COUNT(*) as c, COALESCE(SUM(total_tokens),0) as tok FROM ai_usage WHERE stream=? AND created_at > datetime('now','-30 days')", (s["slug"],))
        agents_on = db.fetchall("SELECT name, status, current_task FROM agents WHERE stream=?", (s["slug"],))

        # Kill criterion progress
        kill_progress = 0
        kill_label = "—"
        if s.get("kill_window_days"):
            created = s.get("created_at")
            if created:
                try:
                    age_days = (now - datetime.fromisoformat(created)).days
                    kill_progress = min(100, int(age_d
ays / s["kill_window_days"] * 100))
                    kill_label = f"Day {age_days}/{s['kill_window_days']}"
                except (ValueError, TypeError):
                    pass

        # Latest prediction vs actual
        pred = db.fetchone("SELECT * FROM predictions WHERE stream=? ORDER BY created_at DESC LIMIT 1", (s["slug"],))

        # Unit economics
        calls = ai_calls["c"] if ai_calls else 0
        tokens = ai_calls["tok"] if ai_calls else 0
        revenue_30 = rev30["t"] if rev30 else 0
        margin = 100.0  # Free tier → ~100% margin on AI spend
        effective_hourly = revenue_30 / max(1, 4)  # assume ~4 founder-hours/month oversight

        stream_panels.append({
            "name": s["name"],
            "slug": s["slug"],
            "status": s["status"],
            "autonomy": s.get("autonomy_level", 1),
            "revenue_total": s.get("revenue_total", 0),
            "revenue_month": rev_month_row(s["slug"]),
            "revenue_30d": revenue_30,
            "ai_calls_30d": calls,
            "tokens_30d": tokens,
            "margin": margin,
            "effective_hourly": round(effective_hourly, 2),
            "kill_criterion": s.get("kill_criterion", "—"),
            "kill_progress": kill_progress,
            "kill_label": kill_label,
            "agents": agents_on,
            "sparkline": sparkline_svg(stream_sparkline(s["slug"]), color=stream_color(s["status"])),
            "prediction": pred,
        })

    # ── Agent workforce ──
    agent_rows = []
    for a in agents:
        hb = a.get("last_heartbeat")
        stale = False
        hb_age = "never"
        if hb:
            try:
                delta = now - datetime.fromisoformat(hb)
                stale = delta > timedelta(minutes=15)
                hb_age = humanize_delta(delta)
            except (ValueError, TypeError):
                stale = True
        total_tasks = (a.get("tasks_completed", 0) or 0) + (a.get("tasks_failed", 0) or 0)
        er
ror_rate = round((a.get("tasks_failed", 0) or 0) / total_tasks * 100, 1) if total_tasks else 0.0
        agent_rows.append({
            **a,
            "stale": stale,
            "hb_age": hb_age,
            "error_rate": error_rate,
        })

    # ── Oracle ──
    active_preds = db.fetchall("SELECT * FROM predictions WHERE actual_value IS NULL ORDER BY created_at DESC LIMIT 8")
    cal = calibration_data()
    lessons = db.get_lessons(limit=5)

    # Recent scenario simulations (from approvals with oracle_simulation)
    sim_rows = db.fetchall("SELECT details, description FROM approvals WHERE details LIKE '%oracle_simulation%' ORDER BY created_at DESC LIMIT 4")
    simulations = []
    for sr in sim_rows:
        d = safe_json_loads(sr["details"], {})
        sim = d.get("oracle_simulation")
        if sim:
            simulations.append({"description": sr["description"], "sim": sim})

    # ── Governance ──
    recent_audits = db.fetchall("SELECT * FROM constitutional_audits ORDER BY created_at DESC LIMIT 8")
    approvals = db.get_pending_approvals()
    decisions = db.fetchall("SELECT * FROM decisions ORDER BY created_at DESC LIMIT 6")

    # ── Ranked actions & single action ──
    ranked = security_buffer.rank_actions_by_runway_impact()
    energy = checkin["energy"] if checkin else None
    pain = checkin["pain"] if checkin else None
    single_action = compute_single_action(energy, pain, approvals, ranked)

    # ── Milestones ──
    milestones = db.fetchall("SELECT * FROM decisions WHERE description LIKE 'MILESTONE:%' ORDER BY created_at DESC LIMIT 3")

    # ── Services ──
    services = {
        "meta-agent": service_status("institution.service"),
        "dashboard": service_status("institution-dashboard.service"),
        "safety": service_status("institution-safety.service"),
    }

    return {
        "now": now.strftime("%A, %B %d, %Y · %H:%M"),
        "halted": halted,
        "overview": {
            "revenue_total": rev_all["t"] if rev_
all else 0,
            "revenue_month": rev_month["t"] if rev_month else 0,
            "runway": runway,
            "trend": trend,
            "runway_history": history,
            "active_agents": len(active_agents),
            "total_agents": len(agents),
            "metrics": metrics,
            "checkin": checkin,
            "compliance": audit_stats,
            "services": services,
        },
        "streams": stream_panels,
        "agents": agent_rows,
        "oracle": {
            "active_predictions": active_preds,
            "calibration": cal,
            "lessons": lessons,
            "simulations": simulations,
        },
        "governance": {
            "recent_audits": recent_audits,
            "approvals": approvals,
            "decisions": decisions,
            "audit_stats": audit_stats,
        },
        "single_action": single_action,
        "milestones": milestones,
    }


def rev_month_row(slug):
    r = db.fetchone("SELECT COALESCE(SUM(amount),0) as t FROM revenue WHERE stream=? AND recorded_at > datetime('now','start of month')", (slug,))
    return r["t"] if r else 0


def stream_color(status):
    return {"active": "#3fb950", "paused": "#d29922", "killed": "#f85149", "pending": "#8b949e"}.get(status, "#8b949e")


def humanize_delta(delta):
    s = int(delta.total_seconds())
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s//60}m ago"
    if s < 86400:
        return f"{s//3600}h ago"
    return f"{s//86400}d ago"


# ─── API ROUTES ───────────────────────────────────────────────
@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "institution-dashboard", "time": now_iso()})


@app.route("/api/status")
@login_required
def api_status():
    return jsonify(gather_status())


@app.route("/api/checkin", methods=["POST"])
@login_required
def api_checkin():
    data = request.get_json(force=True)
    try:
        energy = int(data.get("energy", 3))
        pain = 
int(data.get("pain", 3))
        fear = int(data.get("fear", 3))
        minutes = int(data.get("minutes", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "invalid values"}), 400
    energy = max(1, min(5, energy))
    pain = max(1, min(5, pain))
    fear = max(1, min(5, fear))
    db.record_checkin(energy, pain, fear, minutes, data.get("notes"))
    logger.info(f"Founder check-in: energy={energy} pain={pain} fear={fear} minutes={minutes}")
    return jsonify({"ok": True, "message": "Check-in recorded. The Institution adapts."})


@app.route("/api/approval/<int:approval_id>/resolve", methods=["POST"])
@login_required
def api_resolve_approval(approval_id):
    data = request.get_json(force=True)
    status = data.get("status")
    if status not in ("approved", "rejected"):
        return jsonify({"error": "status must be approved or rejected"}), 400
    db.resolve_approval(approval_id, status)
    logger.info(f"Approval #{approval_id} {status} by founder")
    return jsonify({"ok": True, "id": approval_id, "status": status})


@app.route("/api/emergency-stop", methods=["POST"])
@login_required
def api_emergency_stop():
    """Emergency stop: halt the meta-agent and mark the Institution as halted."""
    STOP_MARKER.write_text(f"Emergency stop activated by founder at {now_iso()}\n", encoding="utf-8")
    db.log_incident("critical", "dashboard", "EMERGENCY STOP activated by founder. All autonomous work halted.")
    # Pause all agents in DB
    db.execute("UPDATE agents SET status='paused', current_task=NULL")
    # Stop the meta-agent service (best effort)
    try:
        subprocess.run(["sudo", "systemctl", "stop", "institution.service"], capture_output=True, timeout=30)
    except Exception as e:
        logger.warning(f"Could not stop systemd service: {e}")
    logger.critical("EMERGENCY STOP activated.")
    return jsonify({"ok": True, "message": "Institution halted. Remove the STOP file and restart to resume."})


@app.route("/api/resume
", methods=["POST"])
@login_required
def api_resume():
    """Resume after emergency stop."""
    if STOP_MARKER.exists():
        STOP_MARKER.unlink()
    db.log_incident("info", "dashboard", "Institution resumed by founder.")
    try:
        subprocess.run(["sudo", "systemctl", "start", "institution.service"], capture_output=True, timeout=30)
    except Exception as e:
        logger.warning(f"Could not start systemd service: {e}")
    return jsonify({"ok": True, "message": "Institution resumed."})


@app.route("/manual")
@login_required
def manual():
    """Serve the Stewardship Manual."""
    manual_path = INSTITUTION_ROOT / "stewardship_manual.md"
    text = manual_path.read_text(encoding="utf-8") if manual_path.exists() else "Manual not found."
    try:
        import markdown
        html = markdown.markdown(text, extensions=["tables", "fenced_code"])
    except ImportError:
        html = "<pre>" + text + "</pre>"
    return render_template_string(MANUAL_TEMPLATE, content=html)


# ─── MAIN ROUTE ───────────────────────────────────────────────
@app.route("/")
@login_required
def index():
    status = gather_status()
    return render_template_string(DASHBOARD_TEMPLATE, **status, tojson=json.dumps)


# ─── TEMPLATES ────────────────────────────────────────────────
LOGIN_TEMPLATE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>The Institution — Sign in</title>
<style>
:root{--bg:#0d1219;--panel:#161c26;--line:#2a3242;--text:#e6edf3;--dim:#8b949e;--accent:#3fb950;--amber:#d29922}
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--text);font-family:system-ui,-apple-system,"Segoe UI",sans-serif;min-height:100vh;display:flex;align-items:center;justify-content:center;
background-image:radial-gradient(ellipse 80% 50% at 20% -10%,rgba(63,185,80,.07),transparent),radial-gradient(ellipse 60% 40% at 90% 110%,rgba(210,153,34,.06),transparent)}
.card
{background:var(--panel);border:1px solid var(--line);border-top:3px solid var(--accent);padding:2.5rem;width:min(400px,90vw);border-radius:8px;box-shadow:0 20px 60px rgba(0,0,0,.5)}
.kicker{font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;font-size:.7rem;letter-spacing:.25em;text-transform:uppercase;color:var(--accent);margin-bottom:.5rem}
h1{font-size:1.6rem;font-weight:800;letter-spacing:-.02em;margin-bottom:1.5rem}
label{display:block;font-size:.8rem;color:var(--dim);margin:1rem 0 .3rem}
input{width:100%;padding:.7rem .9rem;background:#0d1219;border:1px solid var(--line);border-radius:6px;color:var(--text);font-size:1rem;font-family:inherit}
input:focus{outline:none;border-color:var(--accent)}
button{width:100%;margin-top:1.5rem;padding:.8rem;background:var(--accent);color:#0d1219;border:none;border-radius:6px;font-size:1rem;font-weight:700;cursor:pointer;transition:transform .15s,box-shadow .15s}
button:hover{transform:translateY(-1px);box-shadow:0 6px 20px rgba(63,185,80,.3)}
.err{color:#f85149;font-size:.85rem;margin-top:1rem}
</style></head><body>
<div class="card">
<div class="kicker">The Institution</div>
<h1>Operations Center</h1>
<form method="post">
<label>Username</label><input name="user" autocomplete="username" required>
<label>Passphrase</label><input name="pass" type="password" autocomplete="current-password" required>
<button type="submit">Enter</button>
</form>
{% if error %}<div class="err">{{ error }}</div>{% endif %}
</div>
</body></html>"""


MANUAL_TEMPLATE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Stewardship Manual — The Institution</title>
<style>
:root{--bg:#0d1219;--panel:#161c26;--line:#2a3242;--text:#e6edf3;--dim:#8b949e;--accent:#3fb950}
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--text);font-family:system-ui,-apple-system,"Segoe UI",sans-serif;line-height:1.7}
.wrap{max-width:760px;margin:
0 auto;padding:3rem 1.5rem}
a.top{color:var(--accent);font-family:ui-monospace,monospace;font-size:.85rem;text-decoration:none}
h1,h2,h3{letter-spacing:-.02em;margin:1.8rem 0 .6rem}
h1{font-size:2rem;font-weight:800;border-bottom:2px solid var(--accent);padding-bottom:.5rem}
h2{font-size:1.4rem;font-weight:700;color:var(--accent)}
p,li{color:#c9d1d9;margin-bottom:.6rem}
code{background:#0d1219;border:1px solid var(--line);padding:.15rem .4rem;border-radius:4px;font-family:ui-monospace,monospace;font-size:.85em;color:#79c0ff}
pre{background:#0d1219;border:1px solid var(--line);border-left:3px solid var(--accent);padding:1rem;border-radius:6px;overflow-x:auto;margin:1rem 0}
pre code{border:none;background:none}
table{border-collapse:collapse;width:100%;margin:1rem 0}
th,td{border:1px solid var(--line);padding:.5rem .8rem;text-align:left;font-size:.9rem}
th{background:var(--panel)}
</style></head><body>
<div class="wrap">
<a class="top" href="/">&larr; back to dashboard</a>
{{ content|safe }}
</div>
</body></html>"""


DASHBOARD_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>The Institution — Operations Center</title>
<style>
:root{
--bg:#0d1219;--bg2:#101722;--panel:#161c26;--panel2:#1a2230;--line:#2a3242;--line2:#343e52;
--text:#e6edf3;--dim:#8b949e;--faint:#5c6570;
--green:#3fb950;--amber:#d29922;--red:#f85149;--blue:#58a6ff;--purple:#bc8cff;--cyan:#39c5cf;
--mono:ui-monospace,"SF Mono","Cascadia Code",Menlo,Consolas,monospace;
--body:system-ui,-apple-system,"Segoe UI",Helvetica,sans-serif;
}
*{margin:0;padding:0;box-sizing:border-box}
html{scroll-behavior:smooth}
body{background:var(--bg);color:var(--text);font-family:var(--body);font-size:15px;line-height:1.5;
background-image:
radial-gradient(ellipse 70% 45% at 15% -5%,rgba(63,185,80,.06),transparent),
radial-gradient(ellipse 55% 40% at 85% 0%,rgba(88,166,255,.05),transparent),
radial-gradient(ellipse 60% 45%
 at 50% 110%,rgba(188,140,255,.04),transparent),
linear-gradient(rgba(255,255,255,.015) 1px,transparent 1px),
linear-gradient(90deg,rgba(255,255,255,.015) 1px,transparent 1px);
background-size:auto,auto,auto,44px 44px,44px 44px;
background-attachment:fixed;
}
.mono{font-family:var(--mono)}
a{color:var(--blue)}

/* ── STATUS STRIP (distinctive opening: a command bar, not a hero) ── */
.strip{position:sticky;top:0;z-index:50;background:rgba(13,18,25,.92);backdrop-filter:blur(8px);border-bottom:1px solid var(--line);
display:flex;align-items:center;gap:1.5rem;padding:.6rem 1.2rem;flex-wrap:wrap}
.brand{display:flex;align-items:center;gap:.6rem;font-weight:800;letter-spacing:-.02em;font-size:1.05rem}
.brand .sigil{width:12px;height:12px;background:var(--green);border-radius:2px;box-shadow:0 0 12px rgba(63,185,80,.7);animation:pulse 2.4s infinite}
@keyframes pulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.55;transform:scale(.85)}}
.strip .stat{display:flex;flex-direction:column;line-height:1.15}
.strip .stat b{font-family:var(--mono);font-size:.95rem}
.strip .stat span{font-size:.62rem;text-transform:uppercase;letter-spacing:.14em;color:var(--dim)}
.strip .spacer{flex:1}
.live{display:flex;align-items:center;gap:.45rem;font-family:var(--mono);font-size:.72rem;color:var(--dim)}
.live .dot{width:7px;height:7px;border-radius:50%;background:var(--green);animation:pulse 1.6s infinite}
#countdown{font-family:var(--mono);font-size:.72rem;color:var(--faint);min-width:3.2em;text-align:right}
.btn{border:1px solid var(--line2);background:var(--panel2);color:var(--text);padding:.45rem .9rem;border-radius:6px;font-size:.82rem;font-weight:600;cursor:pointer;font-family:inherit;transition:all .15s;text-decoration:none;display:inline-block}
.btn:hover{border-color:var(--blue);transform:translateY(-1px)}
.btn.green{background:var(--green);border-color:var(--green);color:#0d1219}
.btn.green:hover{box-shadow:0 4px 16px rgba(63,185,80,.35)}
.btn.red{background:transparent;border-co
lor:var(--red);color:var(--red)}
.btn.red:hover{background:var(--red);color:#fff;box-shadow:0 4px 16px rgba(248,81,73,.35)}
.btn.sm{padding:.28rem .6rem;font-size:.75rem}

/* ── HALT BANNER ── */
.halt{background:linear-gradient(90deg,rgba(248,81,73,.15),rgba(248,81,73,.05));border:1px solid var(--red);border-left:5px solid var(--red);
margin:1rem 1.2rem 0;padding:1rem 1.2rem;border-radius:8px;display:flex;align-items:center;gap:1rem;flex-wrap:wrap;animation:haltflash 2s infinite}
@keyframes haltflash{0%,100%{box-shadow:0 0 0 rgba(248,81,73,0)}50%{box-shadow:0 0 24px rgba(248,81,73,.25)}}
.halt b{color:var(--red);font-size:1.05rem}

/* ── LAYOUT ── */
.wrap{max-width:1500px;margin:0 auto;padding:1.2rem}
.grid{display:grid;gap:1rem}
.g-overview{grid-template-columns:repeat(auto-fit,minmax(160px,1fr))}
.g-main{grid-template-columns:2fr 1fr;margin-top:1rem}
.g-2{grid-template-columns:1fr 1fr}
@media(max-width:1000px){.g-main,.g-2{grid-template-columns:1fr}}

/* ── PANELS ── */
.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden;transition:border-color .2s,transform .2s;position:relative}
.panel::before{content:"";position:absolute;top:0;left:0;right:0;height:3px;background:var(--pc,var(--blue));opacity:.85}
.panel:hover{border-color:var(--line2)}
.panel header{display:flex;align-items:baseline;gap:.7rem;padding:.9rem 1.1rem .5rem}
.panel header .kicker{font-family:var(--mono);font-size:.62rem;letter-spacing:.22em;text-transform:uppercase;color:var(--pc,var(--blue))}
.panel header h2{font-size:1.05rem;font-weight:800;letter-spacing:-.01em}
.panel .body{padding:.4rem 1.1rem 1.1rem}
.p-green{--pc:var(--green)} .p-blue{--pc:var(--blue)} .p-amber{--pc:var(--amber)} .p-purple{--pc:var(--purple)} .p-red{--pc:var(--red)} .p-cyan{--pc:var(--cyan)}

/* ── KPI CARDS ── */
.kpi{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:.9rem 1rem;position:relative;overflow:hidden;transition:transform .2s,border-color 
.2s}
.kpi:hover{transform:translateY(-2px);border-color:var(--line2)}
.kpi .label{font-size:.62rem;text-transform:uppercase;letter-spacing:.16em;color:var(--dim);margin-bottom:.35rem}
.kpi .value{font-family:var(--mono);font-size:1.55rem;font-weight:700;letter-spacing:-.02em;line-height:1.1}
.kpi .sub{font-size:.72rem;color:var(--dim);margin-top:.3rem}
.kpi .trend-up{color:var(--green)} .kpi .trend-down{color:var(--red)} .kpi .trend-flat{color:var(--dim)}
.kpi .glow{position:absolute;right:-20px;top:-20px;width:80px;height:80px;border-radius:50%;filter:blur(28px);opacity:.18}

/* ── ONE ACTION ── */
.action-card{background:linear-gradient(135deg,var(--panel2),var(--panel));border:1px solid var(--green);border-radius:10px;padding:1.2rem 1.4rem;position:relative;overflow:hidden}
.action-card::before{content:"";position:absolute;top:0;left:0;right:0;height:3px;background:var(--green)}
.action-card .kicker{font-family:var(--mono);font-size:.62rem;letter-spacing:.22em;text-transform:uppercase;color:var(--green)}
.action-card .text{font-size:1.25rem;font-weight:700;letter-spacing:-.01em;margin:.5rem 0 .6rem;line-height:1.35}
.action-card .meta{display:flex;gap:1.2rem;flex-wrap:wrap;font-size:.78rem;color:var(--dim)}
.action-card .meta b{color:var(--text);font-family:var(--mono)}

/* ── STREAM CARDS ── */
.streams{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:1rem;margin-top:1rem}
.stream{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:1rem 1.1rem;transition:transform .2s,border-color .2s,box-shadow .2s}
.stream:hover{transform:translateY(-3px);border-color:var(--line2);box-shadow:0 10px 30px rgba(0,0,0,.35)}
.stream .top{display:flex;justify-content:space-between;align-items:flex-start;gap:.5rem;margin-bottom:.6rem}
.stream h3{font-size:.98rem;font-weight:800;letter-spacing:-.01em}
.stream .slug{font-family:var(--mono);font-size:.68rem;color:var(--faint)}
.badge{font-family:var(--mono);font-size:.62rem;padding:.
18rem .5rem;border-radius:20px;letter-spacing:.08em;text-transform:uppercase;white-space:nowrap}
.b-active{background:rgba(63,185,80,.15);color:var(--green);border:1px solid rgba(63,185,80,.4)}
.b-paused{background:rgba(210,153,34,.15);color:var(--amber);border:1px solid rgba(210,153,34,.4)}
.b-killed{background:rgba(248,81,73,.15);color:var(--red);border:1px solid rgba(248,81,73,.4)}
.b-pending{background:rgba(139,148,158,.15);color:var(--dim);border:1px solid rgba(139,148,158,.4)}
.b-L{background:rgba(88,166,255,.12);color:var(--blue);border:1px solid rgba(88,166,255,.35)}
.stream .nums{display:grid;grid-template-columns:1fr 1fr 1fr;gap:.5rem;margin:.6rem 0}
.stream .num b{font-family:var(--mono);font-size:1rem;display:block}
.stream .num span{font-size:.6rem;text-transform:uppercase;letter-spacing:.1em;color:var(--dim)}
.killbar{height:5px;background:var(--bg2);border-radius:3px;overflow:hidden;margin:.3rem 0 .2rem}
.killbar i{display:block;height:100%;background:linear-gradient(90deg,var(--green),var(--amber));border-radius:3px;transition:width 1s ease}
.stream .kill-label{font-size:.68rem;color:var(--dim);font-family:var(--mono)}
.stream .agents-on{font-size:.72rem;color:var(--dim);margin-top:.5rem}
.sparkline{display:block;margin-top:.4rem}
.sparkline polyline{stroke-dasharray:300;stroke-dashoffset:300;animation:draw 1.6s ease forwards}
@keyframes draw{to{stroke-dashoffset:0}}

/* ── TABLES ── */
table{width:100%;border-collapse:collapse;font-size:.82rem}
th{text-align:left;font-size:.62rem;text-transform:uppercase;letter-spacing:.14em;color:var(--dim);padding:.5rem .6rem;border-bottom:1px solid var(--line);font-weight:600}
td{padding:.55rem .6rem;border-bottom:1px solid rgba(42,50,66,.5);vertical-align:top}
tr.row{transition:background .15s}
tr.row:hover{background:rgba(88,166,255,.05)}
.hb{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:.4rem}
.hb.ok{background:var(--green);animation:pulse 2s infinite}
.hb.stale{background:var(--red)
}
.hb.off{background:var(--faint)}
.mono-sm{font-family:var(--mono);font-size:.75rem}
.dim{color:var(--dim)} .green{color:var(--green)} .amber{color:var(--amber)} .red{color:var(--red)} .blue{color:var(--blue)} .purple{color:var(--purple)}

/* ── CALIBRATION CHART ── */
.cal{display:flex;align-items:flex-end;gap:.6rem;height:110px;padding-top:.5rem}
.cal .bar{flex:1;display:flex;flex-direction:column;align-items:center;gap:.3rem;height:100%;justify-content:flex-end}
.cal .bar i{width:100%;max-width:46px;background:linear-gradient(180deg,var(--purple),rgba(188,140,255,.3));border-radius:4px 4px 0 0;transition:height 1s ease;position:relative}
.cal .bar i:hover{filter:brightness(1.25)}
.cal .bar span{font-family:var(--mono);font-size:.62rem;color:var(--dim)}
.cal .bar b{font-family:var(--mono);font-size:.7rem;color:var(--purple)}

/* ── CHECK-IN FORM ── */
.checkin{display:grid;gap:.8rem}
.ck-row{display:flex;align-items:center;gap:.8rem;flex-wrap:wrap}
.ck-row label{font-size:.78rem;color:var(--dim);min-width:5.5em}
.seg{display:flex;gap:.3rem}
.seg input{display:none}
.seg label{width:34px;height:34px;display:flex;align-items:center;justify-content:center;border:1px solid var(--line2);border-radius:6px;cursor:pointer;font-family:var(--mono);font-size:.85rem;color:var(--dim);transition:all .15s}
.seg label:hover{border-color:var(--blue);color:var(--text)}
.seg input:checked+label{background:var(--blue);border-color:var(--blue);color:#0d1219;font-weight:700;box-shadow:0 2px 10px rgba(88,166,255,.35)}
.seg.pain input:checked+label{background:var(--amber);border-color:var(--amber)}
.seg.fear input:checked+label{background:var(--red);border-color:var(--red)}
input[type=number]{background:var(--bg2);border:1px solid var(--line2);color:var(--text);padding:.45rem .6rem;border-radius:6px;width:90px;font-family:var(--mono)}
#ck-msg{font-size:.8rem;color:var(--green);min-height:1.2em}

/* ── APPROVALS ── */
.approval{border:1px solid var(--line);border-left:3px solid var(--amb
er);border-radius:8px;padding:.8rem .9rem;margin-bottom:.7rem;background:var(--bg2);transition:border-color .2s}
.approval:hover{border-left-color:var(--blue)}
.approval .desc{font-size:.86rem;font-weight:600;margin-bottom:.3rem}
.approval .meta{font-size:.7rem;color:var(--dim);font-family:var(--mono);margin-bottom:.6rem}
.approval .btns{display:flex;gap:.5rem}

/* ── LESSONS / DECISIONS / AUDITS ── */
.item{padding:.6rem 0;border-bottom:1px solid rgba(42,50,66,.5);font-size:.82rem}
.item:last-child{border-bottom:none}
.item .tag{font-family:var(--mono);font-size:.62rem;color:var(--faint)}

/* ── FOOTER ── */
footer{margin:2.5rem 0 2rem;text-align:center;color:var(--faint);font-size:.75rem;font-family:var(--mono)}

/* ── SCROLL REVEAL ── */
.

... [Content truncated]