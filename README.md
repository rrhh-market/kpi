# Sistema de KPIs para Cajeros

Aplicación Flask + SQLite para medir desempeño mensual de cajeros, con ranking,
insignias automáticas, incidencias y premios.

## Requisitos
- Python 3.9+
- `pip install flask`

## Arranque rápido
```bash
pip install flask
python seed.py      # crea kpis.db con datos de prueba
python app.py       # servidor en http://0.0.0.0:5000
```

## Usuarios de prueba (post-seed)
- **Admin**: legajo `0`, contraseña `admin123`
- **Cajeros**: legajos `125 / 136 / 175 / 138 / 13 / 201`, contraseña `cajero123`

## Fórmula de score (mensual)
```
score = tickets·p1 + UPT_promedio·p2 + prendas·p3 − incidencias·p4
```
Los pesos `p1..p4` son configurables desde el panel admin y se guardan en la
tabla `config` como pares clave-valor.

## Estructura
```
app.py              # Flask app, rutas, lógica de score e insignias
seed.py             # datos de prueba
templates/          # Jinja2 (base, login, dashboards, etc.)
static/style.css    # CSS vanilla con variables de color
static/main.js      # refresh JS y ordenamiento de tablas
kpis.db             # SQLite (generada al correr seed.py)
```

## Carga masiva por CSV
Columnas: `legajo, fecha, tickets_cantidad, prendas_por_ticket, sucursal`
La fecha usa formato `YYYY-MM-DD`. El admin sube el archivo, ve preview y
confirma la importación; filas con legajos inexistentes o formato inválido se
reportan sin abortar el resto.

## Insignias automáticas
Se evalúan al cargar KPIs:
- 🏅 Centurión — 100+ tickets en un día
- ⚡ Semana perfecta — 5 días hábiles seguidos sin incidencias
- 🔥 Top del mes — puesto 1 del ranking de la sucursal
- 🎯 UPT de oro — mayor UPT del mes en la sucursal
- 👗 Vendedora del día — más prendas totales en un día
