import os
from functools import wraps

import psycopg2
from flask import Flask, redirect, render_template, request, session, url_for
from psycopg2.errors import UniqueViolation
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


def writer_required(fn):
    @wraps(fn)
    def wrap(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("login"))
        if session.get("role") != "writer":
            return ("观察员只读：不能登台、不能建调拨单", 403)
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


def latest_lot_rows_sql():
    """每个批次最新的一行审评记录，且该批最新调拨落在指定台。

    台归属完全在服务端计算：取每批最新一条调拨履历的目标台。
    源台在最新一批调走后自然查不到该批最新行；目标台查得到。
    """
    return """
        SELECT DISTINCT ON (c.lot) c.*
        FROM cuppings c
        JOIN (
            SELECT DISTINCT ON (lot) lot, target_station
            FROM transfers
            ORDER BY lot, moved_at DESC, id DESC
        ) loc ON loc.lot = c.lot
        {where}
        ORDER BY c.lot, c.id DESC
    """


@app.get("/")
@login_required
def home():
    station = request.args.get("station", "").strip()
    rows = []
    with db() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT name FROM stations ORDER BY name")
        stations = [r["name"] for r in cur.fetchall()]
        if station:
            # 未入册的台名不提供过滤视图
            cur.execute("SELECT 1 FROM stations WHERE name = %s", (station,))
            if cur.fetchone() is None:
                return ("台名未入册，无法查看该台视图", 400)
            cur.execute(
                latest_lot_rows_sql().format(where="WHERE loc.target_station = %s"),
                (station,),
            )
        else:
            cur.execute("SELECT * FROM cuppings ORDER BY id DESC")
        rows = cur.fetchall()
    return render_template(
        "home.html",
        rows=rows,
        stations=stations,
        active_station=station,
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


@app.get("/transfer")
@login_required
def transfer_page():
    with db() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM stations ORDER BY name")
        stations = cur.fetchall()
        cur.execute("SELECT * FROM transfers ORDER BY moved_at DESC, id DESC")
        transfers = cur.fetchall()
        cur.execute("SELECT DISTINCT lot FROM cuppings ORDER BY lot")
        lots = [r["lot"] for r in cur.fetchall()]
    return render_template(
        "transfer.html",
        stations=stations,
        transfers=transfers,
        lots=lots,
        can_write=session.get("role") == "writer",
    )


@app.post("/stations")
@writer_required
def register_station():
    name = request.form.get("name", "").strip()
    if not name:
        return ("台名不能为空", 400)
    try:
        with db() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "INSERT INTO stations (name, created_by) VALUES (%s, %s) RETURNING *",
                (name, session["user"]),
            )
            row = cur.fetchone()
            conn.commit()
    except UniqueViolation:
        return ("台名已入册，台名唯一", 400)
    if request.headers.get("HX-Request"):
        return render_template("_station_row.html", row=row)
    return redirect(url_for("transfer_page"))


@app.post("/transfers")
@writer_required
def create_transfer():
    source = request.form.get("source_station", "").strip()
    target = request.form.get("target_station", "").strip()
    lot = request.form.get("lot", "").strip()
    if not source or not target or not lot:
        return ("调拨单须含源台、目标台、批次", 400)
    if source == target:
        return ("源台与目标台不能相同", 400)

    with db() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        # 服务端校验：只有已入册的台能进调拨单；任一未入册则拒收，履历不增
        cur.execute(
            "SELECT name FROM stations WHERE name IN (%s, %s)", (source, target)
        )
        registered = {r["name"] for r in cur.fetchall()}
        missing = [s for s in (source, target) if s not in registered]
        if missing:
            return (f"台名未入册，调拨单拒收：{'、'.join(missing)}", 400)

        cur.execute(
            """INSERT INTO transfers (source_station, target_station, lot, moved_by)
               VALUES (%s, %s, %s, %s) RETURNING *""",
            (source, target, lot, session["user"]),
        )
        row = cur.fetchone()
        conn.commit()
    if request.headers.get("HX-Request"):
        return render_template("_transfer_row.html", row=row)
    return redirect(url_for("transfer_page"))
