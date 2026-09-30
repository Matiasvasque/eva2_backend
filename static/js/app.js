/*
  Cliente ligero para consumir la API desde los templates (arquitectura híbrida).
  Guarda los JWT en localStorage y arma fetch() con el header Authorization.
*/
const API = "/api";
const PLACEHOLDER_EVENTO = "https://images.unsplash.com/photo-1501281668745-f7f57925c3b4?auto=format&fit=crop&w=900&q=70";

function decodificarJWT(token) {
  try {
    const payload = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    return JSON.parse(decodeURIComponent(escape(atob(payload))));
  } catch { return null; }
}

const Auth = {
  guardar(data) { localStorage.setItem("access", data.access); localStorage.setItem("refresh", data.refresh); localStorage.setItem("rol", data.rol || ""); },
  access() { return localStorage.getItem("access"); },
  rol() { return localStorage.getItem("rol"); },
  usuarioId() { const p = decodificarJWT(this.access() || ""); return p ? p.user_id : null; },
  autenticado() { return !!this.access(); },
  salir() { localStorage.removeItem("access"); localStorage.removeItem("refresh"); localStorage.removeItem("rol"); location.href = "/"; },
};

async function api(path, { method = "GET", body, form = false } = {}) {
  const headers = {};
  if (!form) headers["Content-Type"] = "application/json";
  if (Auth.access()) headers.Authorization = `Bearer ${Auth.access()}`;
  const payload = form ? body : (body ? JSON.stringify(body) : undefined);
  let res = await fetch(`${API}${path}`, { method, headers, body: payload });

  if (res.status === 401 && localStorage.getItem("refresh")) {
    // access vencido: intenta refrescar una vez y reintenta la llamada original.
    const r = await fetch(`${API}/token/refresh/`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh: localStorage.getItem("refresh") }),
    });
    if (r.ok) {
      const { access } = await r.json();
      localStorage.setItem("access", access);
      headers.Authorization = `Bearer ${access}`;
      res = await fetch(`${API}${path}`, { method, headers, body: payload });
    } else {
      Auth.salir();
    }
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw { status: res.status, data };
  return data;
}

function formatearCLP(n) { return "$" + Number(n).toLocaleString("es-CL"); }

/* Convierte los errores de DRF ({campo: [msgs]} o {detail: "..."}) en una frase legible. */
function formatearError(data) {
  if (!data) return "Ocurrió un error inesperado.";
  if (typeof data === "string") return data;
  if (data.detail) return data.detail;
  const partes = [];
  for (const campo in data) {
    const valor = Array.isArray(data[campo]) ? data[campo].join(" ") : data[campo];
    partes.push(`${campo}: ${valor}`);
  }
  return partes.join(" · ") || "Revisa los datos del formulario.";
}

function esc(t) {
  return String(t ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* Trae TODAS las páginas de un listado paginado (la API pagina de a 12). */
async function apiLista(path) {
  const sep = path.includes("?") ? "&" : "?";
  let todos = [];
  for (let pag = 1; pag <= 50; pag++) {
    const d = await api(`${path}${sep}page=${pag}`);
    if (Array.isArray(d)) return d;
    todos = todos.concat(d.results || []);
    if (!d.next) break;
  }
  return todos;
}

/* Texto legible para un error lanzado por api(). */
function mensajeError(err, porDefecto = "Ocurrió un error inesperado.") {
  if (!err || err.data === undefined) return "No se pudo conectar con el servidor.";
  if (err.status >= 500) return "Error del servidor (revisa la terminal donde corre runserver).";
  const t = formatearError(err.data);
  return t === "Revisa los datos del formulario." ? porDefecto : t;
}

/* Aviso flotante: toast("texto", "exito" | "fallo") */
function toast(texto, tipo = "exito") {
  let cont = document.getElementById("toasts");
  if (!cont) { cont = document.createElement("div"); cont.id = "toasts"; document.body.appendChild(cont); }
  const t = document.createElement("div");
  t.className = `toast ${tipo}`;
  t.textContent = texto;
  cont.appendChild(t);
  setTimeout(() => { t.style.opacity = "0"; setTimeout(() => t.remove(), 400); }, tipo === "fallo" ? 7000 : 4000);
}

function actualizarNav() {
  document.querySelectorAll("[data-si-auth]").forEach(el => el.style.display = Auth.autenticado() ? "" : "none");
  document.querySelectorAll("[data-si-anon]").forEach(el => el.style.display = Auth.autenticado() ? "none" : "");
  document.querySelectorAll("[data-rol]").forEach(el => el.style.display = (Auth.rol() === el.dataset.rol) ? "" : "none");
}
document.addEventListener("DOMContentLoaded", actualizarNav);
