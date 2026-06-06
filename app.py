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


# ---------------------------------------------------------------------------
# מודלים
# ---------------------------------------------------------------------------
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    display_name = db.Column(db.String(120), nullable=False)
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
    return {"current_user": current_user()}


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
            return redirect(url_for("index"))
        flash("שם משתמש או סיסמה שגויים", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
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
    flash("הדרישה עודכנה", "ok")
    return redirect(request.referrer or url_for("requirement_detail", req_id=req_id))


@app.route("/requirement/<int:req_id>/delete", methods=["POST"])
@login_required
def requirement_delete(req_id):
    req = db.session.get(Requirement, req_id)
    if not req:
        abort(404)
    parent_id = req.parent_id
    db.session.delete(req)
    db.session.commit()
    flash("הדרישה נמחקה", "ok")
    if parent_id:
        return redirect(url_for("requirement_detail", req_id=parent_id))
    return redirect(url_for("index"))


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
        db.session.delete(mod)
        db.session.commit()
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
    is_admin = bool(request.form.get("is_admin"))

    if not username or not password:
        flash("חובה להזין שם משתמש וסיסמה", "error")
        return redirect(url_for("admin"))
    if User.query.filter_by(username=username).first():
        flash("שם המשתמש כבר קיים", "error")
        return redirect(url_for("admin"))

    user = User(username=username, display_name=display_name, is_admin=is_admin)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
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
        db.session.delete(user)
        db.session.commit()
        flash("המשתמש נמחק", "ok")
    return redirect(url_for("admin"))


@app.route("/admin/user/<int:user_id>/reset", methods=["POST"])
@admin_required
def admin_user_reset(user_id):
    user = db.session.get(User, user_id)
    new_password = request.form.get("password", "")
    if user and new_password:
        user.set_password(new_password)
        db.session.commit()
        flash(f"הסיסמה של {user.display_name} אופסה", "ok")
    return redirect(url_for("admin"))


# ---------------------------------------------------------------------------
# אתחול בסיס הנתונים ויצירת אדמין ראשוני
# ---------------------------------------------------------------------------
def init_db():
    with app.app_context():
        db.create_all()
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
