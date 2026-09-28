const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

function escapeHtml(s) {
  return (s ?? "").toString()
    .replaceAll("&","&amp;")
    .replaceAll("<","&lt;")
    .replaceAll(">","&gt;")
    .replaceAll('"',"&quot;")
    .replaceAll("'","&#039;");
}

function isoToDateInput(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  const pad = (n) => String(n).padStart(2,"0");
  return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}`;
}

function isoToLocalInput(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  const pad = (n) => String(n).padStart(2,"0");
  return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function isoToDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  const pad = (n) => String(n).padStart(2,"0");
  return `${pad(d.getDate())}.${pad(d.getMonth()+1)}.${d.getFullYear()}`;
}

function daysAgo(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  if (isNaN(d.getTime())) return null;
  const diff = Date.now() - d.getTime();
  return Math.floor(diff / (1000*60*60*24));
}

function formatBytes(value) {
  const bytes = Number(value || 0);
  if (bytes < 1024) return `${bytes} Б`;
  const units = ["КБ", "МБ", "ГБ"];
  let size = bytes / 1024;
  let unit = units[0];
  for (let i = 1; i < units.length && size >= 1024; i += 1) {
    size /= 1024;
    unit = units[i];
  }
  return `${size >= 10 ? size.toFixed(0) : size.toFixed(1)} ${unit}`;
}

function attachmentSourceLabel(item) {
  if (!item?.event_id) return "Клиент";
  const type = item.event_type || "Событие";
  const date = isoToDate(item.event_at);
  return date ? `${type} · ${date}` : type;
}

function renderAttachmentItems(items, options={}) {
  const rows = Array.isArray(items) ? items : [];
  if (!rows.length) {
    return `<div class="attachment-empty">Вложений пока нет</div>`;
  }

  return rows.map(item => {
    const path = item.relative_path || item.original_name || "Файл";
    const source = options.showSource ? ` · ${escapeHtml(attachmentSourceLabel(item))}` : "";
    const deleteButton = options.allowDelete === false
      ? ""
      : `<button class="iconbtn attachment-delete" type="button" data-attachment-delete="${Number(item.id)}" title="Удалить вложение">🗑</button>`;
    return `
      <div class="attachment-item" data-attachment-id="${Number(item.id)}">
        <div class="attachment-icon">📎</div>
        <div class="attachment-main">
          <a class="attachment-name" href="/api/attachments/${Number(item.id)}/download" title="${escapeHtml(path)}">${escapeHtml(path)}</a>
          <div class="attachment-meta">${escapeHtml(formatBytes(item.size_bytes))}${source}</div>
        </div>
        ${deleteButton}
      </div>
    `;
  }).join("");
}

async function api(url, opts={}) {
  const res = await fetch(url, {
    headers: {"Content-Type":"application/json"},
    ...opts
  });
  if (!res.ok) {
    let msg = `Ошибка ${res.status}`;
    try {
      const j = await res.json();
      msg = j.detail || msg;
    } catch {}
    throw new Error(msg);
  }
  if (res.status === 204) return null;
  return await res.json();
}

async function uploadAttachmentFiles(url, fileList) {
  const files = Array.from(fileList || []);
  if (!files.length) return {ok: true, items: []};

  const form = new FormData();
  files.forEach(file => {
    form.append("files", file, file.name);
  });
  form.append("paths_json", JSON.stringify(files.map(file => file.webkitRelativePath || file.name)));

  const res = await fetch(url, {method: "POST", body: form});
  if (!res.ok) {
    let message = `Ошибка ${res.status}`;
    try {
      const body = await res.json();
      message = body.detail || message;
    } catch {}
    throw new Error(message);
  }
  return await res.json();
}

const LS_ADV = "miniCRM_adv_v1";
const LS_DENSITY = "miniCRM_density";

function loadAdv() {
  try {
    const j = JSON.parse(localStorage.getItem(LS_ADV) || "{}");
    return {
      filters: Array.isArray(j.filters) ? j.filters : [],
      sorts: Array.isArray(j.sorts) ? j.sorts : [],
    };
  } catch {
    return {filters:[], sorts:[]};
  }
}
function saveAdv(cfg) {
  localStorage.setItem(LS_ADV, JSON.stringify(cfg));
}

function applyDensity() {
  const v = localStorage.getItem(LS_DENSITY) || "comfortable";
  document.body.classList.toggle("compact", v === "compact");
  const btn = $("#densityToggle");
  if (btn) btn.textContent = (v === "compact") ? "Плотно" : "Комфорт";
}
function toggleDensity() {
  const v = localStorage.getItem(LS_DENSITY) || "comfortable";
  const nv = (v === "compact") ? "comfortable" : "compact";
  localStorage.setItem(LS_DENSITY, nv);
  applyDensity();
}

let META = {
  statuses: window.__STATUSES__ || [],
  deal_types: window.__DEAL_TYPES__ || [],
  sales: window.__SALES__ || [],
};

async function refreshMetaFromServer() {
  const [st, tp, sl] = await Promise.all([
    api("/api/statuses?all=0"),
    api("/api/deal-types?all=0"),
    api("/api/sales?all=0"),
  ]);
  META.statuses = st;
  META.deal_types = tp;
  META.sales = sl;
}

// -------------------- Deals page --------------------
let selectedStatusId = "";
let selectedDealTypeId = "";
let selectedClientId = null;
let editEventId = null;
let attachmentTarget = null;

function markActivePill(containerSel, id) {
  const box = $(containerSel);
  if (!box) return;
  $$(containerSel + " .pill").forEach(b => b.classList.remove("active"));
  const btn = $(`${containerSel} .pill[data-id="${id}"]`);
  if (btn) btn.classList.add("active");
}

function renderSidebarLists() {
  // statuses
  const stBox = $("#statusList");
  if (stBox) {
    const stButtons = ['<button class="pill" data-id="">Все</button>']
      .concat(META.statuses.map(s => `<button class="pill" data-id="${s.id}">${escapeHtml(s.name)}</button>`))
      .join("");
    stBox.innerHTML = stButtons;
    markActivePill("#statusList", selectedStatusId);
    stBox.addEventListener("click", (e) => {
      const b = e.target.closest(".pill");
      if (!b) return;
      selectedStatusId = b.dataset.id || "";
      markActivePill("#statusList", selectedStatusId);
      loadClients();
    });
  }

  // deal types
  const tpBox = $("#dealTypeList");
  if (tpBox) {
    const tpButtons = ['<button class="pill" data-id="">Все</button>']
      .concat(META.deal_types.map(t => `<button class="pill" data-id="${t.id}">${escapeHtml(t.name)}</button>`))
      .join("");
    tpBox.innerHTML = tpButtons;
    markActivePill("#dealTypeList", selectedDealTypeId);
    tpBox.addEventListener("click", (e) => {
      const b = e.target.closest(".pill");
      if (!b) return;
      selectedDealTypeId = b.dataset.id || "";
      markActivePill("#dealTypeList", selectedDealTypeId);
      loadClients();
    });
  }

  // update selects in dialogs if exist
  const newStatus = $("#newStatus");
  if (newStatus) {
    newStatus.innerHTML = META.statuses.map(s => `<option value="${s.id}">${escapeHtml(s.name)}</option>`).join("");
  }
  const newDealType = $("#newDealType");
  if (newDealType) {
    newDealType.innerHTML = META.deal_types.map(t => `<option value="${t.id}">${escapeHtml(t.name)}</option>`).join("");
  }

  const newSales = $("#newSales");
  if (newSales) {
    newSales.innerHTML = META.sales.map(p => `<option value="${escapeHtml(p.name)}">${escapeHtml(p.name)}</option>`).join("");
  }

}

function getQueryPayload() {
  const q = ($("#q")?.value || "").trim();
  const include_archived = !!$("#showArchived")?.checked;
  const adv = loadAdv();
  return {
    q,
    include_archived,
    status_ids: selectedStatusId ? [Number(selectedStatusId)] : [],
    deal_type_ids: selectedDealTypeId ? [Number(selectedDealTypeId)] : [],
    filters: adv.filters,
    sorts: adv.sorts,
  };
}


const COLLAPSED_TIMELINES = new Map();

function truncateText(s, n=160) {
  const t = (s ?? "").toString().replaceAll("\n"," ").trim();
  if (t.length <= n) return t;
  return t.slice(0, n-1) + "…";
}

function formatTimelineLines(items) {
  return (items || []).map(it => {
    const date = isoToDate(it.event_at);
    const da = daysAgo(it.event_at);
    const recent = (da !== null && da <= 14) ? "recent" : "";
    return `<div class="tl-line">
      <span class="tl-date ${recent}">${escapeHtml(date)}</span>
      <span class="tl-type">${escapeHtml(it.event_type || "")}</span>
      <span class="tl-text">${escapeHtml(truncateText(it.text || ""))}</span>
    </div>`;
  }).join("");
}

function renderTimelineCollapsed(r) {
  const tl = r.timeline || {items:[], more:0};
  const items = tl.items || [];
  const more = Number(tl.more || 0);
  const inner = items.length ? formatTimelineLines(items) : `<div class="small">—</div>`;
  const moreBtn = more > 0 ? `<button class="tl-more" type="button" data-act="more" data-id="${r.id}">+ ещё ${more}</button>` : "";
  return `<div class="timeline" data-cid="${r.id}" data-state="collapsed">${inner}${moreBtn}</div>`;
}

function renderTimelineExpanded(clientId, events) {
  const inner = events.length ? formatTimelineLines(events) : `<div class="small">—</div>`;
  return `<div class="timeline" data-cid="${clientId}" data-state="expanded">${inner}<button class="tl-more" type="button" data-act="less" data-id="${clientId}">Свернуть</button></div>`;
}

function renderTimelineCell(r) {
  const html = renderTimelineCollapsed(r);
  COLLAPSED_TIMELINES.set(Number(r.id), html);
  return html;
}


function renderClients(rows) {
  const body = $("#clientsBody");
  if (!body) return;
  body.innerHTML = rows.map(r => {
    const d = isoToDate(r.last_contact_at);
    const da = daysAgo(r.last_contact_at);
    const contact = r.last_contact_at
      ? `<div>${d}</div><div class="small">${da} дн. назад</div>`
      : `<div class="small">нет</div>`;
    const tl = renderTimelineCell(r);
    const active = (selectedClientId === r.id) ? "active" : "";
    return `
      <tr class="${active}" data-id="${r.id}">
        <td><div>${escapeHtml(r.name)}</div></td>
        <td>${escapeHtml(r.sales || "")}</td>
        <td><span class="badge">${escapeHtml(r.deal_type_name || "")}</span></td>
        <td><span class="badge">${escapeHtml(r.status_name || "")}</span></td>
        <td>${contact}</td>
        <td>${tl}</td>
      </tr>
    `;
  }).join("");

  body.querySelectorAll("tr").forEach(tr => {
    tr.addEventListener("click", async (ev) => {
      if (ev.target.closest("button[data-act]")) return;

      const id = Number(tr.dataset.id);
      await openClient(id);
    });
  });

  // timeline expand/collapse (delegation)
  if (!body.__tlBound) {
    body.__tlBound = true;
    body.addEventListener("click", async (e) => {
      const btn = e.target.closest("button.tl-more");
      if (!btn) return;
      e.preventDefault();
      e.stopPropagation();

      const cid = Number(btn.dataset.id);
      const act = btn.dataset.act;
      const cell = btn.closest(".timeline");
      if (!cid || !cell) return;

      if (act === "more") {
        try {
          const data = await api(`/api/clients/${cid}`);
          const events = (data.events || []).map(ev => ({
            event_at: ev.event_at,
            event_type: ev.event_type,
            text: ev.text
          }));
          cell.outerHTML = renderTimelineExpanded(cid, events);
        } catch (err) {
          alert(err.message);
        }
      }

      if (act === "less") {
        const h = COLLAPSED_TIMELINES.get(cid);
        if (h) cell.outerHTML = h;
      }
    });
  }
}


async function loadClients(opts ={}) {
  const body = $("#clientsBody");
  if (!body) return;

  const showLoading = opts.showLoading !== false;

  if (showLoading) {
  	body.innerHTML = `<tr><td colspan="6" class="small">Загрузка…</td></tr>`;
  }
	
  try {
    const rows = await api("/api/clients/query", {method:"POST", body: JSON.stringify(getQueryPayload())});
    renderClients(rows);
    // keep row highlight
  } catch (e) {
    body.innerHTML = `<tr><td colspan="6" class="small">${escapeHtml(e.message)}</td></tr>`;
  }
}

function attachmentUploadUrl(target) {
  if (target?.scope === "event") {
    return `/api/events/${Number(target.eventId)}/attachments`;
  }
  return `/api/clients/${Number(target.clientId)}/attachments`;
}

function bindAttachmentDeleteButtons(root, afterDelete) {
  if (!root) return;
  root.querySelectorAll("[data-attachment-delete]").forEach(button => {
    button.addEventListener("click", async (event) => {
      event.preventDefault();
      event.stopPropagation();
      const attachmentId = Number(button.dataset.attachmentDelete);
      if (!attachmentId || !confirm("Удалить это вложение?")) return;
      try {
        await api(`/api/attachments/${attachmentId}`, {method: "DELETE"});
        await afterDelete?.();
      } catch (error) {
        alert(error.message);
      }
    });
  });
}

async function refreshAttachmentDialog() {
  if (!attachmentTarget) return;
  const list = $("#attachmentTargetList");
  if (!list) return;

  list.innerHTML = `<div class="attachment-empty">Загрузка…</div>`;
  const data = await api(`/api/clients/${Number(attachmentTarget.clientId)}`);
  const all = data.attachments || [];
  const items = attachmentTarget.scope === "event"
    ? all.filter(item => Number(item.event_id) === Number(attachmentTarget.eventId))
    : all.filter(item => item.event_id === null || item.event_id === undefined);

  list.innerHTML = renderAttachmentItems(items);
  bindAttachmentDeleteButtons(list, async () => {
    await openClient(attachmentTarget.clientId);
    await refreshAttachmentDialog();
  });
}

async function openAttachmentDialog(target) {
  attachmentTarget = {...target};
  const dlg = $("#dlgAttachments");
  if (!dlg) return;

  $("#attachmentDialogTitle").textContent = target.scope === "event"
    ? "Вложения события"
    : "Документы клиента";
  $("#attachmentDialogSubtitle").textContent = target.label || "";
  $("#attachmentUploadStatus").textContent = "";
  if (!dlg.open) dlg.showModal();

  try {
    await refreshAttachmentDialog();
  } catch (error) {
    $("#attachmentTargetList").innerHTML = `<div class="attachment-error">${escapeHtml(error.message)}</div>`;
  }
}

function chooseAttachmentFiles(target, folder=false) {
  attachmentTarget = {...target};
  const input = folder ? $("#attachmentFolderInput") : $("#attachmentFilesInput");
  if (!input) return;
  input.value = "";
  input.click();
}

async function handleAttachmentSelection(input) {
  const files = Array.from(input?.files || []);
  if (!files.length || !attachmentTarget) return;

  const status = $("#attachmentUploadStatus");
  if (status) {
    status.classList.remove("error", "success");
    status.textContent = `Загрузка: ${files.length} файл(ов)…`;
  }

  try {
    const result = await uploadAttachmentFiles(attachmentUploadUrl(attachmentTarget), files);
    if (status) {
      status.classList.add("success");
      status.textContent = `Загружено: ${(result.items || []).length}`;
    }
    await openClient(attachmentTarget.clientId);
    await refreshAttachmentDialog();
  } catch (error) {
    if (status) {
      status.classList.add("error");
      status.textContent = error.message;
    } else {
      alert(error.message);
    }
  } finally {
    input.value = "";
  }
}

function renderDetail(data) {
  const pane = $("#detailPane");
  if (!pane) return;
  const c = data.client;
  const evs = data.events || [];
  const attachments = data.attachments || [];

  const statusOptions = META.statuses.map(s => `<option value="${s.id}" ${Number(c.status_id)===Number(s.id)?"selected":""}>${escapeHtml(s.name)}</option>`).join("");
  const typeOptions = META.deal_types.map(t => `<option value="${t.id}" ${Number(c.deal_type_id)===Number(t.id)?"selected":""}>${escapeHtml(t.name)}</option>`).join("");
  const salesOptions = META.sales.map(p => `<option value="${escapeHtml(p.name)}" ${String(c.sales||"")===String(p.name)?"selected":""}>${escapeHtml(p.name)}</option>`).join("");

  const last = c.last_contact_at ? `${isoToDate(c.last_contact_at)} (${daysAgo(c.last_contact_at)} дн.)` : "нет";

  pane.innerHTML = `
    <div class="detail-head">
      <div>
        <div style="font-weight:800; font-size:18px; line-height:1.2">${escapeHtml(c.name)}</div>
        <div class="small">Последний контакт: ${escapeHtml(last)}</div>
      </div>
      <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap">
        <button class="btn" id="btnAddEvent">+ Событие</button>
        <button class="btn" id="btnArchive">${c.is_archived ? "Разархивировать" : "В архив"}</button>
        <button class="iconbtn" id="btnEditName" title="Переименовать">✎</button>
      </div>
    </div>

    <div class="kv" style="margin-top:14px">
      <div class="k">Sales</div>
      <div><select id="dSales">${salesOptions}</select></div>

      <div class="k">Тип сделки</div>
      <div><select id="dType">${typeOptions}</select></div>

      <div class="k">Статус</div>
      <div><select id="dStatus">${statusOptions}</select></div>

      <div class="k">Теги</div>
      <div><input id="dTags" value="${escapeHtml(c.tags || "")}" placeholder="через запятую"/></div>

      <div class="k">Приоритет</div>
      <div>
        <select id="dPriority">
          ${[0,1,2,3].map(v => `<option value="${v}" ${Number(c.priority)===v?"selected":""}>${v}</option>`).join("")}
        </select>
      </div>

      <div class="k">Заметка</div>
      <div><textarea id="dNotes">${escapeHtml(c.notes || "")}</textarea></div>
    </div>

    <div class="attachments-section">
      <div class="section-row">
        <div>
          <div class="section-title">Документы клиента</div>
          <div class="small">Все вложения клиента и его событий · ${attachments.length}</div>
        </div>
        <button class="btn" type="button" id="btnClientAttachments">+ Добавить</button>
      </div>
      <div class="attachment-list client-attachments-list">
        ${renderAttachmentItems(attachments, {showSource: true})}
      </div>
    </div>

    <div class="events">
      <div class="section-title">Хронология</div>
      ${evs.length === 0 ? `<div class="small">Пока пусто</div>` : ""}
      ${evs.map(e => {
        const d = isoToDate(e.event_at);
        const eventAttachments = e.attachments || [];
        return `
          <div class="event" data-eid="${e.id}">
            <div class="event-head">
              <div>
                <div style="font-weight:700">${escapeHtml(e.event_type)}</div>
                <div class="event-meta">${escapeHtml(d)}</div>
              </div>
              <div class="event-actions">
                <button class="iconbtn" data-act="attachments" title="Вложения события">📎 ${eventAttachments.length || "+"}</button>
                <button class="iconbtn" data-act="edit" title="Редактировать">✎</button>
                <button class="iconbtn" data-act="del" title="Удалить">🗑</button>
              </div>
            </div>
            <div class="pre">${escapeHtml(e.text)}</div>
            ${eventAttachments.length ? `<div class="event-attachments">${renderAttachmentItems(eventAttachments, {allowDelete: false})}</div>` : ""}
          </div>
        `;
      }).join("")}
    </div>
  `;

  // patch handlers
  const patch = async (obj) => {
    await api(`/api/clients/${c.id}`, {method:"PATCH", body: JSON.stringify(obj)});
    await loadClients();
  };

  $("#btnArchive")?.addEventListener("click", async () => {
    await patch({is_archived: c.is_archived ? 0 : 1});
    await openClient(c.id);
  });

  $("#btnEditName")?.addEventListener("click", async (e) => {
    e.preventDefault();
    const nn = prompt("Новое название клиента:", c.name);
    if (nn === null) return;
    const name = nn.trim();
    if (!name) { alert("Название не может быть пустым"); return; }
    await patch({name});
    await openClient(c.id);
  });

  const upd = (key, getVal) => async () => {
    const v = getVal();
    await patch({[key]: v});
    const latest = await api(`/api/clients/${c.id}`);
    renderDetail(latest);
  };

  $("#dSales")?.addEventListener("change", upd("sales", () => $("#dSales").value));
  $("#dType")?.addEventListener("change", upd("deal_type_id", () => Number($("#dType").value)));
  $("#dStatus")?.addEventListener("change", upd("status_id", () => Number($("#dStatus").value)));
  $("#dTags")?.addEventListener("change", upd("tags", () => $("#dTags").value));
  $("#dPriority")?.addEventListener("change", upd("priority", () => Number($("#dPriority").value)));
  $("#dNotes")?.addEventListener("change", upd("notes", () => $("#dNotes").value));

  $("#btnAddEvent")?.addEventListener("click", () => openEventDialog(c.id, null));
  $("#btnClientAttachments")?.addEventListener("click", () => {
    openAttachmentDialog({scope: "client", clientId: c.id, label: c.name});
  });

  bindAttachmentDeleteButtons(pane.querySelector(".client-attachments-list"), async () => {
    await openClient(c.id);
  });

  pane.querySelectorAll(".event").forEach(div => {
    div.addEventListener("click", async (ev) => {
      const actBtn = ev.target.closest("button[data-act]");
      if (!actBtn) return;
      const eid = Number(div.dataset.eid);
      const act = actBtn.dataset.act;
      if (act === "attachments") {
        const eventData = evs.find(item => Number(item.id) === eid);
        const label = eventData
          ? `${eventData.event_type} · ${isoToDate(eventData.event_at)}`
          : `Событие №${eid}`;
        await openAttachmentDialog({scope: "event", clientId: c.id, eventId: eid, label});
      }
      if (act === "del") {
        if (!confirm("Удалить событие?")) return;
        await api(`/api/events/${eid}`, {method:"DELETE"});
        await openClient(c.id);
        await loadClients();
      }
      if (act === "edit") {
        openEventDialog(c.id, {id:eid, event_type: div.querySelector("div[style*='font-weight:700']").textContent, event_at: null, text: div.querySelector(".pre").textContent});
        // we re-fetch full event to get accurate fields
        const detail = await api(`/api/clients/${c.id}`);
        const found = detail.events.find(x => x.id === eid);
        if (found) openEventDialog(c.id, found);
      }
    });
  });
}

async function openClient(id) {
  const savedScrollX = window.scrollX;
  const savedScrollY = window.scrollY;

  selectedClientId = id;

  await loadClients({showLoading: false}); // to mark active row
  const data = await api(`/api/clients/${id}`);
  renderDetail(data);
  // update URL param
  const u = new URL(window.location.href);
  u.searchParams.set("open", String(id));
  history.replaceState({}, "", u.toString());
  requestAnimationFrame(() => {
    window.scroll(savedScrollX, savedScrollY);
  });
}

function openEventDialog(clientId, eventObj) {
  const dlg = $("#dlgNewEvent");
  if (!dlg) return;

  editEventId = eventObj?.id ?? null;

  $("#evType").value = eventObj?.event_type || "Звонок";
  $("#evAt").value = eventObj?.event_at ? isoToDateInput(eventObj.event_at) : "";
  $("#evText").value = eventObj?.text || "";

  dlg.showModal();

  const btn = $("#addEvent");
  btn.textContent = editEventId ? "Сохранить" : "Добавить";

  const handler = async (e) => {
    e.preventDefault();
    try {
      const payload = {
        event_type: $("#evType").value,
        event_at: $("#evAt").value ? `${$("#evAt").value}T00:00:00` : "",
        text: $("#evText").value.trim(),
      };
      if (!payload.text) throw new Error("Текст события обязателен");

      if (editEventId) {
        await api(`/api/events/${editEventId}`, {method:"PUT", body: JSON.stringify({
          event_type: payload.event_type,
          event_at: payload.event_at || `${new Date().toISOString().slice(0,10)}T00:00:00`,
          text: payload.text,
        })});
      } else {
        await api(`/api/clients/${clientId}/events`, {method:"POST", body: JSON.stringify(payload)});
      }

      dlg.close();
      await openClient(clientId);
      await loadClients();
    } catch (err) {
      alert(err.message);
    }
  };

  btn.onclick = handler;
}

function openNewClientDialog() {
  const dlg = $("#dlgNewClient");
  if (!dlg) return;
  $("#newName").value = "";
  // keep current sales selection

  $("#newTags").value = "";
  $("#newNotes").value = "";
  $("#newPriority").value = "0";
  dlg.showModal();
}

async function createClient() {
  try {
    const payload = {
      name: $("#newName").value.trim(),
      sales: ($("#newSales").value || "").trim(),
      status_id: Number($("#newStatus").value || 0),
      deal_type_id: Number($("#newDealType").value || 0),
      tags: $("#newTags").value.trim(),
      priority: Number($("#newPriority").value || 0),
      notes: $("#newNotes").value.trim(),
    };
    if (!payload.name) throw new Error("Название клиента обязательно");
    const r = await api("/api/clients", {method:"POST", body: JSON.stringify(payload)});
    $("#dlgNewClient").close();
    await loadClients();
    await openClient(r.id);
  } catch (e) {
    alert(e.message);
  }
}

// -------------------- Advanced filters/sorts dialog --------------------
const FIELD_LABELS = [
  ["name", "Клиент (name)"],
  ["sales", "Sales"],
  ["status_id", "Статус"],
  ["deal_type_id", "Тип сделки"],
  ["last_contact_days", "Последний контакт (дней назад)"],
  ["event_text", "Текст событий"],
  ["priority", "Приоритет"],
];

const SORT_FIELDS = [
  ["status", "Статус"],
  ["deal_type", "Тип сделки"],
  ["last_contact", "Последний контакт"],
  ["name", "Клиент"],
  ["sales", "Sales"],
  ["priority", "Приоритет"],
];

function filterOps(field) {
  if (field === "status_id" || field === "deal_type_id") return [["in","в списке"]];
  if (field === "last_contact_days") return [[">=","≥"], ["<=","≤"]];
  if (field === "priority") return [[">=","≥"], ["<=","≤"], ["=","="]];
  return [["contains","содержит"], ["equals","равно"]];
}

function makeSelect(opts, value) {
  return `<select class="adv-sel">${opts.map(o => `<option value="${o[0]}" ${String(value)===String(o[0])?"selected":""}>${escapeHtml(o[1])}</option>`).join("")}</select>`;
}

function renderFilterRow(row) {
  const field = row.field || "name";
  const op = row.op || filterOps(field)[0][0];

  const fieldSel = `<select class="f-field">
    ${FIELD_LABELS.map(o => `<option value="${o[0]}" ${o[0]===field?"selected":""}>${escapeHtml(o[1])}</option>`).join("")}
  </select>`;

  const opSel = `<select class="f-op">
    ${filterOps(field).map(o => `<option value="${o[0]}" ${o[0]===op?"selected":""}>${escapeHtml(o[1])}</option>`).join("")}
  </select>`;

  let valEl = "";
  if (field === "status_id") {
    const chosen = new Set((row.value || []).map(String));
    valEl = `<select class="f-val" multiple>
      ${META.statuses.map(s => `<option value="${s.id}" ${chosen.has(String(s.id))?"selected":""}>${escapeHtml(s.name)}</option>`).join("")}
    </select>`;
  } else if (field === "deal_type_id") {
    const chosen = new Set((row.value || []).map(String));
    valEl = `<select class="f-val" multiple>
      ${META.deal_types.map(t => `<option value="${t.id}" ${chosen.has(String(t.id))?"selected":""}>${escapeHtml(t.name)}</option>`).join("")}
    </select>`;
  } else if (field === "last_contact_days" || field === "priority") {
    valEl = `<input class="f-val" type="number" value="${escapeHtml(row.value ?? "")}" placeholder="число"/>`;
  } else {
    valEl = `<input class="f-val" value="${escapeHtml(row.value ?? "")}" placeholder="значение"/>`;
  }

  return `<div class="adv-row">
    ${fieldSel}
    ${opSel}
    ${valEl}
    <button class="x" type="button" title="Удалить">✕</button>
  </div>`;
}

function renderSortRow(row, idx=0, all=[]) {
  const field = row.field || "status";
  const dir = row.dir || "asc";
  const total = Array.isArray(all) ? all.length : 1;
  const fieldSel = `<select class="s-field">
    ${SORT_FIELDS.map(o => `<option value="${o[0]}" ${o[0]===field?"selected":""}>${escapeHtml(o[1])}</option>`).join("")}
  </select>`;
  const dirSel = `<select class="s-dir">
    <option value="asc" ${dir==="asc"?"selected":""}>↑</option>
    <option value="desc" ${dir==="desc"?"selected":""}>↓</option>
  </select>`;
  return `<div class="adv-row sort">
    ${fieldSel}
    ${dirSel}
    <button class="sort-move" type="button" data-move="up" title="Поднять уровень выше" ${idx===0 ? "disabled" : ""}>↑</button>
    <button class="sort-move" type="button" data-move="down" title="Опустить уровень ниже" ${idx >= total-1 ? "disabled" : ""}>↓</button>
    <button class="x" type="button" title="Удалить">✕</button>
  </div>`;
}

function ensureAdvDefaults(cfg) {
  const out = {
    filters: Array.isArray(cfg?.filters) ? cfg.filters : [],
    sorts: Array.isArray(cfg?.sorts) ? cfg.sorts : [],
  };
  if (out.filters.length === 0) out.filters = [{field:"name", op:"contains", value:""}];
  if (out.sorts.length === 0) out.sorts = [{field:"status", dir:"asc"}, {field:"last_contact", dir:"desc"}];
  return out;
}

function syncAdvDialog() {
  const cfg = ensureAdvDefaults(loadAdv());
  // Важно: диалог показывает дефолтные строки, значит они должны быть и в localStorage.
  // Иначе обработчики select/delete/move работают по пустому массиву и уровни сортировки не меняются.
  saveAdv(cfg);
  const fbox = $("#filtersBox");
  const sbox = $("#sortsBox");
  if (!fbox || !sbox) return;

  fbox.innerHTML = cfg.filters.map(renderFilterRow).join("");
  sbox.innerHTML = cfg.sorts.map(renderSortRow).join("");

  // hook delete
  fbox.querySelectorAll(".adv-row .x").forEach((b, idx) => {
    b.addEventListener("click", () => {
      const cur = loadAdv();
      cur.filters.splice(idx,1);
      saveAdv(cur);
      syncAdvDialog();
    });
  });

  sbox.querySelectorAll(".adv-row.sort .x").forEach((b, idx) => {
    b.addEventListener("click", () => {
      const cur = ensureAdvDefaults(loadAdv());
      cur.sorts.splice(idx,1);
      saveAdv(cur);
      syncAdvDialog();
    });
  });

  sbox.querySelectorAll(".adv-row.sort .sort-move").forEach((b) => {
    b.addEventListener("click", () => {
      const row = b.closest(".adv-row.sort");
      const rows = Array.from(sbox.querySelectorAll(".adv-row.sort"));
      const idx = rows.indexOf(row);
      if (idx < 0) return;
      const cur = ensureAdvDefaults(loadAdv());
      const to = b.dataset.move === "up" ? idx - 1 : idx + 1;
      if (to < 0 || to >= cur.sorts.length) return;
      [cur.sorts[idx], cur.sorts[to]] = [cur.sorts[to], cur.sorts[idx]];
      saveAdv(cur);
      syncAdvDialog();
    });
  });

  // hook field change to rerender row inputs
  fbox.querySelectorAll(".f-field").forEach((sel, idx) => {
    sel.addEventListener("change", () => {
      const cur = loadAdv();
      cur.filters[idx].field = sel.value;
      cur.filters[idx].op = filterOps(sel.value)[0][0];
      cur.filters[idx].value = (sel.value === "status_id" || sel.value === "deal_type_id") ? [] : "";
      saveAdv(cur);
      syncAdvDialog();
    });
  });

  fbox.querySelectorAll(".f-op").forEach((sel, idx) => {
    sel.addEventListener("change", () => {
      const cur = loadAdv();
      cur.filters[idx].op = sel.value;
      saveAdv(cur);
    });
  });

  // value change
  fbox.querySelectorAll(".f-val").forEach((el, idx) => {
    el.addEventListener("change", () => {
      const cur = loadAdv();
      const field = cur.filters[idx].field;
      if (field === "status_id" || field === "deal_type_id") {
        cur.filters[idx].value = Array.from(el.selectedOptions).map(o => Number(o.value));
      } else if (field === "last_contact_days" || field === "priority") {
        cur.filters[idx].value = el.value === "" ? "" : Number(el.value);
      } else {
        cur.filters[idx].value = el.value;
      }
      saveAdv(cur);
    });
  });

  sbox.querySelectorAll(".s-field").forEach((sel, idx) => {
    sel.addEventListener("change", () => {
      const cur = ensureAdvDefaults(loadAdv());
      if (!cur.sorts[idx]) cur.sorts[idx] = {field:"status", dir:"asc"};
      cur.sorts[idx].field = sel.value;
      saveAdv(cur);
    });
  });
  sbox.querySelectorAll(".s-dir").forEach((sel, idx) => {
    sel.addEventListener("change", () => {
      const cur = ensureAdvDefaults(loadAdv());
      if (!cur.sorts[idx]) cur.sorts[idx] = {field:"status", dir:"asc"};
      cur.sorts[idx].dir = sel.value;
      saveAdv(cur);
    });
  });
}

async function renderSalesSettings() {
  const slBox = $("#settingsSales");
  if (!slBox) return;

  const sl = await api("/api/sales?all=1");
  slBox.innerHTML = sl.map(p => `
    <div class="set-row" data-id="${p.id}">
      <input class="nm" value="${escapeHtml(p.name)}"/>
      <div class="small muted"> </div>
      <div class="small muted"> </div>
      <button class="trash" type="button" title="Удалить">🗑</button>
    </div>
  `).join("");

  slBox.querySelectorAll(".set-row").forEach(row => {
    const id = Number(row.dataset.id);
    row.querySelector(".trash").addEventListener("click", async () => {
      if (!confirm("Удалить sales навсегда?")) return;
      try {
        await api(`/api/sales/${id}`, {method:"DELETE"});
        await renderSalesSettings();
        await refreshMetaFromServer();
        renderSidebarLists();
        await loadClients();
      } catch (e) { alert(e.message); }
    });

    row.querySelector(".nm").addEventListener("change", async () => {
      try {
        await api(`/api/sales/${id}`, {method:"PUT", body: JSON.stringify({
          name: row.querySelector(".nm").value.trim(),
        })});
        await refreshMetaFromServer();
        renderSidebarLists();
        await loadClients();
      } catch (e) { alert(e.message); }
    });
  });

  const addButton = $("#slAddBtn");
  if (addButton) {
    addButton.onclick = async () => {
      try {
        await api("/api/sales", {method:"POST", body: JSON.stringify({
          name: $("#slNewName").value.trim(),
        })});
        $("#slNewName").value = "";
        await renderSalesSettings();
        await refreshMetaFromServer();
        renderSidebarLists();
        await loadClients();
      } catch (e) { alert(e.message); }
    };
  }
}

async function openAdvDialog() {
  const dlg = $("#dlgAdv");
  if (!dlg) return;
  syncAdvDialog();
  dlg.showModal();
  try {
    await renderSalesSettings();
  } catch (e) {
    alert(e.message);
  }
}

async function applyAdv() {
  // config already saved on change; just reload
  $("#dlgAdv")?.close();
  await loadClients();
}

function resetAdv() {
  saveAdv({filters:[], sorts:[]});
  syncAdvDialog();
}

// -------------------- Settings dialog --------------------
async function openSettings() {
  const dlg = $("#dlgSettings");
  if (!dlg) return;
  await renderSettingsLists();
  dlg.showModal();
}

async function renderSettingsLists() {
  const [st, tp] = await Promise.all([
    api("/api/statuses?all=1"),
    api("/api/deal-types?all=1"),
  ]);

  const stBox = $("#settingsStatuses");
  const tpBox = $("#settingsTypes");

  if (stBox) {
    stBox.innerHTML = st.map(s => `
      <div class="set-row" data-id="${s.id}">
        <input class="nm" value="${escapeHtml(s.name)}"/>
        <input class="ord" type="number" value="${Number(s.order_index || 100)}"/>
        <label class="check tiny"><input class="fin" type="checkbox" ${s.is_final ? "checked":""}/><span>Фин.</span></label>
        <button class="trash" type="button" title="Удалить">🗑</button>
      </div>
    `).join("");

    stBox.querySelectorAll(".set-row").forEach(row => {
      const id = Number(row.dataset.id);
      row.querySelector(".trash").addEventListener("click", async () => {
        if (!confirm("Удалить статус навсегда?")) return;
        try {
          await api(`/api/statuses/${id}`, {method:"DELETE"});
          await renderSettingsLists();
          await refreshMetaFromServer();
          renderSidebarLists();
          await loadClients();
        } catch (e) { alert(e.message); }
      });

      const save = async () => {
        try {
          await api(`/api/statuses/${id}`, {method:"PUT", body: JSON.stringify({
            name: row.querySelector(".nm").value.trim(),
            order_index: Number(row.querySelector(".ord").value || 100),
            is_final: row.querySelector(".fin").checked ? 1 : 0,
          })});
          await refreshMetaFromServer();
          renderSidebarLists();
          await loadClients();
        } catch (e) { alert(e.message); }
      };

      row.querySelector(".nm").addEventListener("change", save);
      row.querySelector(".ord").addEventListener("change", save);
      row.querySelector(".fin").addEventListener("change", save);
    });
  }

  if (tpBox) {
    tpBox.innerHTML = tp.map(t => `
      <div class="set-row" data-id="${t.id}">
        <input class="nm" value="${escapeHtml(t.name)}"/>
        <input class="ord" type="number" value="${Number(t.order_index || 100)}"/>
        <div class="small muted"> </div>
        <button class="trash" type="button" title="Удалить">🗑</button>
      </div>
    `).join("");

    tpBox.querySelectorAll(".set-row").forEach(row => {
      const id = Number(row.dataset.id);
      row.querySelector(".trash").addEventListener("click", async () => {
        if (!confirm("Удалить тип сделки навсегда?")) return;
        try {
          await api(`/api/deal-types/${id}`, {method:"DELETE"});
          await renderSettingsLists();
          await refreshMetaFromServer();
          renderSidebarLists();
          await loadClients();
        } catch (e) { alert(e.message); }
      });

      const save = async () => {
        try {
          await api(`/api/deal-types/${id}`, {method:"PUT", body: JSON.stringify({
            name: row.querySelector(".nm").value.trim(),
            order_index: Number(row.querySelector(".ord").value || 100),
          })});
          await refreshMetaFromServer();
          renderSidebarLists();
          await loadClients();
        } catch (e) { alert(e.message); }
      };

      row.querySelector(".nm").addEventListener("change", save);
      row.querySelector(".ord").addEventListener("change", save);
    });
  }
  // add new
  const statusAddButton = $("#stAddBtn");
  if (statusAddButton) {
    statusAddButton.onclick = async () => {
      try {
        await api("/api/statuses", {method:"POST", body: JSON.stringify({
          name: $("#stNewName").value.trim(),
          order_index: Number($("#stNewOrder").value || 100),
          is_final: $("#stNewFinal").checked ? 1 : 0,
        })});
        $("#stNewName").value = "";
        $("#stNewOrder").value = "100";
        $("#stNewFinal").checked = false;
        await renderSettingsLists();
        await refreshMetaFromServer();
        renderSidebarLists();
        await loadClients();
      } catch (e) { alert(e.message); }
    };
  }

  const typeAddButton = $("#tpAddBtn");
  if (typeAddButton) {
    typeAddButton.onclick = async () => {
      try {
        await api("/api/deal-types", {method:"POST", body: JSON.stringify({
          name: $("#tpNewName").value.trim(),
          order_index: Number($("#tpNewOrder").value || 100),
        })});
        $("#tpNewName").value = "";
        $("#tpNewOrder").value = "100";
        await renderSettingsLists();
        await refreshMetaFromServer();
        renderSidebarLists();
        await loadClients();
      } catch (e) { alert(e.message); }
    };
  }
}

// -------------------- Boot --------------------
function bootDealsPage() {
  applyDensity();
  $("#densityToggle")?.addEventListener("click", toggleDensity);

  renderSidebarLists();

  const q = $("#q");
  if (q) {
    let t = null;
    q.addEventListener("input", () => {
      if (t) clearTimeout(t);
      t = setTimeout(loadClients, 200);
    });
  }
  $("#showArchived")?.addEventListener("change", loadClients);

  $("#newClientBtn")?.addEventListener("click", openNewClientDialog);
  $("#createClient")?.addEventListener("click", (e) => { e.preventDefault(); createClient(); });
  $("#pickAttachmentFiles")?.addEventListener("click", () => {
    if (attachmentTarget) chooseAttachmentFiles(attachmentTarget, false);
  });
  $("#pickAttachmentFolder")?.addEventListener("click", () => {
    if (attachmentTarget) chooseAttachmentFiles(attachmentTarget, true);
  });
  $("#attachmentFilesInput")?.addEventListener("change", (event) => {
    handleAttachmentSelection(event.target);
  });
  $("#attachmentFolderInput")?.addEventListener("change", (event) => {
    handleAttachmentSelection(event.target);
  });

  // Table header quick sort: overwrite primary sort
  const thead = $("#clientsTable thead");
  if (thead) {
    thead.addEventListener("click", (e) => {
      const th = e.target.closest("th[data-sort]");
      if (!th) return;
      const field = th.dataset.sort;
      const cfg = loadAdv();
      const cur = cfg.sorts?.[0];
      let dir = "asc";
      if (cur && cur.field === field) dir = (cur.dir === "asc") ? "desc" : "asc";
      cfg.sorts = [{field, dir}].concat(cfg.sorts.filter(s => s.field !== field)).slice(0,5);
      saveAdv(cfg);
      loadClients();
    });
  }

  $("#openAdv")?.addEventListener("click", openAdvDialog);
  $("#applyAdv")?.addEventListener("click", (e) => { e.preventDefault(); applyAdv(); });
  $("#resetAdv")?.addEventListener("click", (e) => { e.preventDefault(); resetAdv(); });

  $("#addFilterRow")?.addEventListener("click", () => {
    const cfg = loadAdv();
    cfg.filters.push({field:"name", op:"contains", value:""});
    saveAdv(cfg);
    syncAdvDialog();
  });

  $("#addSortRow")?.addEventListener("click", () => {
    const cfg = loadAdv();
    cfg.sorts.push({field:"status", dir:"asc"});
    saveAdv(cfg);
    syncAdvDialog();
  });

  $("#openSettings")?.addEventListener("click", openSettings);

  // auto open client by URL param
  const u = new URL(window.location.href);
  const openId = u.searchParams.get("open");
  loadClients().then(async () => {
    if (openId) {
      try { await openClient(Number(openId)); } catch {}
    }
  });
}

function bootActivityPage() {
  applyDensity();
  $("#densityToggle")?.addEventListener("click", toggleDensity);
  const body = $("#activityBody");
  if (!body) return;

  const load = async () => {
    const days = Number($("#days").value || 7);
    body.innerHTML = `<tr><td colspan="5" class="small">Загрузка…</td></tr>`;
    try {
      const rows = await api(`/api/activity?days=${days}`);
      if (rows.length === 0) {
        body.innerHTML = `<tr><td colspan="5" class="small">Пусто 🎉</td></tr>`;
        return;
      }
      body.innerHTML = rows.map(r => {
        const last = r.last_contact_at ? `${isoToDate(r.last_contact_at)} (${daysAgo(r.last_contact_at)} дн.)` : "нет";
        return `<tr data-id="${r.id}">
          <td>${escapeHtml(r.name)}</td>
          <td>${escapeHtml(r.sales || "")}</td>
          <td>${escapeHtml(r.deal_type_name || "")}</td>
          <td>${escapeHtml(r.status_name || "")}</td>
          <td>${escapeHtml(last)}</td>
        </tr>`;
      }).join("");
      body.querySelectorAll("tr[data-id]").forEach(tr => {
        tr.addEventListener("click", () => {
          window.location.href = `/?open=${tr.dataset.id}`;
        });
      });
    } catch (e) {
      body.innerHTML = `<tr><td colspan="5" class="small">${escapeHtml(e.message)}</td></tr>`;
    }
  };

  $("#refresh")?.addEventListener("click", load);
  $("#days")?.addEventListener("change", load);
  load();
}

document.addEventListener("DOMContentLoaded", () => {
  if ($("#clientsBody")) bootDealsPage();
  if ($("#activityBody")) bootActivityPage();
});
