import os
from functools import wraps

import psycopg2
from flask import Flask, redirect, render_template, request, session, url_for
from psycopg2.extras import RealDictCursor

from rules import weigh

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "tea-cupping-dev-secret")

ACCOUNTS = {
    "taster": {"password": "tea123456", "role": "writer"},
    "observer": {"password": "look123456", "role": "reader"},
}


def db():
    return psycopg2.connect(os.environ["DATABASE_URL"])


def login_required(fn):
    @wraps(fn)
    def wrap(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("login"))
        return fn(*args, **kwargs)

    return wrap


@app.get("/health")
def health():
    return {"status": "ok", "service": "tea-blend-cupping"}


@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    if request.method == "POST":
        name = request.form.get("username", "").strip()
        account = ACCOUNTS.get(name)
        if not account or account["password"] != request.form.get("password", ""):
            error = "用户名或密码错误"
        else:
            session["user"] = name
            session["role"] = account["role"]
            return redirect(url_for("home"))
    return render_template("login.html", error=error)


@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.get("/")
@login_required
def home():
    table = request.args.get("table", "").strip()
    with db() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM cupping_tables ORDER BY name")
        tables = cur.fetchall()
        if table:
            # 服务端过滤：只留当前在该台的批次，每批取最新一行
            cur.execute(
                """SELECT * FROM (
                       SELECT DISTINCT ON (lot) * FROM cuppings ORDER BY lot, id DESC
                   ) latest
                   WHERE latest.lot IN (
                       SELECT lot FROM (
                           SELECT DISTINCT ON (lot) lot, target_table
                           FROM transfers ORDER BY lot, id DESC
                       ) cur WHERE cur.target_table = %s
                   )
                   ORDER BY latest.id DESC""",
                (table,),
            )
        else:
            cur.execute("SELECT * FROM cuppings ORDER BY id DESC")
        rows = cur.fetchall()
    return render_template(
        "home.html",
        rows=rows,
        tables=tables,
        current_table=table,
        can_write=session.get("role") == "writer",
    )


@app.post("/cuppings")
@login_required
def create():
    if session.get("role") != "writer":
        return ("仅审评员可提交拼配审评", 403)
    aroma = float(request.form["aroma"])
    taste = float(request.form["taste"])
    liquor = float(request.form["liquor"])
    lot = request.form["lot"].strip()
    verdict, note, score = weigh(aroma, taste, liquor)
    with db() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """INSERT INTO cuppings (lot, aroma, taste, liquor, score, verdict, note, created_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
            (lot, aroma, taste, liquor, score, verdict, note, session["user"]),
        )
        row = cur.fetchone()
        conn.commit()
    if request.headers.get("HX-Request"):
        return render_template("_row.html", row=row)
    return redirect(url_for("home"))


def transfers_context():
    with db() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM cupping_tables ORDER BY name")
        tables = cur.fetchall()
        cur.execute("SELECT * FROM transfers ORDER BY id DESC")
        history = cur.fetchall()
        cur.execute("SELECT DISTINCT lot FROM cuppings ORDER BY lot")
        lots = [row["lot"] for row in cur.fetchall()]
    return {"tables": tables, "history": history, "lots": lots}


@app.get("/transfers")
@login_required
def transfers_page():
    return render_template(
        "transfers.html",
        **transfers_context(),
        can_write=session.get("role") == "writer",
        error="",
    )


@app.post("/tables")
@login_required
def register_table():
    if session.get("role") != "writer":
        return ("观察员不能登记审评台", 403)
    name = request.form.get("name", "").strip()
    if not name:
        return (
            render_template("transfers.html", **transfers_context(), can_write=True, error="台名不能为空"),
            400,
        )
    try:
        with db() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO cupping_tables (name, created_by) VALUES (%s, %s)",
                (name, session["user"]),
            )
            conn.commit()
    except psycopg2.errors.UniqueViolation:
        return (
            render_template("transfers.html", **transfers_context(), can_write=True, error="台名已存在"),
            409,
        )
    return redirect(url_for("transfers_page"))


@app.post("/transfers")
@login_required
def create_transfer():
    if session.get("role") != "writer":
        return ("观察员不能建调拨单", 403)
    source = request.form.get("source", "").strip()
    target = request.form.get("target", "").strip()
    lot = request.form.get("lot", "").strip()
    context = transfers_context()
    registered = {t["name"] for t in context["tables"]}
    error = ""
    if not lot:
        error = "批次不能为空"
    elif not source or not target:
        error = "源台与目标台都要填写"
    elif source == target:
        error = "源台与目标台不能相同"
    elif source not in registered or target not in registered:
        error = "未入册的台名不能出现在调拨单里"
    if error:
        return render_template("transfers.html", **context, can_write=True, error=error), 400
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO transfers (lot, source_table, target_table, moved_by) VALUES (%s,%s,%s,%s)",
            (lot, source, target, session["user"]),
        )
        conn.commit()
    return redirect(url_for("transfers_page"))
