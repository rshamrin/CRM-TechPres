(function () {
  const SORT_STORAGE_KEY = 'miniCRM_adv_v1';
  const daysInput = document.getElementById('reportDays');
  const buildBtn = document.getElementById('buildReportBtn');
  const copyBtn = document.getElementById('copyReportBtn');
  const dlBtn = document.getElementById('downloadReportBtn');
  const meta = document.getElementById('reportMeta');
  const preview = document.getElementById('reportPreview');

  function getDays() {
    const v = parseInt(daysInput.value || '14', 10);
    if (Number.isNaN(v) || v < 1) return 1;
    if (v > 365) return 365;
    return v;
  }

  function setMeta(m) {
    if (!m) return;
    const sorting = m.sort_summary ? ` Сортировка внутри разделов: ${m.sort_summary}.` : '';
    meta.textContent = `В отчёт попали сделки с активностью за последние ${m.days} дней. Период: ${m.date_from} – ${m.date_to}.${sorting}`;
  }

  function currentSorts() {
    try {
      const cfg = JSON.parse(localStorage.getItem(SORT_STORAGE_KEY) || '{}');
      return Array.isArray(cfg.sorts) ? cfg.sorts : [];
    } catch {
      return [];
    }
  }

  async function build() {
    const days = getDays();
    localStorage.setItem('reportDays', String(days));
    preview.innerHTML = '<div class="empty">Формирую…</div>';
    const params = new URLSearchParams({
      days: String(days),
      sorts: JSON.stringify(currentSorts()),
    });
    try {
      const r = await fetch(`/api/report?${params.toString()}`);
      const j = await r.json();
      if (!r.ok || !j || !j.ok) throw new Error(j?.detail || 'Не удалось сформировать отчёт');
      setMeta(j.meta);
      preview.innerHTML = j.html;
    } catch (error) {
      console.error(error);
      preview.innerHTML = '<div class="empty">Не удалось сформировать отчёт.</div>';
    }
  }

  function stripHtml(html) {
    const tmp = document.createElement('div');
    tmp.innerHTML = html;
    return (tmp.textContent || tmp.innerText || '').trim();
  }

  async function copyToClipboard() {
    // copy last built html (from preview)
    const html = preview.innerHTML || '';
    if (!html || html.indexOf('Статус сделок') === -1) {
      alert('Сначала сформируй отчёт.');
      return;
    }

    try {
      // Rich clipboard (HTML) when supported
      if (window.ClipboardItem && navigator.clipboard && navigator.clipboard.write) {
        const blobHtml = new Blob([html], { type: 'text/html' });
        const blobText = new Blob([stripHtml(html)], { type: 'text/plain' });
        const item = new ClipboardItem({ 'text/html': blobHtml, 'text/plain': blobText });
        await navigator.clipboard.write([item]);
      } else if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(stripHtml(html));
      } else {
        // fallback
        const ta = document.createElement('textarea');
        ta.value = stripHtml(html);
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
      }
      alert('Отчёт скопирован. Открой Outlook → Новое письмо → вставь (Cmd+V).');
    } catch (e) {
      console.error(e);
      alert('Не удалось скопировать. Попробуй “Скачать HTML”.');
    }
  }

  function download() {
    const html = preview.innerHTML || '';
    if (!html || html.indexOf('Статус сделок') === -1) {
      alert('Сначала сформируй отчёт.');
      return;
    }
    const days = getDays();
    const full = `<!doctype html><html lang="ru"><head><meta charset="utf-8"></head><body>${html}</body></html>`;
    const blob = new Blob([full], { type: 'text/html;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `crm_report_${days}d.html`;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => {
      URL.revokeObjectURL(a.href);
      document.body.removeChild(a);
    }, 0);
  }

  // init
  const saved = localStorage.getItem('reportDays');
  if (saved) {
    daysInput.value = saved;
  }

  buildBtn.addEventListener('click', build);
  copyBtn.addEventListener('click', copyToClipboard);
  dlBtn.addEventListener('click', download);
})();
