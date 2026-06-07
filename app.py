"""
אפליקציית ניהול דרישות
=======================
ניהול דרישות, תתי-דרישות, שיוך למודולים וזיהוי מי הציע כל דרישה.
ניהול משתמשים בידי האדמין בלבד.

הרצה מקומית:  python app.py
פריסה (Railway):  gunicorn app:app
"""

import os
from datetime import datetime
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, abort
)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

# ---------------------------------------------------------------------------
# הגדרות בסיס
# ---------------------------------------------------------------------------
app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-change-me")

# בסיס נתונים: Postgres ב-Railway אם קיים DATABASE_URL, אחרת SQLite מקומי.
db_url = os.environ.get("DATABASE_URL", "sqlite:///requirements.db")
# Railway מספק לעיתים postgres:// — SQLAlchemy דורש postgresql://
if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)
app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)

# מצבי דרישה ועדיפויות (ניתן להרחיב)
STATUSES = ["הצעה", "בבדיקה", "אושרה", "בפיתוח", "הושלמה", "נדחתה"]
PRIORITIES = ["נמוכה", "בינונית", "גבוהה", "קריטית"]

# תווית גרסה — לבדיקה שהפריסה התעדכנה
APP_VERSION = "גרסה 3.0 · צ'אט והתראות"


# ---------------------------------------------------------------------------
# מודלים
# ---------------------------------------------------------------------------
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    display_name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(40), default="")  # לטובת התראות וואטסאפ בעתיד
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    proposed = db.relationship(
        "Requirement", backref="proposer",
        foreign_keys="Requirement.proposer_id"
    )

    def set_password(self, raw):
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw):
        return check_password_hash(self.password_hash, raw)


class Module(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    color = db.Column(db.String(7), default="#6b7280")  # צבע תווית
    requirements = db.relationship("Requirement", backref="module")


class Requirement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(300), nullable=False)
    description = db.Column(db.Text, default="")
    status = db.Column(db.String(40), default="הצעה")
    priority = db.Column(db.String(40), default="בינונית")

    module_id = db.Column(db.Integer, db.ForeignKey("module.id"), nullable=True)
    proposer_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    parent_id = db.Column(db.Integer, db.ForeignKey("requirement.id"), nullable=True)

    created_by_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # תתי-דרישות
    children = db.relationship(
        "Requirement",
        backref=db.backref("parent", remote_side=[id]),
        cascade="all, delete-orphan",
        foreign_keys=[parent_id],
    )
    creator = db.relationship("User", foreign_keys=[created_by_id])


class ActionLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    user_id = db.Column(db.Integer, nullable=True)        # מי ביצע
    user_name = db.Column(db.String(120), default="")     # שם משוכפל (נשמר גם אם המשתמש נמחק)
    action = db.Column(db.String(80), default="")         # סוג הפעולה
    target = db.Column(db.String(300), default="")        # על מה בוצעה
    details = db.Column(db.String(500), default="")       # פירוט נוסף


class Comment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    requirement_id = db.Column(db.Integer, db.ForeignKey("requirement.id"), nullable=False)
    author_id = db.Column(db.Integer, nullable=True)
    author_name = db.Column(db.String(120), default="")
    body = db.Column(db.Text, default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    requirement = db.relationship(
        "Requirement",
        backref=db.backref("comments", cascade="all, delete-orphan", order_by="Comment.created_at"),
    )


class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)  # הנמען
    requirement_id = db.Column(db.Integer, db.ForeignKey("requirement.id"), nullable=True)
    text = db.Column(db.String(400), default="")
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


def send_whatsapp(phone, message):
    """
    נקודת חיבור עתידית לוואטסאפ.
    כיום אינה פעילה. כדי להפעיל בעתיד דרך Twilio:
      1. להתקין: pip install twilio (ולהוסיף ל-requirements.txt)
      2. להגדיר משתני סביבה: TWILIO_SID, TWILIO_TOKEN, TWILIO_WHATSAPP_FROM
      3. לממש כאן את השליחה בפועל.
    מוחזר True אם נשלח, אחרת False.
    """
    if not phone:
        return False
    # --- מקום למימוש Twilio בעתיד ---
    return False


def log_action(action, target="", details=""):
    """רישום פעולה ללוג. נקרא בתוך נתיב, אחרי שיש משתמש מחובר."""
    u = current_user()
    entry = ActionLog(
        user_id=u.id if u else None,
        user_name=u.display_name if u else "אורח",
        action=action,
        target=str(target)[:300],
        details=str(details)[:500],
    )
    db.session.add(entry)
    db.session.commit()


# ---------------------------------------------------------------------------
# אימות והרשאות
# ---------------------------------------------------------------------------
def current_user():
    uid = session.get("user_id")
    if uid:
        return db.session.get(User, uid)
    return None


@app.context_processor
def inject_user():
    u = current_user()
    unread = 0
    if u:
        unread = Notification.query.filter_by(user_id=u.id, is_read=False).count()
    return {"current_user": u, "unread_count": unread, "app_version": APP_VERSION}


@app.template_filter("israel_time")
def israel_time(dt):
    """המרת זמן UTC לשעון ישראל לתצוגה."""
    if dt is None:
        return ""
    try:
        from zoneinfo import ZoneInfo
        return dt.replace(tzinfo=ZoneInfo("UTC")).astimezone(
            ZoneInfo("Asia/Jerusalem")
        ).strftime("%d/%m/%Y %H:%M")
    except Exception:
        return dt.strftime("%d/%m/%Y %H:%M")


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not current_user():
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        u = current_user()
        if not u:
            return redirect(url_for("login"))
        if not u.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return wrapper


# ---------------------------------------------------------------------------
# נתיבי אימות
# ---------------------------------------------------------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(url_for("index"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            session["user_id"] = user.id
            log_action("כניסה למערכת")
            return redirect(url_for("index"))
        flash("שם משתמש או סיסמה שגויים", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    log_action("יציאה מהמערכת")
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# נתיב ראשי — רשימת הדרישות
# ---------------------------------------------------------------------------
@app.route("/")
@login_required
def index():
    module_filter = request.args.get("module", type=int)
    status_filter = request.args.get("status", "")

    query = Requirement.query.filter_by(parent_id=None)
    if module_filter:
        query = query.filter_by(module_id=module_filter)
    if status_filter:
        query = query.filter_by(status=status_filter)

    requirements = query.order_by(Requirement.created_at.desc()).all()
    modules = Module.query.order_by(Module.name).all()
    users = User.query.order_by(User.display_name).all()
    return render_template(
        "index.html",
        requirements=requirements,
        modules=modules,
        users=users,
        statuses=STATUSES,
        priorities=PRIORITIES,
        module_filter=module_filter,
        status_filter=status_filter,
    )


@app.route("/requirement/<int:req_id>")
@login_required
def requirement_detail(req_id):
    req = db.session.get(Requirement, req_id)
    if not req:
        abort(404)
    modules = Module.query.order_by(Module.name).all()
    users = User.query.order_by(User.display_name).all()
    return render_template(
        "requirement.html",
        req=req,
        modules=modules,
        users=users,
        statuses=STATUSES,
        priorities=PRIORITIES,
    )


@app.route("/requirement/add", methods=["POST"])
@login_required
def requirement_add():
    title = request.form.get("title", "").strip()
    if not title:
        flash("חובה להזין כותרת לדרישה", "error")
        return redirect(request.referrer or url_for("index"))

    parent_id = request.form.get("parent_id", type=int)
    module_id = request.form.get("module_id", type=int) or None
    proposer_id = request.form.get("proposer_id", type=int) or None

    req = Requirement(
        title=title,
        description=request.form.get("description", "").strip(),
        status=request.form.get("status") or "הצעה",
        priority=request.form.get("priority") or "בינונית",
        module_id=module_id,
        proposer_id=proposer_id,
        parent_id=parent_id,
        created_by_id=current_user().id,
    )
    db.session.add(req)
    db.session.commit()
    log_action("הוספת תת-דרישה" if parent_id else "הוספת דרישה", title)
    flash("הדרישה נוספה בהצלחה", "ok")
    if parent_id:
        return redirect(url_for("requirement_detail", req_id=parent_id))
    return redirect(url_for("index"))


@app.route("/requirement/<int:req_id>/edit", methods=["POST"])
@login_required
def requirement_edit(req_id):
    req = db.session.get(Requirement, req_id)
    if not req:
        abort(404)
    req.title = request.form.get("title", req.title).strip()
    req.description = request.form.get("description", "").strip()
    req.status = request.form.get("status") or req.status
    req.priority = request.form.get("priority") or req.priority
    req.module_id = request.form.get("module_id", type=int) or None
    req.proposer_id = request.form.get("proposer_id", type=int) or None
    db.session.commit()
    log_action("עריכת דרישה", req.title, f"סטטוס: {req.status}")
    flash("הדרישה עודכנה", "ok")
    return redirect(request.referrer or url_for("requirement_detail", req_id=req_id))


@app.route("/requirement/<int:req_id>/delete", methods=["POST"])
@login_required
def requirement_delete(req_id):
    req = db.session.get(Requirement, req_id)
    if not req:
        abort(404)
    parent_id = req.parent_id
    req_title = req.title
    db.session.delete(req)
    db.session.commit()
    log_action("מחיקת דרישה", req_title)
    flash("הדרישה נמחקה", "ok")
    if parent_id:
        return redirect(url_for("requirement_detail", req_id=parent_id))
    return redirect(url_for("index"))


@app.route("/requirement/<int:req_id>/comment", methods=["POST"])
@login_required
def comment_add(req_id):
    req = db.session.get(Requirement, req_id)
    if not req:
        abort(404)
    body = request.form.get("body", "").strip()
    if not body:
        flash("לא ניתן לשלוח תגובה ריקה", "error")
        return redirect(url_for("requirement_detail", req_id=req_id))

    me = current_user()
    comment = Comment(
        requirement_id=req.id,
        author_id=me.id,
        author_name=me.display_name,
        body=body,
    )
    db.session.add(comment)
    db.session.commit()
    log_action("תגובה בצ'אט", req.title)

    # אזכורים: כל משתמש שסומן מקבל התראה
    tagged_ids = request.form.getlist("tagged")
    for tid in tagged_ids:
        try:
            tid = int(tid)
        except ValueError:
            continue
        if tid == me.id:
            continue
        target_user = db.session.get(User, tid)
        if not target_user:
            continue
        note = Notification(
            user_id=target_user.id,
            requirement_id=req.id,
            text=f"{me.display_name} אזכר/ה אותך בצ'אט של \"{req.title}\"",
        )
        db.session.add(note)
        # תשתית עתידית לוואטסאפ (כיום אינה פעילה)
        send_whatsapp(target_user.phone, f"אוזכרת בדרישה: {req.title}")
    db.session.commit()
    flash("התגובה נוספה", "ok")
    return redirect(url_for("requirement_detail", req_id=req_id) + "#chat")


# ---------------------------------------------------------------------------
# התראות
# ---------------------------------------------------------------------------
@app.route("/notifications")
@login_required
def notifications():
    me = current_user()
    notes = (
        Notification.query.filter_by(user_id=me.id)
        .order_by(Notification.created_at.desc())
        .limit(100)
        .all()
    )
    # סימון הכל כנקרא לאחר הצפייה
    Notification.query.filter_by(user_id=me.id, is_read=False).update({"is_read": True})
    db.session.commit()
    return render_template("notifications.html", notes=notes)


# ---------------------------------------------------------------------------
# ניהול מודולים
# ---------------------------------------------------------------------------
@app.route("/modules", methods=["GET", "POST"])
@login_required
def modules():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        color = request.form.get("color", "#6b7280")
        if name and not Module.query.filter_by(name=name).first():
            db.session.add(Module(name=name, color=color))
            db.session.commit()
            log_action("הוספת מודול", name)
            flash("המודול נוסף", "ok")
        else:
            flash("שם מודול ריק או קיים כבר", "error")
        return redirect(url_for("modules"))
    return render_template("modules.html", modules=Module.query.order_by(Module.name).all())


@app.route("/modules/<int:mod_id>/delete", methods=["POST"])
@login_required
def module_delete(mod_id):
    mod = db.session.get(Module, mod_id)
    if mod:
        mod_name = mod.name
        db.session.delete(mod)
        db.session.commit()
        log_action("מחיקת מודול", mod_name)
        flash("המודול נמחק", "ok")
    return redirect(url_for("modules"))


# ---------------------------------------------------------------------------
# ניהול משתמשים — אדמין בלבד
# ---------------------------------------------------------------------------
@app.route("/admin")
@admin_required
def admin():
    users = User.query.order_by(User.created_at).all()
    return render_template("admin.html", users=users)


@app.route("/admin/user/add", methods=["POST"])
@admin_required
def admin_user_add():
    username = request.form.get("username", "").strip()
    display_name = request.form.get("display_name", "").strip() or username
    password = request.form.get("password", "")
    phone = request.form.get("phone", "").strip()
    is_admin = bool(request.form.get("is_admin"))

    if not username or not password:
        flash("חובה להזין שם משתמש וסיסמה", "error")
        return redirect(url_for("admin"))
    if User.query.filter_by(username=username).first():
        flash("שם המשתמש כבר קיים", "error")
        return redirect(url_for("admin"))

    user = User(username=username, display_name=display_name, phone=phone, is_admin=is_admin)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    log_action("רישום משתמש", display_name, "אדמין" if is_admin else "משתמש")
    flash(f"המשתמש {display_name} נוסף בהצלחה", "ok")
    return redirect(url_for("admin"))


@app.route("/admin/user/<int:user_id>/delete", methods=["POST"])
@admin_required
def admin_user_delete(user_id):
    if user_id == current_user().id:
        flash("לא ניתן למחוק את המשתמש שאיתו את מחוברת", "error")
        return redirect(url_for("admin"))
    user = db.session.get(User, user_id)
    if user:
        user_name = user.display_name
        db.session.delete(user)
        db.session.commit()
        log_action("מחיקת משתמש", user_name)
        flash("המשתמש נמחק", "ok")
    return redirect(url_for("admin"))


@app.route("/admin/user/<int:user_id>/rename", methods=["POST"])
@admin_required
def admin_user_rename(user_id):
    user = db.session.get(User, user_id)
    new_name = request.form.get("display_name", "").strip()
    if user and new_name:
        old_name = user.display_name
        user.display_name = new_name
        db.session.commit()
        log_action("שינוי שם משתמש", new_name, f"מ-{old_name}")
        flash(f"השם עודכן ל-{new_name}", "ok")
    return redirect(url_for("admin"))


@app.route("/admin/user/<int:user_id>/phone", methods=["POST"])
@admin_required
def admin_user_phone(user_id):
    user = db.session.get(User, user_id)
    if user:
        user.phone = request.form.get("phone", "").strip()
        db.session.commit()
        log_action("עדכון טלפון", user.display_name)
        flash(f"הטלפון של {user.display_name} עודכן", "ok")
    return redirect(url_for("admin"))


@app.route("/admin/user/<int:user_id>/reset", methods=["POST"])
@admin_required
def admin_user_reset(user_id):
    user = db.session.get(User, user_id)
    new_password = request.form.get("password", "")
    if user and new_password:
        user.set_password(new_password)
        db.session.commit()
        log_action("איפוס סיסמה", user.display_name)
        flash(f"הסיסמה של {user.display_name} אופסה", "ok")
    return redirect(url_for("admin"))


# ---------------------------------------------------------------------------
# לוג פעולות — אדמין בלבד
# ---------------------------------------------------------------------------
@app.route("/log")
@admin_required
def activity_log():
    user_filter = request.args.get("user", type=int)
    query = ActionLog.query
    if user_filter:
        query = query.filter_by(user_id=user_filter)
    entries = query.order_by(ActionLog.timestamp.desc()).limit(500).all()
    users = User.query.order_by(User.display_name).all()
    return render_template(
        "log.html", entries=entries, users=users, user_filter=user_filter
    )


# ---------------------------------------------------------------------------
# אתחול בסיס הנתונים ויצירת אדמין ראשוני
# ---------------------------------------------------------------------------
def ensure_schema():
    """הוספת עמודות חדשות לטבלאות קיימות (מיגרציה קלה ובטוחה)."""
    from sqlalchemy import inspect, text
    insp = inspect(db.engine)
    try:
        cols = [c["name"] for c in insp.get_columns("user")]
    except Exception:
        return  # הטבלה עוד לא קיימת — create_all יטפל
    if "phone" not in cols:
        try:
            db.session.execute(text('ALTER TABLE "user" ADD COLUMN phone VARCHAR(40)'))
            db.session.commit()
            print("[migrate] נוספה עמודת phone לטבלת המשתמשים")
        except Exception as e:
            db.session.rollback()
            print(f"[migrate] שגיאה בהוספת phone: {e}")


def init_db():
    with app.app_context():
        db.create_all()
        ensure_schema()
        if not User.query.first():
            admin_username = os.environ.get("ADMIN_USERNAME", "dikla")
            admin_password = os.environ.get("ADMIN_PASSWORD", "changeme123")
            admin_name = os.environ.get("ADMIN_NAME", "דקלה")
            admin_user = User(
                username=admin_username,
                display_name=admin_name,
                is_admin=True,
            )
            admin_user.set_password(admin_password)
            db.session.add(admin_user)
            db.session.commit()
            print(f"[init] נוצר משתמש אדמין: {admin_username}")


init_db()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
