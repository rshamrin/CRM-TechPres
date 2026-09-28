(function () {
  const byId = (id) => document.getElementById(id);
  const numberFormat = new Intl.NumberFormat('ru-RU');
  let currentData = null;
  let dataBounds = null;

  function escapeHtml(value) {
    return String(value ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#039;');
  }

  function formatNumber(value) {
    return numberFormat.format(Number(value || 0));
  }

  function formatDate(iso) {
    if (!iso) return '—';
    const parts = String(iso).slice(0, 10).split('-');
    if (parts.length !== 3) return String(iso);
    return `${parts[2]}.${parts[1]}.${parts[0]}`;
  }

  function parseIsoDate(iso) {
    const [year, month, day] = String(iso).split('-').map(Number);
    return new Date(Date.UTC(year, month - 1, day));
  }

  function isoDate(date) {
    return date.toISOString().slice(0, 10);
  }

  function addDays(iso, days) {
    const date = parseIsoDate(iso);
    date.setUTCDate(date.getUTCDate() + days);
    return isoDate(date);
  }

  function currentParams() {
    return new URLSearchParams({
      date_from: byId('biDateFrom').value,
      date_to: byId('biDateTo').value,
      comment: byId('biComment').value.trim(),
      match_mode: byId('biMatchMode').value,
      sales: byId('biSales').value,
      event_type: byId('biEventType').value,
      granularity: byId('biGranularity').value,
    });
  }

  function setLoading(loading) {
    const button = byId('analyticsFilters').querySelector('button[type="submit"]');
    button.disabled = loading;
    button.textContent = loading ? 'Считаю…' : 'Обновить аналитику';
    byId('analyticsStatus').textContent = loading ? 'Обновление…' : 'Данные обновлены';
    byId('analyticsStatus').classList.toggle('loading', loading);
  }

  function showError(message) {
    const box = byId('analyticsError');
    box.textContent = message;
    box.hidden = !message;
  }

  function renderKpis(data) {
    const kpi = data.kpis;
    byId('kpiEvents').textContent = formatNumber(kpi.matching_events);
    byId('kpiEventsNote').textContent = `из ${formatNumber(kpi.scope_events)} событий в выбранном срезе`;
    byId('kpiClients').textContent = formatNumber(kpi.unique_clients);
    byId('kpiSales').textContent = formatNumber(kpi.active_sales);
    byId('kpiShare').textContent = `${Number(kpi.share_percent || 0).toLocaleString('ru-RU')}%`;
    byId('kpiShareNote').textContent = `от ${formatNumber(kpi.scope_events)} активностей периода`;

    const changeEl = byId('kpiChange');
    changeEl.classList.remove('positive', 'negative');
    if (kpi.change_percent === null) {
      changeEl.textContent = 'новое';
      changeEl.classList.add('positive');
    } else {
      const value = Number(kpi.change_percent || 0);
      changeEl.textContent = `${value > 0 ? '+' : ''}${value.toLocaleString('ru-RU')}%`;
      if (value > 0) changeEl.classList.add('positive');
      if (value < 0) changeEl.classList.add('negative');
    }
    byId('kpiChangeNote').textContent = `${formatNumber(kpi.previous_events)} событий за ${data.meta.previous_date_from_ru}–${data.meta.previous_date_to_ru}`;
  }

  function renderChart(data) {
    const box = byId('periodChart');
    const periods = data.periods || [];
    if (!periods.length) {
      box.innerHTML = '<div class="empty">Нет периодов для отображения</div>';
      return;
    }
    const maxValue = Math.max(1, ...periods.map(item => Number(item.events || 0)));
    const minWidth = Math.max(680, periods.length * 78);
    box.innerHTML = `<div class="bi-bars" style="min-width:${minWidth}px">
      ${periods.map(item => {
        const value = Number(item.events || 0);
        const height = Math.round((value / maxValue) * 176);
        return `<div class="bi-bar-column" title="${escapeHtml(item.label)}: ${formatNumber(value)}">
          <div class="bi-bar-value">${formatNumber(value)}</div>
          <div class="bi-bar-track"><div class="bi-bar-fill" style="height:${height}px"></div></div>
          <div class="bi-bar-label">${escapeHtml(item.label)}</div>
        </div>`;
      }).join('')}
    </div>`;

    const query = data.meta.comment
      ? `${data.meta.match_mode === 'exact' ? 'Точное совпадение' : 'Содержит'}: «${data.meta.comment}»`
      : 'Все комментарии';
    byId('chartSubtitle').textContent = `${query}. Период ${data.meta.date_from_ru}–${data.meta.date_to_ru}.`;
  }

  function renderPeriodTable(data) {
    const sales = (data.sales_breakdown || []).map(item => item.sales);
    byId('periodTableHead').innerHTML = [
      '<th>Период</th>',
      '<th>Всего</th>',
      '<th>Сделки</th>',
      ...sales.map(name => `<th>${escapeHtml(name)}</th>`),
    ].join('');

    const rows = data.periods || [];
    byId('periodTableBody').innerHTML = rows.length
      ? rows.map(row => `<tr>
          <td><strong>${escapeHtml(row.label)}</strong><div class="small">${formatDate(row.date_from)}–${formatDate(row.date_to)}</div></td>
          <td><strong>${formatNumber(row.events)}</strong></td>
          <td>${formatNumber(row.unique_clients)}</td>
          ${sales.map(name => `<td>${formatNumber((row.by_sales || {})[name] || 0)}</td>`).join('')}
        </tr>`).join('')
      : '<tr><td colspan="3" class="empty">Нет данных</td></tr>';
  }

  function renderBreakdowns(data) {
    const salesRows = data.sales_breakdown || [];
    byId('salesTableBody').innerHTML = salesRows.length
      ? salesRows.map(row => `<tr>
          <td><strong>${escapeHtml(row.sales)}</strong></td>
          <td>${formatNumber(row.events)}</td>
          <td>${formatNumber(row.unique_clients)}</td>
          <td>${Number(row.share_percent || 0).toLocaleString('ru-RU')}%</td>
          <td>${formatDate(row.latest_date)}</td>
        </tr>`).join('')
      : '<tr><td colspan="5" class="empty">Нет совпадений</td></tr>';

    const typeRows = data.event_type_breakdown || [];
    byId('eventTypeTableBody').innerHTML = typeRows.length
      ? typeRows.map(row => `<tr>
          <td><strong>${escapeHtml(row.event_type)}</strong></td>
          <td>${formatNumber(row.events)}</td>
          <td>${formatNumber(row.unique_clients)}</td>
        </tr>`).join('')
      : '<tr><td colspan="3" class="empty">Нет совпадений</td></tr>';
  }

  function renderDetails(data) {
    const rows = data.details || [];
    byId('detailsTableBody').innerHTML = rows.length
      ? rows.map(row => `<tr>
          <td class="bi-nowrap">${escapeHtml(row.date_ru)}</td>
          <td>${escapeHtml(row.sales)}</td>
          <td><a class="bi-client-link" href="/?open=${encodeURIComponent(row.client_id)}">${escapeHtml(row.client_name)}</a>${row.is_archived ? '<span class="bi-archived">архив</span>' : ''}</td>
          <td>${escapeHtml(row.event_type)}</td>
          <td class="bi-comment-cell">${escapeHtml(row.text)}</td>
        </tr>`).join('')
      : '<tr><td colspan="5" class="empty">События по условию не найдены</td></tr>';
    const limitNote = data.details_limited ? ` Показаны первые ${formatNumber(rows.length)}.` : '';
    byId('detailsSubtitle').textContent = `Найдено строк: ${formatNumber(data.details_total)}.${limitNote}`;
  }

  function renderPopularComments(data) {
    const rows = data.top_comments || [];
    const box = byId('popularComments');
    const list = byId('commentSuggestions');
    list.innerHTML = rows.map(row => `<option value="${escapeHtml(row.text)}"></option>`).join('');
    box.innerHTML = rows.length
      ? rows.map((row, index) => `<button class="bi-chip bi-comment-chip" type="button" data-index="${index}" title="${escapeHtml(row.text)}">${escapeHtml(row.text)} <strong>${formatNumber(row.count)}</strong></button>`).join('')
      : '<span class="small">Нет комментариев в выбранном срезе</span>';
    box.querySelectorAll('[data-index]').forEach(button => {
      button.addEventListener('click', () => {
        const row = rows[Number(button.dataset.index)];
        byId('biComment').value = row.text;
        byId('biMatchMode').value = 'exact';
        loadAnalytics();
      });
    });
  }

  function renderDataQuality(data) {
    const warning = byId('dataQualityWarning');
    const count = Number(data.data_quality?.suspicious_event_dates || 0);
    if (!count) {
      warning.hidden = true;
      return;
    }
    const examples = (data.data_quality.examples || []).map(escapeHtml).join(', ');
    warning.innerHTML = `Проверка качества данных: найдено подозрительных дат — <strong>${formatNumber(count)}</strong>. Они исключены из расчёта.${examples ? ` Пример: ${examples}.` : ''}`;
    warning.hidden = false;
  }

  function renderAll(data) {
    currentData = data;
    dataBounds = data.data_bounds;
    renderKpis(data);
    renderChart(data);
    renderPeriodTable(data);
    renderBreakdowns(data);
    renderDetails(data);
    renderPopularComments(data);
    renderDataQuality(data);
  }

  async function loadAnalytics() {
    showError('');
    setLoading(true);
    let failed = false;
    try {
      const response = await fetch(`/api/analytics?${currentParams().toString()}`);
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || `Ошибка ${response.status}`);
      renderAll(data);
    } catch (error) {
      failed = true;
      showError(error.message || 'Не удалось загрузить аналитику');
    } finally {
      setLoading(false);
      if (failed) byId('analyticsStatus').textContent = 'Ошибка расчёта';
    }
  }

  function applyQuickRange(range) {
    const to = byId('biDateTo').value || isoDate(new Date());
    if (range === '30' || range === '90') {
      byId('biDateFrom').value = addDays(to, -(Number(range) - 1));
    } else if (range === 'ytd') {
      byId('biDateFrom').value = `${String(to).slice(0, 4)}-01-01`;
    } else if (range === 'all' && dataBounds) {
      byId('biDateFrom').value = dataBounds.date_from;
      byId('biDateTo').value = dataBounds.date_to;
    }
    loadAnalytics();
  }

  function csvCell(value) {
    let text = String(value ?? '');
    if (/^[=+\-@]/.test(text)) text = `'${text}`;
    return `"${text.replaceAll('"', '""')}"`;
  }

  function downloadCsv() {
    if (!currentData) return;
    const header = ['Дата', 'Sales', 'Сделка', 'Тип события', 'Комментарий', 'Архив'];
    const lines = [header.map(csvCell).join(';')];
    for (const row of currentData.details || []) {
      lines.push([
        row.date_ru,
        row.sales,
        row.client_name,
        row.event_type,
        row.text,
        row.is_archived ? 'Да' : 'Нет',
      ].map(csvCell).join(';'));
    }
    const blob = new Blob([`\ufeff${lines.join('\r\n')}`], {type: 'text/csv;charset=utf-8'});
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = `crm_analytics_${currentData.meta.date_from}_${currentData.meta.date_to}.csv`;
    document.body.appendChild(link);
    link.click();
    setTimeout(() => {
      URL.revokeObjectURL(link.href);
      link.remove();
    }, 0);
    if (currentData.details_limited) {
      alert(`В CSV выгружены первые ${currentData.details.length} строк из ${currentData.details_total}.`);
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    byId('analyticsFilters').addEventListener('submit', (event) => {
      event.preventDefault();
      loadAnalytics();
    });
    byId('clearCommentBtn').addEventListener('click', () => {
      byId('biComment').value = '';
      loadAnalytics();
    });
    byId('downloadAnalyticsCsv').addEventListener('click', downloadCsv);
    document.querySelectorAll('[data-range]').forEach(button => {
      button.addEventListener('click', () => applyQuickRange(button.dataset.range));
    });
    loadAnalytics();
  });
})();
