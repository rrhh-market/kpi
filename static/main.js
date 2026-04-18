// JS vanilla para refresh automático del dashboard del empleado y ordenamiento de tablas

// Actualiza los valores del dashboard del empleado cada 60s usando la API liviana
function iniciarRefreshAutomatico() {
  const setTxt = (id, v) => {
    const el = document.getElementById(id);
    if (el) el.textContent = v;
  };

  const refrescar = async () => {
    try {
      const res = await fetch('/api/empleado/refresh', { credentials: 'same-origin' });
      if (!res.ok) return;
      const data = await res.json();
      setTxt('kpi-dia-tickets',  data.dia.tickets);
      setTxt('kpi-dia-upt',      data.dia.upt.toFixed(2));
      setTxt('kpi-dia-prendas',  Math.round(data.dia.prendas));
      setTxt('kpi-mes-tickets',  data.mes.tickets);
      setTxt('kpi-mes-upt',      data.mes.upt.toFixed(2));
      setTxt('kpi-mes-prendas',  Math.round(data.mes.prendas));
      setTxt('kpi-mes-incidencias', data.mes.incidencias);
      setTxt('kpi-mes-score',    data.mes.score);
    } catch (e) {
      // Silencioso: no romper la UI si falla
    }
  };

  setInterval(refrescar, 60000);
}

// Ordena una tabla <table> por columnas clickeables según data-sort = "text" | "number"
function habilitarOrdenTabla(tabla) {
  if (!tabla) return;
  const ths = tabla.querySelectorAll('th[data-sort]');
  ths.forEach((th, idx) => {
    let asc = true;
    th.addEventListener('click', () => {
      const tipo = th.dataset.sort;
      const tbody = tabla.tBodies[0];
      const filas = Array.from(tbody.rows);
      filas.sort((a, b) => {
        const va = (a.cells[idx]?.textContent || '').trim();
        const vb = (b.cells[idx]?.textContent || '').trim();
        let cmp;
        if (tipo === 'number') {
          cmp = (parseFloat(va) || 0) - (parseFloat(vb) || 0);
        } else {
          cmp = va.localeCompare(vb, 'es', { sensitivity: 'base' });
        }
        return asc ? cmp : -cmp;
      });
      filas.forEach(f => tbody.appendChild(f));
      asc = !asc;
    });
  });
}
