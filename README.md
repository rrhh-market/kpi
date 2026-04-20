# Sistema de KPIs para Cajeros

Aplicación Flask + SQLite para medir desempeño mensual de cajeros, con ranking,
insignias automáticas, incidencias y premios.

## Requisitos
- Python 3.9+
- `pip install flask`
- Para la importación desde SQL Server (Dragonfish): `pip install pyodbc`
  y el driver ODBC de SQL Server instalado en el sistema.

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

## Carga masiva desde SQL Server (Dragonfish)
Desde el panel admin, "Importar desde SQL Server" ejecuta la query unificada
contra `DRAGONFISH_LURO` y `DRAGONFISH_PERALTA` y cruza con `MARKET.dbo.RRHHLegajos`
para obtener el `Nombre` de cada cajero. El admin elige `fecha_desde` y
opcionalmente `fecha_hasta` (por defecto hoy), ve un preview de filas matcheadas
(agregadas por empleado/día) más las que no tienen empleado asociado, y confirma
la importación. En cada confirmación, los KPIs diarios y horarios existentes
para cada (empleado, día) se reemplazan para evitar duplicados.

La aplicación necesita la variable de entorno `SQL_SERVER_CONN_STR` con una
cadena ODBC válida, por ejemplo:
```
SQL_SERVER_CONN_STR="DRIVER={ODBC Driver 17 for SQL Server};SERVER=host;DATABASE=MARKET;UID=usuario;PWD=secreto"
```

El matcheo a `empleados` se hace por `sucursal` (Local = LURO/PERALTA) + nombre
completo normalizado (`nombre + apellido` en cualquier orden). Si un cajero no
matchea se reporta sin abortar el resto.

## Insignias automáticas
Se evalúan al cargar KPIs:
- 🏅 Centurión — 100+ tickets en un día
- ⚡ Semana perfecta — 5 días hábiles seguidos sin incidencias
- 🔥 Top del mes — puesto 1 del ranking de la sucursal
- 🎯 UPT de oro — mayor UPT del mes en la sucursal
- 👗 Vendedora del día — más prendas totales en un día
