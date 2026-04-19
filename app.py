"""
App principal - Sistema de KPIs para cajeros
Flask + SQLite. Todo el cálculo de ranking y score se hace en Python.
"""
import csv
import io
import os
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta
from functools import wraps
from hashlib import sha256

from flask import (
    Flask, flash, g, jsonify, redirect, render_template, request, session, url_for,
)

# --- Configuración básica ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "kpis.db")

app = Flask(__name__)
app.secret_key = "cambiar-esta-clave-en-produccion-kpis-2026"

SUCURSALES = ["CENTRAL", "LURO", "PERALTA", "TALCA"]

# Tipos de insignias disponibles (clave -> (emoji, nombre))
INSIGNIAS = {
    "centurion":        ("🏅", "Centurión"),
    "semana_perfecta":  ("⚡", "Semana perfecta"),
    "top_mes":          ("🔥", "Top del mes"),
    "upt_oro":          ("🎯", "UPT de oro"),
    "vendedora_dia":    ("👗", "Vendedora del día"),
}


# --- Conexión a base de datos ---
def get_db():
    """Devuelve una conexión SQLite por request."""
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    """Crea las tablas si no existen y siembra la configuración por defecto."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.executescript("""
    CREATE TABLE IF NOT EXISTS empleados (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        legajo TEXT UNIQUE NOT NULL,
        nombre TEXT NOT NULL,
        apellido TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        sucursal TEXT NOT NULL,
        rol TEXT NOT NULL DEFAULT 'empleado',
        activo INTEGER NOT NULL DEFAULT 1
    );

    CREATE TABLE IF NOT EXISTS kpis (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        empleado_id INTEGER NOT NULL,
        fecha DATE NOT NULL,
        tickets_cantidad INTEGER NOT NULL DEFAULT 0,
        prendas_por_ticket REAL NOT NULL DEFAULT 0,
        horas_trabajadas REAL NOT NULL DEFAULT 0,
        prendas_total REAL NOT NULL DEFAULT 0,
        sucursal TEXT,
        FOREIGN KEY (empleado_id) REFERENCES empleados(id)
    );
    CREATE INDEX IF NOT EXISTS idx_kpis_empleado_fecha ON kpis(empleado_id, fecha);

    CREATE TABLE IF NOT EXISTS kpis_horarios (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        empleado_id INTEGER NOT NULL,
        fecha DATE NOT NULL,
        hora INTEGER NOT NULL,
        tickets INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY (empleado_id) REFERENCES empleados(id)
    );
    CREATE INDEX IF NOT EXISTS idx_horarios_fecha ON kpis_horarios(fecha);

    CREATE TABLE IF NOT EXISTS incidencias (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        empleado_id INTEGER NOT NULL,
        tipo TEXT NOT NULL,
        descripcion TEXT,
        fecha DATE NOT NULL,
        estado TEXT NOT NULL DEFAULT 'pendiente',
        comentario_empleado TEXT,
        FOREIGN KEY (empleado_id) REFERENCES empleados(id)
    );

    CREATE TABLE IF NOT EXISTS premios (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        empleado_id INTEGER NOT NULL,
        descripcion TEXT NOT NULL,
        fecha DATE NOT NULL,
        FOREIGN KEY (empleado_id) REFERENCES empleados(id)
    );

    CREATE TABLE IF NOT EXISTS insignias (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        empleado_id INTEGER NOT NULL,
        tipo TEXT NOT NULL,
        fecha_otorgada DATE NOT NULL,
        UNIQUE(empleado_id, tipo),
        FOREIGN KEY (empleado_id) REFERENCES empleados(id)
    );

    CREATE TABLE IF NOT EXISTS config (
        clave TEXT PRIMARY KEY,
        valor TEXT NOT NULL
    );

    -- Sucursales que un supervisor tiene asignadas (rol='supervisor').
    -- Un supervisor puede cubrir varias; solo lee datos de esas sucursales.
    CREATE TABLE IF NOT EXISTS supervisor_sucursales (
        empleado_id INTEGER NOT NULL,
        sucursal    TEXT NOT NULL,
        PRIMARY KEY (empleado_id, sucursal),
        FOREIGN KEY (empleado_id) REFERENCES empleados(id)
    );
    """)
    # Configuración por defecto de pesos del score mensual
    defaults = {
        "peso_tickets":     "1.0",
        "peso_upt":         "10.0",
        "peso_prendas":     "0.5",
        "peso_incidencias": "15.0",
    }
    for k, v in defaults.items():
        cur.execute("INSERT OR IGNORE INTO config(clave, valor) VALUES (?, ?)", (k, v))
    conn.commit()
    conn.close()


# --- Utilidades de contraseñas y sesión ---
def hash_pw(pw):
    return sha256(pw.encode("utf-8")).hexdigest()


def login_required(f):
    @wraps(f)
    def wrap(*args, **kwargs):
        if "empleado_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrap


def admin_required(f):
    """Solo admin. Usar en rutas de escritura."""
    @wraps(f)
    def wrap(*args, **kwargs):
        if session.get("rol") != "admin":
            flash("Acceso restringido a administradores.", "error")
            return redirect(url_for("dashboard"))
        return f(*args, **kwargs)
    return wrap


def staff_required(f):
    """Admin o supervisor. Supervisor tiene solo acceso de lectura."""
    @wraps(f)
    def wrap(*args, **kwargs):
        if session.get("rol") not in ("admin", "supervisor"):
            flash("Acceso restringido.", "error")
            return redirect(url_for("dashboard"))
        return f(*args, **kwargs)
    return wrap


def sucursales_visibles():
    """Sucursales que el usuario actual puede ver.
    - admin -> None (todas)
    - supervisor -> lista de sucursales asignadas
    - empleado -> [su sucursal]
    """
    rol = session.get("rol")
    if rol == "admin":
        return None
    if rol == "supervisor":
        return session.get("sucursales_supervisor") or []
    return [session.get("sucursal")] if session.get("sucursal") else []


@app.context_processor
def inject_user():
    """Expone info del usuario logueado a todos los templates."""
    return {
        "current_user": {
            "id":        session.get("empleado_id"),
            "legajo":    session.get("legajo"),
            "nombre":    session.get("nombre"),
            "apellido":  session.get("apellido"),
            "rol":       session.get("rol"),
            "sucursal":  session.get("sucursal"),
        }
    }


# --- Helpers de configuración ---
def get_pesos():
    """Devuelve los pesos de score actuales como diccionario de floats."""
    rows = get_db().execute("SELECT clave, valor FROM config").fetchall()
    return {r["clave"]: float(r["valor"]) for r in rows}


# --- Cálculo de score mensual ---
def calcular_score(tickets, upt_promedio, prendas, incidencias, pesos):
    """Score = tickets*p1 + upt*p2 + prendas*p3 - incidencias*p4 (redondeado)."""
    return round(
        tickets     * pesos["peso_tickets"]     +
        upt_promedio* pesos["peso_upt"]         +
        prendas     * pesos["peso_prendas"]     -
        incidencias * pesos["peso_incidencias"],
        2,
    )


def primer_dia_mes(ref=None):
    ref = ref or date.today()
    return ref.replace(day=1)


def resumen_mensual(empleado_id, mes_inicio=None):
    """Agregado mensual de un empleado: tickets, upt_prom, prendas, incidencias, score."""
    db = get_db()
    if mes_inicio is None:
        mes_inicio = primer_dia_mes()
    pesos = get_pesos()

    row = db.execute("""
        SELECT COALESCE(SUM(tickets_cantidad), 0) AS tickets,
               COALESCE(AVG(prendas_por_ticket), 0) AS upt_prom,
               COALESCE(SUM(prendas_total), 0) AS prendas
          FROM kpis
         WHERE empleado_id = ? AND fecha >= ?
    """, (empleado_id, mes_inicio.isoformat())).fetchone()

    incid = db.execute("""
        SELECT COUNT(*) AS n FROM incidencias
         WHERE empleado_id = ? AND fecha >= ?
    """, (empleado_id, mes_inicio.isoformat())).fetchone()["n"]

    tickets = int(row["tickets"] or 0)
    upt     = round(float(row["upt_prom"] or 0), 2)
    prendas = round(float(row["prendas"] or 0), 2)
    score   = calcular_score(tickets, upt, prendas, incid, pesos)
    return {
        "tickets":     tickets,
        "upt":         upt,
        "prendas":     prendas,
        "incidencias": incid,
        "score":       score,
    }


def ranking_mensual(sucursal=None, mes_inicio=None):
    """Devuelve lista de cajeros ordenados por score desc del mes.
    `sucursal` puede ser None/''/'Todas' (sin filtro), un string (una sucursal)
    o una lista de sucursales.
    """
    db = get_db()
    if mes_inicio is None:
        mes_inicio = primer_dia_mes()
    q = "SELECT id, legajo, nombre, apellido, sucursal FROM empleados WHERE rol='empleado' AND activo=1"
    params = []
    if isinstance(sucursal, (list, tuple)) and sucursal:
        q += " AND sucursal IN (%s)" % ",".join("?" * len(sucursal))
        params.extend(sucursal)
    elif isinstance(sucursal, str) and sucursal and sucursal != "Todas":
        q += " AND sucursal = ?"
        params.append(sucursal)
    empleados = db.execute(q, params).fetchall()

    resultado = []
    for e in empleados:
        r = resumen_mensual(e["id"], mes_inicio)
        resultado.append({
            "id":       e["id"],
            "legajo":   e["legajo"],
            "nombre":   e["nombre"],
            "apellido": e["apellido"],
            "sucursal": e["sucursal"],
            **r,
        })
    resultado.sort(key=lambda x: x["score"], reverse=True)
    return resultado


# --- Insignias automáticas ---
def otorgar_insignia(empleado_id, tipo, fecha=None):
    """Inserta una insignia si no la tiene aún."""
    if tipo not in INSIGNIAS:
        return False
    fecha = fecha or date.today()
    db = get_db()
    try:
        db.execute(
            "INSERT INTO insignias(empleado_id, tipo, fecha_otorgada) VALUES (?, ?, ?)",
            (empleado_id, tipo, fecha.isoformat()),
        )
        db.commit()
        return True
    except sqlite3.IntegrityError:
        return False  # ya existía


def evaluar_insignias_dia(empleado_id, fecha):
    """Evalúa insignias que dependen del día puntual (Centurión, Vendedora del día, Semana perfecta)."""
    db = get_db()

    # Centurión: 100+ tickets en un día
    row = db.execute(
        "SELECT SUM(tickets_cantidad) AS t FROM kpis WHERE empleado_id=? AND fecha=?",
        (empleado_id, fecha.isoformat()),
    ).fetchone()
    if row and (row["t"] or 0) >= 100:
        otorgar_insignia(empleado_id, "centurion", fecha)

    # Vendedora del día: mayor prendas_total del día en su sucursal
    emp = db.execute("SELECT sucursal FROM empleados WHERE id=?", (empleado_id,)).fetchone()
    if emp:
        top = db.execute("""
            SELECT k.empleado_id, SUM(k.prendas_total) AS p
              FROM kpis k
              JOIN empleados e ON e.id = k.empleado_id
             WHERE k.fecha=? AND e.sucursal=?
             GROUP BY k.empleado_id
             ORDER BY p DESC LIMIT 1
        """, (fecha.isoformat(), emp["sucursal"])).fetchone()
        if top and top["empleado_id"] == empleado_id and (top["p"] or 0) > 0:
            otorgar_insignia(top["empleado_id"], "vendedora_dia", fecha)

    # Semana perfecta: 5 días hábiles (lun-vie) seguidos sin incidencias y con KPIs cargados
    dias = []
    d = fecha
    while len(dias) < 5:
        if d.weekday() < 5:
            dias.append(d)
        d -= timedelta(days=1)
    ok = True
    for dd in dias:
        tiene_kpi = db.execute(
            "SELECT 1 FROM kpis WHERE empleado_id=? AND fecha=?",
            (empleado_id, dd.isoformat()),
        ).fetchone()
        tiene_inc = db.execute(
            "SELECT 1 FROM incidencias WHERE empleado_id=? AND fecha=?",
            (empleado_id, dd.isoformat()),
        ).fetchone()
        if not tiene_kpi or tiene_inc:
            ok = False
            break
    if ok:
        otorgar_insignia(empleado_id, "semana_perfecta", fecha)


def evaluar_insignias_mes():
    """Evalúa Top del mes y UPT de oro por sucursal. Se corre tras cada carga."""
    db = get_db()
    for suc in SUCURSALES:
        ranking = ranking_mensual(suc)
        if not ranking:
            continue
        # Top del mes (posición 1)
        otorgar_insignia(ranking[0]["id"], "top_mes")
        # UPT de oro (mayor UPT)
        mejor_upt = max(ranking, key=lambda x: x["upt"])
        if mejor_upt["upt"] > 0:
            otorgar_insignia(mejor_upt["id"], "upt_oro")


# --- Rutas de autenticación ---
@app.route("/", methods=["GET"])
def index():
    if "empleado_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        legajo = request.form.get("legajo", "").strip()
        pw     = request.form.get("password", "")
        emp = get_db().execute(
            "SELECT * FROM empleados WHERE legajo=? AND activo=1",
            (legajo,),
        ).fetchone()
        if emp and emp["password_hash"] == hash_pw(pw):
            session["empleado_id"] = emp["id"]
            session["legajo"]      = emp["legajo"]
            session["nombre"]      = emp["nombre"]
            session["apellido"]    = emp["apellido"]
            session["rol"]         = emp["rol"]
            session["sucursal"]    = emp["sucursal"]
            # Si es supervisor, cargo las sucursales que tiene asignadas.
            if emp["rol"] == "supervisor":
                sucs = get_db().execute(
                    "SELECT sucursal FROM supervisor_sucursales WHERE empleado_id=?",
                    (emp["id"],),
                ).fetchall()
                session["sucursales_supervisor"] = [r["sucursal"] for r in sucs]
            flash(f"Bienvenido, {emp['nombre']}!", "success")
            return redirect(url_for("dashboard"))
        flash("Legajo o contraseña incorrectos.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Sesión cerrada.", "success")
    return redirect(url_for("login"))


@app.route("/dashboard")
@login_required
def dashboard():
    if session["rol"] in ("admin", "supervisor"):
        return redirect(url_for("dashboard_admin"))
    return redirect(url_for("dashboard_empleado"))


# --- Dashboard Empleado ---
@app.route("/empleado/dashboard")
@login_required
def dashboard_empleado():
    db = get_db()
    emp_id = session["empleado_id"]
    hoy = date.today()

    # KPIs del día
    dia = db.execute("""
        SELECT COALESCE(SUM(tickets_cantidad),0) AS tickets,
               COALESCE(AVG(prendas_por_ticket),0) AS upt,
               COALESCE(SUM(prendas_total),0) AS prendas
          FROM kpis WHERE empleado_id=? AND fecha=?
    """, (emp_id, hoy.isoformat())).fetchone()

    # Resumen mensual
    mes = resumen_mensual(emp_id)

    # Posición en ranking de la sucursal
    ranking = ranking_mensual(session["sucursal"])
    posicion = next((i + 1 for i, r in enumerate(ranking) if r["id"] == emp_id), None)
    total_cajeros = len(ranking)

    # Tickets por hora de hoy
    horas = db.execute("""
        SELECT hora, SUM(tickets) AS t FROM kpis_horarios
         WHERE empleado_id=? AND fecha=?
         GROUP BY hora ORDER BY hora
    """, (emp_id, hoy.isoformat())).fetchall()
    # Serie completa 9-21
    serie_horas = []
    mapa = {r["hora"]: r["t"] for r in horas}
    for h in range(9, 22):
        serie_horas.append({"hora": h, "tickets": int(mapa.get(h, 0) or 0)})
    max_h = max((x["tickets"] for x in serie_horas), default=0) or 1

    # Historial últimos 7 días
    hist = db.execute("""
        SELECT fecha, SUM(tickets_cantidad) AS tickets,
               AVG(prendas_por_ticket) AS upt,
               SUM(prendas_total) AS prendas
          FROM kpis
         WHERE empleado_id=? AND fecha >= ?
         GROUP BY fecha ORDER BY fecha DESC
    """, (emp_id, (hoy - timedelta(days=6)).isoformat())).fetchall()

    # Insignias del empleado
    ins = db.execute(
        "SELECT tipo, fecha_otorgada FROM insignias WHERE empleado_id=? ORDER BY fecha_otorgada DESC",
        (emp_id,),
    ).fetchall()
    insignias = [{
        "tipo":  i["tipo"],
        "emoji": INSIGNIAS[i["tipo"]][0],
        "nombre":INSIGNIAS[i["tipo"]][1],
        "fecha": i["fecha_otorgada"],
    } for i in ins if i["tipo"] in INSIGNIAS]

    return render_template(
        "dashboard_empleado.html",
        dia=dia, mes=mes, posicion=posicion, total_cajeros=total_cajeros,
        serie_horas=serie_horas, max_h=max_h, historial=hist, insignias=insignias,
        hoy=hoy,
    )


@app.route("/api/empleado/refresh")
@login_required
def api_empleado_refresh():
    """Endpoint liviano para refresh automático en JS."""
    emp_id = session["empleado_id"]
    hoy = date.today()
    db = get_db()
    dia = db.execute("""
        SELECT COALESCE(SUM(tickets_cantidad),0) AS tickets,
               COALESCE(AVG(prendas_por_ticket),0) AS upt,
               COALESCE(SUM(prendas_total),0) AS prendas
          FROM kpis WHERE empleado_id=? AND fecha=?
    """, (emp_id, hoy.isoformat())).fetchone()
    mes = resumen_mensual(emp_id)
    return jsonify({
        "dia": {
            "tickets": int(dia["tickets"] or 0),
            "upt":     round(float(dia["upt"] or 0), 2),
            "prendas": round(float(dia["prendas"] or 0), 2),
        },
        "mes": mes,
    })


# --- Incidencias ---
@app.route("/empleado/incidencias", methods=["GET", "POST"])
@login_required
def incidencias_empleado():
    db = get_db()
    emp_id = session["empleado_id"]
    if request.method == "POST":
        inc_id = request.form.get("inc_id")
        comentario = request.form.get("comentario", "").strip()
        row = db.execute(
            "SELECT * FROM incidencias WHERE id=? AND empleado_id=?",
            (inc_id, emp_id),
        ).fetchone()
        if row and row["estado"] == "pendiente":
            db.execute(
                "UPDATE incidencias SET comentario_empleado=? WHERE id=?",
                (comentario, inc_id),
            )
            db.commit()
            flash("Comentario agregado.", "success")
        else:
            flash("No se puede comentar esa incidencia.", "error")
        return redirect(url_for("incidencias_empleado"))

    mes_inicio = primer_dia_mes()
    lista = db.execute("""
        SELECT * FROM incidencias
         WHERE empleado_id=? AND fecha>=?
         ORDER BY fecha DESC
    """, (emp_id, mes_inicio.isoformat())).fetchall()
    return render_template("incidencias.html", modo="empleado", lista=lista)


# --- Dashboard Admin ---
@app.route("/admin/dashboard")
@login_required
@staff_required
def dashboard_admin():
    # Para supervisor: el selector solo muestra sus sucursales y "Todas" equivale
    # a "todas las que puede ver".
    permitidas = sucursales_visibles()  # None = admin (sin restricción)
    sucursal_input = request.args.get("sucursal", "Todas")

    if permitidas is None:
        # Admin: puede ver una o todas
        opciones_sucursal = SUCURSALES
        if sucursal_input == "Todas":
            filtro_ranking = None
            filtro_horas = None
        else:
            filtro_ranking = sucursal_input
            filtro_horas = [sucursal_input]
    else:
        # Supervisor: acotado a sus sucursales
        opciones_sucursal = permitidas
        if sucursal_input in permitidas:
            filtro_ranking = [sucursal_input]
            filtro_horas = [sucursal_input]
        else:
            filtro_ranking = permitidas
            filtro_horas = permitidas
            sucursal_input = "Todas"

    ranking = ranking_mensual(filtro_ranking)
    top3 = ranking[:3]

    # Tickets por hora del día actual, filtrados por sucursales visibles
    hoy = date.today()
    db = get_db()
    q = """
        SELECT h.hora, SUM(h.tickets) AS t
          FROM kpis_horarios h
          JOIN empleados e ON e.id = h.empleado_id
         WHERE h.fecha=?
    """
    params = [hoy.isoformat()]
    if filtro_horas:
        q += " AND e.sucursal IN (%s)" % ",".join("?" * len(filtro_horas))
        params.extend(filtro_horas)
    q += " GROUP BY h.hora ORDER BY h.hora"
    rows = db.execute(q, params).fetchall()
    mapa = {r["hora"]: r["t"] for r in rows}
    serie_horas = [{"hora": h, "tickets": int(mapa.get(h, 0) or 0)} for h in range(9, 22)]
    max_h = max((x["tickets"] for x in serie_horas), default=0) or 1

    return render_template(
        "dashboard_admin.html",
        ranking=ranking, top3=top3, sucursal=sucursal_input,
        sucursales=opciones_sucursal,
        etiqueta_todas=("Todas mis sucursales" if permitidas is not None else "Todas"),
        serie_horas=serie_horas, max_h=max_h, hoy=hoy,
    )


# --- Cargar KPIs (manual y CSV) ---
@app.route("/admin/kpis", methods=["GET", "POST"])
@login_required
@admin_required
def cargar_kpis():
    db = get_db()
    if request.method == "POST" and request.form.get("modo") == "manual":
        try:
            legajo = request.form["legajo"].strip()
            fecha  = datetime.strptime(request.form["fecha"], "%Y-%m-%d").date()
            tickets  = int(request.form["tickets_cantidad"])
            upt      = float(request.form["prendas_por_ticket"])
            horas    = float(request.form.get("horas_trabajadas") or 0)
        except (KeyError, ValueError):
            flash("Datos inválidos en el formulario.", "error")
            return redirect(url_for("cargar_kpis"))

        emp = db.execute("SELECT * FROM empleados WHERE legajo=?", (legajo,)).fetchone()
        if not emp:
            flash(f"No existe empleado con legajo {legajo}.", "error")
            return redirect(url_for("cargar_kpis"))

        prendas_total = round(tickets * upt, 2)
        db.execute("""
            INSERT INTO kpis(empleado_id, fecha, tickets_cantidad, prendas_por_ticket,
                             horas_trabajadas, prendas_total, sucursal)
            VALUES (?,?,?,?,?,?,?)
        """, (emp["id"], fecha.isoformat(), tickets, upt, horas, prendas_total, emp["sucursal"]))
        db.commit()
        evaluar_insignias_dia(emp["id"], fecha)
        evaluar_insignias_mes()
        flash(f"KPI cargado para {emp['legajo']} - {emp['nombre']}.", "success")
        return redirect(url_for("cargar_kpis"))

    # Config de pesos (se actualiza desde la misma vista)
    pesos = get_pesos()
    return render_template("cargar_kpis.html", pesos=pesos)


@app.route("/admin/kpis/config", methods=["POST"])
@login_required
@admin_required
def guardar_config():
    db = get_db()
    for clave in ("peso_tickets", "peso_upt", "peso_prendas", "peso_incidencias"):
        v = request.form.get(clave)
        if v is not None:
            try:
                float(v)
                db.execute("UPDATE config SET valor=? WHERE clave=?", (v, clave))
            except ValueError:
                pass
    db.commit()
    flash("Configuración de pesos actualizada.", "success")
    return redirect(url_for("cargar_kpis"))


@app.route("/admin/kpis/csv", methods=["POST"])
@login_required
@admin_required
def importar_csv():
    f = request.files.get("csv_file")
    if not f:
        flash("Debe seleccionar un archivo CSV.", "error")
        return redirect(url_for("cargar_kpis"))

    raw = f.read().decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(raw))

    preview = request.form.get("preview") == "1"
    filas_ok, filas_err = [], []
    db = get_db()
    empleados_afectados = set()

    for i, row in enumerate(reader, start=2):  # start=2 por header
        try:
            legajo = (row.get("legajo") or "").strip()
            fecha  = datetime.strptime((row.get("fecha") or "").strip(), "%Y-%m-%d").date()
            tickets = int(row.get("tickets_cantidad") or 0)
            upt     = float(row.get("prendas_por_ticket") or 0)
            sucursal_csv = (row.get("sucursal") or "").strip().upper() or None
        except (ValueError, TypeError) as e:
            filas_err.append({"linea": i, "legajo": row.get("legajo"), "error": f"Formato inválido: {e}"})
            continue

        emp = db.execute("SELECT * FROM empleados WHERE legajo=?", (legajo,)).fetchone()
        if not emp:
            filas_err.append({"linea": i, "legajo": legajo, "error": "Legajo inexistente"})
            continue

        prendas_total = round(tickets * upt, 2)
        filas_ok.append({
            "linea": i, "legajo": legajo, "nombre": f"{emp['nombre']} {emp['apellido']}",
            "fecha": fecha.isoformat(), "tickets": tickets, "upt": upt,
            "prendas_total": prendas_total,
        })

        if not preview:
            db.execute("""
                INSERT INTO kpis(empleado_id, fecha, tickets_cantidad, prendas_por_ticket,
                                 horas_trabajadas, prendas_total, sucursal)
                VALUES (?,?,?,?,?,?,?)
            """, (emp["id"], fecha.isoformat(), tickets, upt, 0,
                  prendas_total, sucursal_csv or emp["sucursal"]))
            empleados_afectados.add((emp["id"], fecha))

    if not preview:
        db.commit()
        for emp_id, fecha in empleados_afectados:
            evaluar_insignias_dia(emp_id, fecha)
        evaluar_insignias_mes()
        flash(f"Importación: {len(filas_ok)} filas OK, {len(filas_err)} con errores.", "success")
        return redirect(url_for("cargar_kpis"))

    return render_template(
        "cargar_kpis.html",
        pesos=get_pesos(),
        preview={"ok": filas_ok, "err": filas_err, "csv_content": raw},
    )


@app.route("/admin/kpis/csv/confirmar", methods=["POST"])
@login_required
@admin_required
def confirmar_csv():
    raw = request.form.get("csv_content", "")
    reader = csv.DictReader(io.StringIO(raw))
    db = get_db()
    ok, err = 0, 0
    afectados = set()
    for i, row in enumerate(reader, start=2):
        try:
            legajo = (row.get("legajo") or "").strip()
            fecha  = datetime.strptime((row.get("fecha") or "").strip(), "%Y-%m-%d").date()
            tickets = int(row.get("tickets_cantidad") or 0)
            upt     = float(row.get("prendas_por_ticket") or 0)
            sucursal_csv = (row.get("sucursal") or "").strip().upper() or None
        except (ValueError, TypeError):
            err += 1
            continue
        emp = db.execute("SELECT * FROM empleados WHERE legajo=?", (legajo,)).fetchone()
        if not emp:
            err += 1
            continue
        prendas_total = round(tickets * upt, 2)
        db.execute("""
            INSERT INTO kpis(empleado_id, fecha, tickets_cantidad, prendas_por_ticket,
                             horas_trabajadas, prendas_total, sucursal)
            VALUES (?,?,?,?,?,?,?)
        """, (emp["id"], fecha.isoformat(), tickets, upt, 0,
              prendas_total, sucursal_csv or emp["sucursal"]))
        afectados.add((emp["id"], fecha))
        ok += 1
    db.commit()
    for emp_id, fecha in afectados:
        evaluar_insignias_dia(emp_id, fecha)
    evaluar_insignias_mes()
    flash(f"Importación confirmada: {ok} filas OK, {err} errores.", "success")
    return redirect(url_for("cargar_kpis"))


# --- Gestión de incidencias (admin) ---
@app.route("/admin/incidencias", methods=["GET", "POST"])
@login_required
@staff_required
def admin_incidencias():
    db = get_db()
    if request.method == "POST":
        # Supervisor es solo lectura: rechazamos cualquier escritura.
        if session.get("rol") != "admin":
            flash("Los supervisores no pueden modificar incidencias.", "error")
            return redirect(url_for("admin_incidencias"))
        accion = request.form.get("accion")
        if accion == "crear":
            legajo = request.form["legajo"].strip()
            tipo = request.form["tipo"].strip()
            descripcion = request.form.get("descripcion", "").strip()
            fecha = datetime.strptime(request.form["fecha"], "%Y-%m-%d").date()
            emp = db.execute("SELECT * FROM empleados WHERE legajo=?", (legajo,)).fetchone()
            if not emp:
                flash("Legajo inexistente.", "error")
            else:
                db.execute("""
                    INSERT INTO incidencias(empleado_id, tipo, descripcion, fecha, estado)
                    VALUES (?,?,?,?, 'pendiente')
                """, (emp["id"], tipo, descripcion, fecha.isoformat()))
                db.commit()
                flash("Incidencia registrada.", "success")
        elif accion == "estado":
            inc_id = request.form["inc_id"]
            nuevo  = request.form["estado"]
            if nuevo in ("pendiente", "resuelta", "desestimada"):
                db.execute("UPDATE incidencias SET estado=? WHERE id=?", (nuevo, inc_id))
                db.commit()
                flash("Estado actualizado.", "success")
        return redirect(url_for("admin_incidencias"))

    f_emp = request.args.get("empleado", "")
    f_mes = request.args.get("mes", "")
    f_est = request.args.get("estado", "")
    q = """
        SELECT i.*, e.legajo, e.nombre, e.apellido, e.sucursal
          FROM incidencias i
          JOIN empleados e ON e.id = i.empleado_id
         WHERE 1=1
    """
    params = []
    permitidas = sucursales_visibles()
    if permitidas is not None:
        if not permitidas:
            permitidas = [""]
        q += " AND e.sucursal IN (%s)" % ",".join("?" * len(permitidas))
        params.extend(permitidas)
    if f_emp:
        q += " AND e.legajo = ?"; params.append(f_emp)
    if f_mes:
        q += " AND strftime('%Y-%m', i.fecha) = ?"; params.append(f_mes)
    if f_est:
        q += " AND i.estado = ?"; params.append(f_est)
    q += " ORDER BY i.fecha DESC"
    lista = db.execute(q, params).fetchall()

    q_emp = "SELECT legajo, nombre, apellido FROM empleados WHERE rol='empleado'"
    p_emp = []
    if permitidas is not None:
        q_emp += " AND sucursal IN (%s)" % ",".join("?" * len(permitidas))
        p_emp.extend(permitidas)
    q_emp += " ORDER BY legajo"
    empleados = db.execute(q_emp, p_emp).fetchall()

    return render_template(
        "incidencias.html", modo="admin", lista=lista, empleados=empleados,
        filtros={"empleado": f_emp, "mes": f_mes, "estado": f_est},
    )


# --- Gestión de empleados (admin) ---
@app.route("/admin/empleados", methods=["GET", "POST"])
@login_required
@staff_required
def admin_empleados():
    db = get_db()

    def guardar_sucursales_supervisor(emp_id, rol):
        """Si el empleado es supervisor, persiste las sucursales marcadas en la tabla m:n."""
        db.execute("DELETE FROM supervisor_sucursales WHERE empleado_id=?", (emp_id,))
        if rol == "supervisor":
            marcadas = request.form.getlist("sucursales_cubiertas")
            for s in marcadas:
                if s in SUCURSALES:
                    db.execute(
                        "INSERT INTO supervisor_sucursales(empleado_id, sucursal) VALUES (?,?)",
                        (emp_id, s),
                    )

    if request.method == "POST":
        if session.get("rol") != "admin":
            flash("Los supervisores no pueden modificar empleados.", "error")
            return redirect(url_for("admin_empleados"))
        accion = request.form.get("accion")
        if accion == "crear":
            legajo  = request.form["legajo"].strip()
            nombre  = request.form["nombre"].strip()
            apellido= request.form["apellido"].strip()
            pw      = request.form["password"]
            sucursal= request.form["sucursal"]
            rol     = request.form.get("rol", "empleado")
            try:
                cur = db.execute("""
                    INSERT INTO empleados(legajo, nombre, apellido, password_hash, sucursal, rol, activo)
                    VALUES (?,?,?,?,?,?,1)
                """, (legajo, nombre, apellido, hash_pw(pw), sucursal, rol))
                guardar_sucursales_supervisor(cur.lastrowid, rol)
                db.commit()
                flash("Empleado creado.", "success")
            except sqlite3.IntegrityError:
                flash("Ya existe un empleado con ese legajo.", "error")
        elif accion == "editar":
            emp_id  = request.form["emp_id"]
            rol     = request.form.get("rol", "empleado")
            db.execute("""
                UPDATE empleados SET nombre=?, apellido=?, sucursal=?, rol=? WHERE id=?
            """, (request.form["nombre"].strip(), request.form["apellido"].strip(),
                  request.form["sucursal"], rol, emp_id))
            guardar_sucursales_supervisor(emp_id, rol)
            db.commit()
            flash("Empleado actualizado.", "success")
        elif accion == "toggle":
            emp_id = request.form["emp_id"]
            db.execute("UPDATE empleados SET activo = 1 - activo WHERE id=?", (emp_id,))
            db.commit()
            flash("Estado del empleado actualizado.", "success")
        elif accion == "reset_pw":
            emp_id = request.form["emp_id"]
            nueva  = request.form["nueva"]
            db.execute("UPDATE empleados SET password_hash=? WHERE id=?", (hash_pw(nueva), emp_id))
            db.commit()
            flash("Contraseña reseteada.", "success")
        return redirect(url_for("admin_empleados"))

    permitidas = sucursales_visibles()
    q = "SELECT * FROM empleados"
    params = []
    if permitidas is not None:
        # Supervisor ve a los empleados de sus sucursales (más a sí mismo si aplica)
        q += " WHERE sucursal IN (%s) OR id=?" % ",".join("?" * len(permitidas or [""]))
        params.extend(permitidas or [""])
        params.append(session["empleado_id"])
    q += " ORDER BY rol DESC, legajo"
    empleados = db.execute(q, params).fetchall()

    # Mapa empleado_id -> lista de sucursales (para supervisores)
    sup_rows = db.execute(
        "SELECT empleado_id, sucursal FROM supervisor_sucursales"
    ).fetchall()
    sup_sucs = defaultdict(list)
    for r in sup_rows:
        sup_sucs[r["empleado_id"]].append(r["sucursal"])

    return render_template(
        "empleados.html",
        empleados=empleados, sucursales=SUCURSALES, sup_sucs=sup_sucs,
    )


# --- Premios e insignias (admin) ---
@app.route("/admin/premios", methods=["GET", "POST"])
@login_required
@staff_required
def admin_premios():
    db = get_db()
    if request.method == "POST":
        if session.get("rol") != "admin":
            flash("Los supervisores no pueden registrar premios.", "error")
            return redirect(url_for("admin_premios"))
        legajo = request.form["legajo"].strip()
        descripcion = request.form["descripcion"].strip()
        fecha = datetime.strptime(request.form["fecha"], "%Y-%m-%d").date()
        emp = db.execute("SELECT * FROM empleados WHERE legajo=?", (legajo,)).fetchone()
        if not emp:
            flash("Legajo inexistente.", "error")
        else:
            db.execute(
                "INSERT INTO premios(empleado_id, descripcion, fecha) VALUES (?,?,?)",
                (emp["id"], descripcion, fecha.isoformat()),
            )
            db.commit()
            flash("Premio registrado.", "success")
        return redirect(url_for("admin_premios"))

    permitidas = sucursales_visibles()
    filtro_suc_sql = ""
    filtro_params = []
    if permitidas is not None:
        ph = ",".join("?" * len(permitidas or [""]))
        filtro_suc_sql = f" AND e.sucursal IN ({ph})"
        filtro_params = list(permitidas or [""])

    premios = db.execute(f"""
        SELECT p.*, e.legajo, e.nombre, e.apellido
          FROM premios p JOIN empleados e ON e.id = p.empleado_id
         WHERE 1=1 {filtro_suc_sql}
         ORDER BY p.fecha DESC
    """, filtro_params).fetchall()

    ins_rows = db.execute(f"""
        SELECT i.tipo, e.legajo, e.nombre, e.apellido, i.fecha_otorgada
          FROM insignias i JOIN empleados e ON e.id = i.empleado_id
         WHERE 1=1 {filtro_suc_sql}
         ORDER BY i.tipo, i.fecha_otorgada DESC
    """, filtro_params).fetchall()
    insignias_por_tipo = defaultdict(list)
    for r in ins_rows:
        if r["tipo"] in INSIGNIAS:
            insignias_por_tipo[r["tipo"]].append(dict(r))

    q_emp = "SELECT legajo, nombre, apellido FROM empleados WHERE rol='empleado'"
    p_emp = []
    if permitidas is not None:
        q_emp += " AND sucursal IN (%s)" % ",".join("?" * len(permitidas or [""]))
        p_emp.extend(permitidas or [""])
    q_emp += " ORDER BY legajo"
    empleados = db.execute(q_emp, p_emp).fetchall()

    return render_template(
        "premios.html",
        premios=premios, empleados=empleados,
        insignias_por_tipo=insignias_por_tipo, INSIGNIAS=INSIGNIAS,
    )


# --- Inicialización ---
with app.app_context():
    init_db()


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5000, debug=True)
