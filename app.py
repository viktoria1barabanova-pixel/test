import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from functools import wraps

from flask import (
    Flask,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

try:
    import requests
except Exception:  # pragma: no cover
    requests = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(BASE_DIR, "support.db")
BITRIX_WEBHOOK_URL = os.getenv("BITRIX_WEBHOOK_URL", "").strip()
BITRIX_INBOUND_KEY = os.getenv("BITRIX_INBOUND_KEY", "bitrix-demo-key")
MANAGER_API_KEY = os.getenv("MANAGER_API_KEY", "manager-demo-key")

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-secret-change-me")

CRITICALITY_OPTIONS = ["Низкая", "Средняя", "Высокая", "Критическая"]
STATUS_OPTIONS = ["Новая", "В работе", "Ждёт клиента", "Решена", "Закрыта"]


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    schema_path = os.path.join(BASE_DIR, "schema.sql")
    with closing(sqlite3.connect(DATABASE)) as db, open(schema_path, "r", encoding="utf-8") as f:
        db.executescript(f.read())
        db.commit()


def ensure_schema():
    db = get_db()
    columns = {row["name"] for row in db.execute("PRAGMA table_info(tickets)").fetchall()}
    if "bitrix_entity_type" not in columns:
        db.execute("ALTER TABLE tickets ADD COLUMN bitrix_entity_type TEXT")
    if "bitrix_entity_id" not in columns:
        db.execute("ALTER TABLE tickets ADD COLUMN bitrix_entity_id INTEGER")
    db.commit()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


def get_or_create_user(phone: str):
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE phone = ?", (phone,)).fetchone()
    if user:
        return user
    db.execute(
        "INSERT INTO users (phone, full_name, created_at) VALUES (?, ?, ?)",
        (phone, f"Партнёр {phone}", now_iso()),
    )
    db.commit()
    return db.execute("SELECT * FROM users WHERE phone = ?", (phone,)).fetchone()


def bitrix_call(method: str, payload: dict):
    if not BITRIX_WEBHOOK_URL or requests is None:
        return False, {"error": "BITRIX_WEBHOOK_URL не задан или requests недоступен"}

    url = f"{BITRIX_WEBHOOK_URL.rstrip('/')}/{method}"
    try:
        response = requests.post(url, json=payload, timeout=8)
        response.raise_for_status()
        data = response.json()
        if "error" in data:
            return False, data
        return True, data
    except Exception as exc:  # pragma: no cover
        return False, {"error": str(exc)}


def create_bitrix_ticket(ticket, user_phone):
    payload = {
        "fields": {
            "TITLE": f"[Support #{ticket['id']}] {ticket['title']}",
            "NAME": "Support request",
            "PHONE": [{"VALUE": user_phone, "VALUE_TYPE": "WORK"}],
            "COMMENTS": (
                f"Local Ticket ID: {ticket['id']}\n"
                f"Телефон: {user_phone}\n"
                f"Критичность: {ticket['criticality']}\n"
                f"Тег: {ticket['tag']}\n"
                f"Отдел: {ticket['department']}\n"
                f"Описание:\n{ticket['description']}"
            ),
            "SOURCE_ID": "WEB",
        }
    }
    return bitrix_call("crm.lead.add", payload)


def sync_comment_to_bitrix(ticket, text, author):
    entity_id = ticket["bitrix_entity_id"]
    entity_type = (ticket["bitrix_entity_type"] or "LEAD").upper()
    if not entity_id:
        return False, {"error": "ticket is not linked to bitrix entity"}

    payload = {
        "fields": {
            "ENTITY_ID": int(entity_id),
            "ENTITY_TYPE": entity_type,
            "COMMENT": f"[{author}] {text}",
        }
    }
    return bitrix_call("crm.timeline.comment.add", payload)


def sync_status_to_bitrix(ticket, status):
    entity_id = ticket["bitrix_entity_id"]
    if not entity_id:
        return False, {"error": "ticket is not linked to bitrix entity"}
    payload = {
        "id": int(entity_id),
        "fields": {
            "STATUS_DESCRIPTION": status,
        },
    }
    return bitrix_call("crm.lead.update", payload)


def find_ticket_for_inbound(data):
    db = get_db()
    local_ticket_id = data.get("local_ticket_id")
    bitrix_entity_id = data.get("bitrix_entity_id")

    if local_ticket_id:
        return db.execute("SELECT * FROM tickets WHERE id = ?", (local_ticket_id,)).fetchone()
    if bitrix_entity_id:
        return db.execute(
            "SELECT * FROM tickets WHERE bitrix_entity_id = ?", (bitrix_entity_id,)
        ).fetchone()
    return None


@app.before_request
def before_request():
    ensure_schema()


@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        phone = request.form.get("phone", "").strip()
        otp = request.form.get("otp", "").strip()

        if not phone:
            flash("Введите номер телефона")
            return render_template("login.html")

        # MVP: верификация кода заглушкой
        if otp and otp != "0000":
            flash("Неверный код. Для демо используйте 0000")
            return render_template("login.html", phone=phone)

        user = get_or_create_user(phone)
        session["user_id"] = user["id"]
        session["phone"] = user["phone"]
        flash("Вы успешно вошли")
        return redirect(url_for("dashboard"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Вы вышли из аккаунта")
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    db = get_db()
    tickets = db.execute(
        """
        SELECT t.*,
               (SELECT COUNT(1) FROM comments c
                WHERE c.ticket_id = t.id AND c.author_type = 'manager' AND c.read_by_client = 0) AS unread_count
        FROM tickets t
        WHERE t.user_id = ?
        ORDER BY t.created_at DESC
        """,
        (session["user_id"],),
    ).fetchall()
    return render_template("dashboard.html", tickets=tickets)


@app.route("/tickets/new", methods=["GET", "POST"])
@login_required
def new_ticket():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        criticality = request.form.get("criticality", "").strip()
        tag = request.form.get("tag", "").strip()
        department = request.form.get("department", "").strip()

        if not title or not description or not criticality or not tag:
            flash("Заполните обязательные поля")
            return render_template(
                "new_ticket.html", criticality_options=CRITICALITY_OPTIONS
            )

        db = get_db()
        cursor = db.execute(
            """
            INSERT INTO tickets (user_id, criticality, tag, department, title, description, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 'Новая', ?)
            """,
            (
                session["user_id"],
                criticality,
                tag,
                department,
                title,
                description,
                now_iso(),
            ),
        )
        ticket_id = cursor.lastrowid
        db.commit()

        ticket = db.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        ok, info = create_bitrix_ticket(ticket, session.get("phone", ""))
        if ok:
            bitrix_id = info.get("result")
            db.execute(
                """
                UPDATE tickets
                SET bitrix_sync_status = 'sent', bitrix_payload = ?, bitrix_entity_type = 'LEAD', bitrix_entity_id = ?
                WHERE id = ?
                """,
                (str(info)[:2000], bitrix_id, ticket_id),
            )
            db.commit()
        else:
            db.execute(
                "UPDATE tickets SET bitrix_sync_status = 'error', bitrix_payload = ? WHERE id = ?",
                (str(info)[:2000], ticket_id),
            )
            db.commit()

        flash("Заявка создана")
        return redirect(url_for("ticket_detail", ticket_id=ticket_id))

    return render_template("new_ticket.html", criticality_options=CRITICALITY_OPTIONS)


@app.route("/tickets/<int:ticket_id>")
@login_required
def ticket_detail(ticket_id):
    db = get_db()
    ticket = db.execute(
        "SELECT * FROM tickets WHERE id = ? AND user_id = ?", (ticket_id, session["user_id"])
    ).fetchone()
    if not ticket:
        flash("Заявка не найдена")
        return redirect(url_for("dashboard"))

    comments = db.execute(
        "SELECT * FROM comments WHERE ticket_id = ? ORDER BY created_at ASC", (ticket_id,)
    ).fetchall()
    db.execute(
        "UPDATE comments SET read_by_client = 1 WHERE ticket_id = ? AND author_type = 'manager'",
        (ticket_id,),
    )
    db.commit()

    return render_template("ticket_detail.html", ticket=ticket, comments=comments)


@app.route("/tickets/<int:ticket_id>/comment", methods=["POST"])
@login_required
def add_client_comment(ticket_id):
    text = request.form.get("text", "").strip()
    if not text:
        flash("Комментарий пуст")
        return redirect(url_for("ticket_detail", ticket_id=ticket_id))

    db = get_db()
    ticket = db.execute(
        "SELECT * FROM tickets WHERE id = ? AND user_id = ?", (ticket_id, session["user_id"])
    ).fetchone()
    if not ticket:
        flash("Заявка не найдена")
        return redirect(url_for("dashboard"))

    db.execute(
        """
        INSERT INTO comments (ticket_id, author_type, author_name, text, created_at, read_by_client)
        VALUES (?, 'client', ?, ?, ?, 1)
        """,
        (ticket_id, session.get("phone", "client"), text, now_iso()),
    )

    sync_ok, sync_info = sync_comment_to_bitrix(ticket, text, session.get("phone", "client"))
    if sync_ok:
        db.execute(
            "UPDATE tickets SET bitrix_sync_status = 'sent', bitrix_payload = ? WHERE id = ?",
            (str(sync_info)[:2000], ticket_id),
        )
    else:
        db.execute(
            "UPDATE tickets SET bitrix_sync_status = 'error', bitrix_payload = ? WHERE id = ?",
            (str(sync_info)[:2000], ticket_id),
        )

    db.commit()
    flash("Комментарий отправлен")
    return redirect(url_for("ticket_detail", ticket_id=ticket_id))


@app.route("/manager/tickets/<int:ticket_id>/comment", methods=["POST"])
def manager_comment(ticket_id):
    api_key = request.headers.get("X-Manager-Key", "")
    if api_key != MANAGER_API_KEY:
        return {"ok": False, "error": "Unauthorized"}, 401

    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    author = (data.get("author") or "Manager").strip()
    if not text:
        return {"ok": False, "error": "text is required"}, 400

    db = get_db()
    ticket = db.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    if not ticket:
        return {"ok": False, "error": "ticket not found"}, 404

    db.execute(
        """
        INSERT INTO comments (ticket_id, author_type, author_name, text, created_at, read_by_client)
        VALUES (?, 'manager', ?, ?, ?, 0)
        """,
        (ticket_id, author, text, now_iso()),
    )
    if ticket["first_response_at"] is None:
        db.execute("UPDATE tickets SET first_response_at = ? WHERE id = ?", (now_iso(), ticket_id))
    db.commit()
    return {"ok": True}


@app.route("/manager/tickets/<int:ticket_id>/status", methods=["POST"])
def manager_update_status(ticket_id):
    api_key = request.headers.get("X-Manager-Key", "")
    if api_key != MANAGER_API_KEY:
        return {"ok": False, "error": "Unauthorized"}, 401

    data = request.get_json(silent=True) or {}
    status = (data.get("status") or "").strip()
    if status not in STATUS_OPTIONS:
        return {"ok": False, "error": f"status must be one of {STATUS_OPTIONS}"}, 400

    db = get_db()
    ticket = db.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    if not ticket:
        return {"ok": False, "error": "ticket not found"}, 404

    resolved_at = now_iso() if status in {"Решена", "Закрыта"} else None
    db.execute(
        "UPDATE tickets SET status = ?, resolved_at = COALESCE(?, resolved_at) WHERE id = ?",
        (status, resolved_at, ticket_id),
    )

    sync_ok, sync_info = sync_status_to_bitrix(ticket, status)
    db.execute(
        "UPDATE tickets SET bitrix_sync_status = ?, bitrix_payload = ? WHERE id = ?",
        ("sent" if sync_ok else "error", str(sync_info)[:2000], ticket_id),
    )
    db.commit()
    return {"ok": True, "bitrix_sync": sync_ok}


@app.route("/integrations/bitrix/inbound", methods=["POST"])
def bitrix_inbound():
    api_key = request.headers.get("X-Bitrix-Key", "")
    if api_key != BITRIX_INBOUND_KEY:
        return {"ok": False, "error": "Unauthorized"}, 401

    data = request.get_json(silent=True) or {}
    action = (data.get("action") or "").strip().lower()
    if action not in {"comment", "status"}:
        return {"ok": False, "error": "action must be comment or status"}, 400

    ticket = find_ticket_for_inbound(data)
    if not ticket:
        return {"ok": False, "error": "ticket not found by local_ticket_id/bitrix_entity_id"}, 404

    db = get_db()
    if action == "comment":
        text = (data.get("text") or "").strip()
        author = (data.get("author") or "Bitrix manager").strip()
        if not text:
            return {"ok": False, "error": "text is required for action=comment"}, 400

        db.execute(
            """
            INSERT INTO comments (ticket_id, author_type, author_name, text, created_at, read_by_client)
            VALUES (?, 'manager', ?, ?, ?, 0)
            """,
            (ticket["id"], author, text, now_iso()),
        )
        if ticket["first_response_at"] is None:
            db.execute(
                "UPDATE tickets SET first_response_at = ?, bitrix_sync_status = 'sent' WHERE id = ?",
                (now_iso(), ticket["id"]),
            )
        else:
            db.execute("UPDATE tickets SET bitrix_sync_status = 'sent' WHERE id = ?", (ticket["id"],))
    else:
        status = (data.get("status") or "").strip()
        if status not in STATUS_OPTIONS:
            return {"ok": False, "error": f"status must be one of {STATUS_OPTIONS}"}, 400

        resolved_at = now_iso() if status in {"Решена", "Закрыта"} else None
        db.execute(
            "UPDATE tickets SET status = ?, resolved_at = COALESCE(?, resolved_at), bitrix_sync_status = 'sent' WHERE id = ?",
            (status, resolved_at, ticket["id"]),
        )

    db.commit()
    return {"ok": True, "ticket_id": ticket["id"], "action": action}


@app.route("/tickets/<int:ticket_id>/rate", methods=["POST"])
@login_required
def rate_ticket(ticket_id):
    rate = request.form.get("rate", "").strip()
    if rate not in {"1", "2", "3", "4", "5"}:
        flash("Оценка должна быть от 1 до 5")
        return redirect(url_for("ticket_detail", ticket_id=ticket_id))

    db = get_db()
    db.execute(
        "UPDATE tickets SET rating = ? WHERE id = ? AND user_id = ?",
        (int(rate), ticket_id, session["user_id"]),
    )
    db.commit()
    flash("Спасибо за оценку")
    return redirect(url_for("ticket_detail", ticket_id=ticket_id))


@app.route("/analytics")
def analytics():
    db = get_db()

    total = db.execute("SELECT COUNT(1) AS c FROM tickets").fetchone()["c"]
    by_tag = db.execute(
        "SELECT tag, COUNT(1) AS c FROM tickets GROUP BY tag ORDER BY c DESC"
    ).fetchall()
    by_department = db.execute(
        "SELECT department, COUNT(1) AS c FROM tickets GROUP BY department ORDER BY c DESC"
    ).fetchall()

    metrics = db.execute(
        """
        SELECT
            AVG((julianday(first_response_at) - julianday(created_at)) * 24 * 60) AS avg_first_response_min,
            AVG((julianday(resolved_at) - julianday(created_at)) * 24 * 60) AS avg_resolution_min,
            AVG(rating) AS avg_rating
        FROM tickets
        """
    ).fetchone()

    return render_template(
        "analytics.html",
        total=total,
        by_tag=by_tag,
        by_department=by_department,
        metrics=metrics,
    )


if __name__ == "__main__":
    if not os.path.exists(DATABASE):
        init_db()
    app.run(debug=True)
