"""
Seed de datos de prueba para el sistema de KPIs.
Inserta: 1 admin, 6 cajeros, KPIs de últimos 30 días, datos por hora de hoy,
incidencias variadas e insignias otorgadas.

Uso:
    python seed.py
"""
import os
import random
import sqlite3
from datetime import date, datetime, timedelta
from hashlib import sha256

from app import init_db, DB_PATH, INSIGNIAS


def hash_pw(pw):
    return sha256(pw.encode("utf-8")).hexdigest()


def limpiar(conn):
    """Vacía las tablas antes de insertar datos de prueba."""
    cur = conn.cursor()
    for t in ("insignias", "premios", "incidencias", "kpis_horarios", "kpis", "empleados"):
        cur.execute(f"DELETE FROM {t}")
    conn.commit()


def sembrar():
    init_db()  # asegura esquema + pesos por defecto
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    limpiar(conn)

    # --- Usuarios ---
    # Admin
    cur.execute("""
        INSERT INTO empleados(legajo, nombre, apellido, password_hash, sucursal, rol, activo)
        VALUES (?, ?, ?, ?, ?, 'admin', 1)
    """, ("0", "Admin", "General", hash_pw("admin123"), "CENTRAL"))

    # Cajeros (legajos estilo del enunciado)
    cajeros = [
        ("125", "Nestor",   "Caceres",  "CENTRAL"),
        ("136", "Luciana",  "Romero",   "LURO"),
        ("175", "Pablo",    "Gomez",    "CENTRAL"),
        ("138", "Sofia",    "Lopez",    "PERALTA"),
        ("13",  "Marcelo",  "Diaz",     "TALCA"),
        ("201", "Valentina","Sosa",     "LURO"),
    ]
    for legajo, nombre, apellido, suc in cajeros:
        cur.execute("""
            INSERT INTO empleados(legajo, nombre, apellido, password_hash, sucursal, rol, activo)
            VALUES (?, ?, ?, ?, ?, 'empleado', 1)
        """, (legajo, nombre, apellido, hash_pw("cajero123"), suc))
    conn.commit()

    # Supervisora de ejemplo: cubre CENTRAL y LURO (solo lectura).
    cur.execute("""
        INSERT INTO empleados(legajo, nombre, apellido, password_hash, sucursal, rol, activo)
        VALUES (?, ?, ?, ?, ?, 'supervisor', 1)
    """, ("500", "Carolina", "Gutierrez", hash_pw("super123"), "CENTRAL"))
    sup_id = cur.lastrowid
    for s in ("CENTRAL", "LURO"):
        cur.execute(
            "INSERT INTO supervisor_sucursales(empleado_id, sucursal) VALUES (?, ?)",
            (sup_id, s),
        )
    conn.commit()

    empleados = {
        r["legajo"]: dict(r)
        for r in cur.execute("SELECT id, legajo, nombre, apellido, sucursal FROM empleados WHERE rol='empleado'")
    }

    # --- KPIs últimos 30 días ---
    hoy = date.today()
    random.seed(42)
    for dias in range(30, 0, -1):
        f = hoy - timedelta(days=dias)
        if f.weekday() == 6:  # domingo, no laborable
            continue
        for leg, emp in empleados.items():
            tickets = random.randint(80, 180)
            upt = round(random.uniform(4.0, 14.0), 2)
            horas = round(random.uniform(6.0, 9.0), 1)
            prendas = round(tickets * upt, 2)
            cur.execute("""
                INSERT INTO kpis(empleado_id, fecha, tickets_cantidad, prendas_por_ticket,
                                 horas_trabajadas, prendas_total, sucursal)
                VALUES (?,?,?,?,?,?,?)
            """, (emp["id"], f.isoformat(), tickets, upt, horas, prendas, emp["sucursal"]))

    # KPIs del día de hoy también
    for leg, emp in empleados.items():
        tickets = random.randint(80, 180)
        upt = round(random.uniform(4.0, 14.0), 2)
        horas = round(random.uniform(6.0, 9.0), 1)
        prendas = round(tickets * upt, 2)
        cur.execute("""
            INSERT INTO kpis(empleado_id, fecha, tickets_cantidad, prendas_por_ticket,
                             horas_trabajadas, prendas_total, sucursal)
            VALUES (?,?,?,?,?,?,?)
        """, (emp["id"], hoy.isoformat(), tickets, upt, horas, prendas, emp["sucursal"]))

    # --- Datos por hora de hoy (horario comercial 9-21, pico 15-17) ---
    # Distribución simulada de pesos por hora
    pesos_horarios = {
        9: 0.4, 10: 0.6, 11: 0.7, 12: 0.8, 13: 0.7, 14: 0.7,
        15: 1.2, 16: 1.4, 17: 1.3, 18: 1.0, 19: 0.8, 20: 0.5, 21: 0.3,
    }
    for leg, emp in empleados.items():
        total_dia = random.randint(80, 180)
        suma = sum(pesos_horarios.values())
        for hora, peso in pesos_horarios.items():
            base = int(total_dia * peso / suma)
            variacion = random.randint(-2, 3)
            tickets_h = max(0, base + variacion)
            if tickets_h > 0:
                cur.execute("""
                    INSERT INTO kpis_horarios(empleado_id, fecha, hora, tickets)
                    VALUES (?,?,?,?)
                """, (emp["id"], hoy.isoformat(), hora, tickets_h))

    # --- Incidencias cargadas por admin (disciplinarias; penalizan score) ---
    incidencias_admin = [
        ("136", "Tardanza",        "Llegó 15 minutos tarde",          hoy - timedelta(days=5),  "pendiente"),
        ("136", "Ausencia",        "No asistió sin aviso",            hoy - timedelta(days=12), "resuelta"),
        ("175", "Trato al cliente","Queja de un cliente",             hoy - timedelta(days=3),  "pendiente"),
        ("138", "Desempeño",       "UPT por debajo del objetivo",     hoy - timedelta(days=8),  "resuelta"),
        ("13",  "Tardanza",        "Tardanza de 10 minutos",          hoy - timedelta(days=2),  "desestimada"),
    ]
    for leg, tipo, desc, f, est in incidencias_admin:
        emp = empleados[leg]
        cur.execute("""
            INSERT INTO incidencias
                (empleado_id, tipo, descripcion, fecha, estado,
                 origen, estado_aprobacion)
            VALUES (?,?,?,?,?, 'admin', 'no_aplica')
        """, (emp["id"], tipo, desc, f.isoformat(), est))

    # --- Auto-reportes del empleado (no penalizan score) ---
    # (legajo, tipo, descripción, días_atrás, estado, estado_aprob, coment_admin)
    autoreportes = [
        ("125", "Olvido de fichada (salida)",
         "Olvidé fichar al salir el lunes 21hs.", 1,
         "pendiente", "pendiente_aprobacion", None),
        ("201", "Permiso médico",
         "Turno con cardiólogo, adjunto certificado físico.", 2,
         "resuelta", "aprobada", "Recibido, OK."),
        ("138", "Justificación de tardanza",
         "Corte de luz en el subte.", 1,
         "desestimada", "rechazada", "Ya se había registrado en planilla."),
    ]
    for leg, tipo, desc, dias, est, aprob, coment in autoreportes:
        emp = empleados[leg]
        f = hoy - timedelta(days=dias)
        cur.execute("""
            INSERT INTO incidencias
                (empleado_id, tipo, descripcion, fecha, estado,
                 origen, estado_aprobacion, comentario_admin, fecha_carga)
            VALUES (?,?,?,?,?, 'empleado', ?, ?, ?)
        """, (emp["id"], tipo, desc, f.isoformat(), est, aprob, coment,
              datetime.now().isoformat(timespec="seconds")))

    # --- Premios de ejemplo ---
    premios_mock = [
        ("125", "Vale de compra $10.000 por cierre de mes",       hoy - timedelta(days=15)),
        ("201", "Día libre a elección",                            hoy - timedelta(days=9)),
    ]
    for leg, desc, f in premios_mock:
        emp = empleados[leg]
        cur.execute(
            "INSERT INTO premios(empleado_id, descripcion, fecha) VALUES (?,?,?)",
            (emp["id"], desc, f.isoformat()),
        )

    # --- Insignias pre-otorgadas ---
    insignias_mock = [
        ("125", "centurion",       hoy - timedelta(days=4)),
        ("125", "top_mes",         hoy - timedelta(days=1)),
        ("201", "upt_oro",         hoy - timedelta(days=2)),
        ("175", "vendedora_dia",   hoy - timedelta(days=6)),
        ("138", "semana_perfecta", hoy - timedelta(days=3)),
    ]
    for leg, tipo, f in insignias_mock:
        emp = empleados[leg]
        try:
            cur.execute("""
                INSERT INTO insignias(empleado_id, tipo, fecha_otorgada)
                VALUES (?,?,?)
            """, (emp["id"], tipo, f.isoformat()))
        except sqlite3.IntegrityError:
            pass  # ya existe

    conn.commit()
    conn.close()

    print("✓ Seed completado.")
    print("  Admin:      legajo=0    pass=admin123")
    print("  Supervisor: legajo=500  pass=super123  (sucursales: CENTRAL, LURO)")
    print("  Cajeros:")
    for leg, nombre, apellido, suc in cajeros:
        print(f"    legajo={leg:>4}  {nombre} {apellido} ({suc})  pass=cajero123")


if __name__ == "__main__":
    # Borra DB si existe para arrancar limpio
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    sembrar()
