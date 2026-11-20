'use strict';
const $ = (selector) => document.querySelector(selector);
const icons = {
  nodes: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  subscription: '<path d="M5 4h10l4 4v12H5z"/><path d="M14 4v5h5M9 13h6m-6 4h6"/>',
  logs: '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 8h8m-8 4h8m-8 4h5"/>',
  settings: '<path d="m9 4 1-2h4l1 2 2 1 2-.2 2 3-1 2v3l1 2-2 3-2-.2-2 1-1 2h-4l-1-2-2-1-2 .2-2-3 1-2v-3l-1-2 2-3 2 .2z"/><circle cx="12" cy="11.5" r="3"/>',
  server: '<rect x="3" y="3" width="18" height="7" rx="2"/><rect x="3" y="14" width="18" height="7" rx="2"/><path d="M7 6.5h.01M7 17.5h.01m4-11h6m-6 11h6"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  download: '<path d="M12 3v12m-4-4 4 4 4-4M4 16v5h16v-5"/>',
  activity: '<path d="M2 12h5l3-8 4 16 3-8h5"/>',
  route: '<circle cx="6" cy="6" r="3"/><circle cx="18" cy="18" r="3"/><path d="M9 6h6a3 3 0 0 1 0 6H9a3 3 0 0 0 0 6h6"/>',
  refresh: '<path d="M20 8a8 8 0 0 0-14-2L3 9m0-6v6h6m-5 7a8 8 0 0 0 14 2l3-3m0 6v-6h-6"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v.1"/>',
  shield: '<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6z"/><path d="m8 12 3 3 5-6"/>',
  terminal: '<rect x="2" y="4" width="20" height="16" rx="2"/><path d="m6 9 3 3-3 3m6 0h5"/>',
  power: '<path d="M12 2v10M6 5a9 9 0 1 0 12 0"/>',
  copy: '<rect x="8" y="8" width="12" height="13" rx="2"/><path d="M16 8V3H3v13h5"/>',
  lock: '<rect x="4" y="10" width="16" height="11" rx="2"/><path d="M8 10V6a4 4 0 0 1 8 0v4m-4 5v2"/>',
  close: '<path d="m6 6 12 12M6 18 18 6"/>',
  edit: '<path d="m16 3 5 5-12 12H4v-5zM13 6l5 5"/>',
  trash: '<path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7"/>',
};
const icon = (name) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name] || icons.nodes}</svg>`;
document.querySelectorAll('[data-icon]').forEach(el => { el.innerHTML = icon(el.dataset.icon); });
const escape = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const protocol = (name) => ({vless: 'VLESS', vmess: 'VMess', trojan: 'Trojan', ss: 'SS', hysteria2: 'HY2', tuic: 'TUIC', http: 'HTTP', socks5: 'SOCKS5'}[name] || name);
let state = null, access = null, page = 'nodes', refreshing = false, confirmAction = null, toastTimer = null, previousTraffic = null;
const watchedJobs = new Map();

function bytes(value, rate = false) {
  if (value == null || !Number.isFinite(Number(value))) return '—';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let amount = Number(value), index = 0;
  while (amount >= 1000 && index < units.length - 1) { amount /= 1000; index++; }
  return `${amount >= 100 || index === 0 ? amount.toFixed(0) : amount.toFixed(1)} ${units[index]}${rate ? '/s' : ''}`;
}
const stamp = (seconds) => seconds ? new Date(seconds * 1000).toLocaleString('zh-CN', {month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'}) : '尚未更新';
function toast(message, error = false) {
  clearTimeout(toastTimer);
  $('#toast').textContent = message; $('#toast').classList.toggle('error', error); $('#toast').hidden = false;
  toastTimer = setTimeout(() => { $('#toast').hidden = true; }, error ? 7000 : 4000);
}
async function api(path, options = {}) {
  const headers = {'Content-Type': 'application/json'};
  const response = await fetch(path, {headers, ...options, body: options.body === undefined ? undefined : JSON.stringify(options.body)});
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || '操作失败，请重试');
  }
  return data;
}
async function busy(button, action) {
  if (button?.disabled) return;
  if (button) button.disabled = true;
  try { return await action(); } finally { if (button) button.disabled = false; }
}
async function formAction(button, errorSelector, action) {
  $(errorSelector).hidden = true;
  try { await busy(button, action); } catch (error) { $(errorSelector).textContent = error.message; $(errorSelector).hidden = false; }
}

function renderState() {
  if (!state) return;
  const core = state.core, current = state.nodes.find(n => n.id === state.selected);
  $('#owner-name').textContent = '当前用户 · ' + core.user;
  $('#core-status').textContent = core.running ? '代理运行中' : '代理已停止';
  $('#core-status').classList.toggle('stopped', !core.running);
  $('#current-name').textContent = current?.name || '尚未选择';
  $('#current-detail').textContent = current ? `${protocol(current.type)}${current.security ? ' · ' + current.security : ''} · 新连接使用此节点` : '导入一个节点开始使用';
  $('#proxy-address').textContent = `127.0.0.1:${core.proxy_port}`;
  const formatted = bytes(core.download).split(' ');
  $('#total-download').innerHTML = `${escape(formatted[0])} <small>${escape(formatted[1])}</small>`;
  $('#connection-count').textContent = core.connections;
  $('#upload-detail').textContent = '累计上传 ' + bytes(core.upload);
  const now = Date.now();
  if (previousTraffic && core.pid === previousTraffic.pid) {
    const rate = Math.max(0, (core.download - previousTraffic.download) / ((now - previousTraffic.at) / 1000));
    $('#live-download').textContent = '当前下载 ' + bytes(rate, true);
  } else $('#live-download').textContent = '当前下载 0 B/s';
  previousTraffic = {at: now, pid: core.pid, download: core.download};
  $('#core-warning').hidden = core.running && !core.last_error;
  $('#core-warning').textContent = core.last_error || '代理已停止。可到“使用设置”中启动，节点和订阅仍可管理。';
  $('#node-count').textContent = state.nodes.length; $('#nav-count').textContent = state.nodes.length;
  $('#last-sync').textContent = new Date().toLocaleTimeString('zh-CN', {hour: '2-digit', minute: '2-digit'}) + ' 已同步';
  const endpoint = `http://127.0.0.1:${core.proxy_port}`;
  if (access) $('#proxy-command').textContent = `export http_proxy='${access.http}'\nexport https_proxy='${access.http}'\nexport all_proxy='${access.socks}'\nexport no_proxy=localhost,127.0.0.1,::1,${location.hostname}\nexport HTTP_PROXY="$http_proxy" HTTPS_PROXY="$https_proxy"\nexport ALL_PROXY="$all_proxy" NO_PROXY="$no_proxy"`;
  $('#settings-endpoint').textContent = endpoint;
  $('#process-details').textContent = core.running ? `状态：运行中　·　PID ${core.pid}\n代理端口：${core.proxy_port}（仅本机）` : `状态：已停止\n代理端口：${core.proxy_port}（仅本机）`;
  $('#core-toggle').textContent = core.running ? '停止代理' : '启动代理';
  renderNodes(); renderSubscriptions(); renderJobs();
}
function renderNodes() {
  if (!state) return;
  const needle = $('#search').value.trim().toLowerCase(), type = $('#type-filter').value;
  const list = state.nodes.filter(n => (!type || n.type === type) && `${n.name} ${n.server}`.toLowerCase().includes(needle));
  $('#filtered-count').textContent = `${list.length} 个节点`;
  $('#test-all-delay').disabled = $('#test-all-speed').disabled = state.nodes.length === 0;
  if (!list.length) {
    $('#node-rows').innerHTML = `<tr><td colspan="6"><div class="empty-state"><strong>${state.nodes.length ? '没有匹配的节点' : '添加你的第一个节点'}</strong>${state.nodes.length ? '换个关键词，或调整协议筛选。' : '点击右上角“导入节点”，或在订阅管理中添加来源。'}</div></td></tr>`;
    return;
  }
  $('#node-rows').innerHTML = list.map(n => {
    const result = n.result, selected = n.id === state.selected;
    const source = n.source ? state.subscriptions.find(s => s.id === n.source)?.name || '订阅' : '手动导入';
    const delay = result?.delay != null ? `<span class="metric-value">${escape(result.delay)} <small>ms</small></span>` : result?.error ? '<span class="error-text">未连通</span>' : '<span class="untested">未测试</span>';
    const speed = result?.speed != null ? `<span class="metric-value">${escape(bytes(result.speed, true))}</span>` : result?.error ? '<span class="error-text">未完成</span>' : '<span class="untested">未测速</span>';
    return `<tr data-node-id="${escape(n.id)}" class="${selected ? 'selected' : ''}"><td><div class="node-cell"><span class="node-symbol">${escape(protocol(n.type).slice(0, 1))}</span><div class="node-text"><span class="node-name" title="${escape(n.name)}">${escape(n.name)}</span><span class="node-address" title="${escape(n.server)}:${n.port}">${escape(n.server)}:${n.port}</span></div></div></td><td><span class="protocol-tag">${escape(protocol(n.type))}</span><span class="security-label">${escape([n.security, n.network.toUpperCase()].filter(Boolean).join(' · '))}</span></td><td title="${escape(result?.error || (result ? '测试于 ' + stamp(result.at) : '尚未测试'))}">${delay}</td><td title="${escape(result?.error || (result?.speed_at ? '2 MB 实测 · ' + stamp(result.speed_at) : '2 MB 文件实测'))}">${speed}</td><td><span class="source-tag" title="${escape(source)}">${escape(source)}</span></td><td><div class="row-actions">${selected ? '<span class="active-label">当前</span>' : '<button class="switch-button" data-action="select">切换</button>'}<button class="icon-button" data-action="delay" title="测试连通性" aria-label="测试连通性">${icon('activity')}</button><button class="icon-button" data-action="speed" title="测试下载速度" aria-label="测试下载速度">${icon('download')}</button><button class="icon-button" data-action="edit" title="编辑节点" aria-label="编辑节点">${icon('edit')}</button><button class="icon-button" data-action="delete" title="删除节点" aria-label="删除节点">${icon('trash')}</button></div></td></tr>`;
  }).join('');
}
function renderSubscriptions() {
  if (!state) return;
  if (!state.subscriptions.length) {
    $('#subscription-list').innerHTML = '<section class="panel empty-state"><strong>暂时没有订阅</strong>添加订阅链接，集中更新和管理节点。</section>';
    return;
  }
  $('#subscription-list').innerHTML = state.subscriptions.map(s => `<article class="panel subscription-card" data-sub-id="${escape(s.id)}"><div class="subscription-symbol">${icon('subscription')}</div><div class="subscription-body"><h2>${escape(s.name)}</h2><p class="meta">${escape(s.url_display)} · ${s.count} 个节点 · ${s.via_proxy ? '通过代理更新' : '直连更新'}</p><p class="meta">${escape(stamp(s.updated_at))} · ${s.interval ? '每 ' + s.interval + ' 小时自动更新' : '仅手动更新'}</p>${s.error ? `<p class="subscription-error">${escape(s.error)}</p>` : ''}</div><div class="subscription-actions"><button class="button secondary compact" data-action="refresh-sub">${icon('refresh')}更新节点</button><button class="icon-button" data-action="delete-sub" title="删除订阅及其节点" aria-label="删除订阅及其节点">${icon('trash')}</button></div></article>`).join('');
}
async function refreshState(silent = false) {
  if (refreshing) return;
  refreshing = true;
  try { state = await api('/api/state'); renderState(); }
  catch (error) { if (!silent) toast(error.message, true); }
  finally { refreshing = false; }
}
async function showApp() {
  state = await api('/api/state');
  renderState();
  $('#loading').hidden = true;
  $('#app').hidden = false;
  const active = await api('/api/jobs');
  for (const job of active.jobs) watchJob(job);
  if (page === 'settings') loadAccess();
}
async function loadAccess() {
  try { access = await api('/api/access'); renderState(); }
  catch (error) { toast(error.message, true); }
}
function navigate(target) {
  page = target;
  document.querySelectorAll('.page').forEach(el => { el.hidden = el.id !== 'page-' + target; });
  document.querySelectorAll('.nav-item').forEach(el => el.classList.toggle('active', el.dataset.page === target));
  $('#breadcrumb').textContent = {nodes: '节点管理', subscriptions: '订阅管理', logs: '运行日志', settings: '使用设置'}[target];
  if (target === 'logs') refreshLogs();
  if (target === 'settings') loadAccess();
}
function openDialog(id) {
  const dialog = $(id);
  dialog.querySelectorAll('.form-error').forEach(el => { el.hidden = true; });
  dialog.showModal();
}
function confirm(title, message, action) {
  $('#confirm-title').textContent = title; $('#confirm-message').textContent = message; confirmAction = action;
  openDialog('#confirm-dialog');
}
function renderJobs() {
  const jobs = [...watchedJobs.values()];
  $('#job-banner').hidden = jobs.length === 0;
  if (!jobs.length) return;
  const job = jobs[0];
  $('#job-title').textContent = job.kind === 'refresh' ? '正在更新订阅' : job.kind === 'speed' ? '正在测试下载速度' : '正在测试连通性';
  const parallel = job.parallel_limit > 1 ? ` · 最多 ${job.parallel_limit} 个并行` : '';
  $('#job-detail').textContent = `${job.done || 0} / ${job.total || 1} 完成${parallel}${jobs.length > 1 ? ' · 共 ' + jobs.length + ' 个任务' : ''}`;
  $('#job-progress').value = job.done || 0; $('#job-progress').max = job.total || 1;
}
async function watchJob(result) {
  const id = result.id;
  if (watchedJobs.has(id)) return;
  watchedJobs.set(id, {kind: 'delay', ...result}); renderJobs();
  const poll = async () => {
    if (!watchedJobs.has(id)) return;
    try {
      const job = await api('/api/jobs/' + id);
      watchedJobs.set(id, job); renderJobs();
      if (job.status === 'done' || job.status === 'error') {
        watchedJobs.delete(id); renderJobs(); await refreshState();
        if (job.status === 'error') toast(job.error, true);
        else if (job.kind === 'refresh') toast('订阅更新完成');
        else { const failed = job.results.filter(r => r.error).length; toast(failed ? `测试完成，${failed} 个节点未通过，详情见列表` : '测试完成，结果已更新', failed > 0); }
      } else setTimeout(poll, 900);
    } catch (error) { watchedJobs.delete(id); renderJobs(); toast(error.message, true); }
  };
  poll();
}
async function testNodes(kind, ids) {
  if (!ids.length) return;
  try { await watchJob(await api('/api/tests', {method: 'POST', body: {kind, ids}})); }
  catch (error) { toast(error.message, true); }
}
async function refreshLogs() {
  try { const data = await api('/api/logs'); $('#logs-content').textContent = data.logs || '暂无运行日志。代理启动并产生连接后，记录会显示在这里。'; }
  catch (error) { toast(error.message, true); }
}

document.querySelectorAll('[data-page]').forEach(el => el.addEventListener('click', () => navigate(el.dataset.page)));
document.querySelectorAll('.close-dialog').forEach(el => el.addEventListener('click', () => el.closest('dialog').close()));
$('#import-button').addEventListener('click', () => { $('#import-form').reset(); openDialog('#import-dialog'); });
$('#add-subscription').addEventListener('click', () => { $('#subscription-form').reset(); openDialog('#subscription-dialog'); });
$('#search').addEventListener('input', renderNodes); $('#type-filter').addEventListener('change', renderNodes);
$('#refresh-button').addEventListener('click', () => refreshState()); $('#refresh-logs').addEventListener('click', refreshLogs);
$('#test-all-delay').addEventListener('click', () => testNodes('delay', state.nodes.map(n => n.id)));
$('#test-all-speed').addEventListener('click', () => testNodes('speed', state.nodes.map(n => n.id)));
$('#import-form').addEventListener('submit', event => {
  event.preventDefault(); formAction($('#import-submit'), '#import-error', async () => {
    const result = await api('/api/nodes/import', {method: 'POST', body: {text: $('#import-text').value}});
    $('#import-dialog').close(); $('#import-text').value = ''; await refreshState(); toast(`已导入 ${result.added} 个节点${result.duplicates ? '，跳过 ' + result.duplicates + ' 个重复节点' : ''}`);
  });
});
$('#edit-form').addEventListener('submit', event => {
  event.preventDefault(); formAction($('#edit-submit'), '#edit-error', async () => {
    await api('/api/nodes/' + $('#edit-id').value, {method: 'PATCH', body: {name: $('#edit-name').value, server: $('#edit-server').value, port: Number($('#edit-port').value), servername: $('#edit-sni').value}});
    $('#edit-dialog').close(); await refreshState(); toast('节点已更新');
  });
});
$('#subscription-form').addEventListener('submit', event => {
  event.preventDefault(); formAction($('#sub-submit'), '#sub-error', async () => {
    await api('/api/subscriptions', {method: 'POST', body: {name: $('#sub-name').value, url: $('#sub-url').value, interval: Number($('#sub-interval').value), via_proxy: $('#sub-via-proxy').checked}});
    $('#subscription-dialog').close(); $('#sub-url').value = ''; await refreshState(); toast('订阅已保存，点击“更新节点”获取内容');
  });
});
$('#node-rows').addEventListener('click', async event => {
  const button = event.target.closest('button[data-action]'), row = button?.closest('[data-node-id]');
  if (!row || !state) return;
  const n = state.nodes.find(n => n.id === row.dataset.nodeId); if (!n) return;
  const action = button.dataset.action;
  if (action === 'edit') {
    $('#edit-id').value = n.id; $('#edit-name').value = n.name; $('#edit-server').value = n.server; $('#edit-port').value = n.port; $('#edit-sni').value = n.servername; openDialog('#edit-dialog');
  } else if (action === 'delete') confirm('删除节点', `确定删除“${n.name}”吗？如果来自订阅，下次更新时可能重新出现。`, async () => { await api('/api/nodes/' + n.id, {method: 'DELETE'}); await refreshState(); toast('节点已删除'); });
  else if (action === 'select') {
    try { await busy(button, async () => { await api('/api/nodes/' + n.id + '/select', {method: 'POST', body: {}}); await refreshState(); toast('已切换，新连接将使用“' + n.name + '”'); }); } catch (error) { toast(error.message, true); }
  } else if (action === 'delay' || action === 'speed') testNodes(action, [n.id]);
});
$('#subscription-list').addEventListener('click', async event => {
  const button = event.target.closest('button[data-action]'), card = button?.closest('[data-sub-id]');
  if (!card) return;
  const sub = state.subscriptions.find(s => s.id === card.dataset.subId); if (!sub) return;
  if (button.dataset.action === 'delete-sub') confirm('删除订阅', `删除“${sub.name}”及其订阅节点？手动导入的节点会保留。`, async () => { await api('/api/subscriptions/' + sub.id, {method: 'DELETE'}); await refreshState(); toast('订阅已删除'); });
  else try { await busy(button, async () => watchJob(await api('/api/subscriptions/' + sub.id + '/refresh', {method: 'POST', body: {}}))); } catch (error) { toast(error.message, true); }
});
$('#confirm-submit').addEventListener('click', async () => {
  try { await busy($('#confirm-submit'), async () => { if (confirmAction) await confirmAction(); $('#confirm-dialog').close(); }); } catch (error) { toast(error.message, true); }
});
async function coreAction(action, button) {
  try { await busy(button, async () => { await api('/api/core/' + action, {method: 'POST', body: {}}); await refreshState(); toast(action === 'stop' ? '代理已停止' : '代理已启动'); }); } catch (error) { toast(error.message, true); }
}
$('#core-toggle').addEventListener('click', () => coreAction(state.core.running ? 'stop' : 'start', $('#core-toggle')));
$('#core-restart').addEventListener('click', () => coreAction('restart', $('#core-restart')));
$('#copy-command').addEventListener('click', async () => {
  const text = $('#proxy-command').textContent;
  try {
    if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(text);
    else { const el = document.createElement('textarea'); el.value = text; document.body.appendChild(el); el.select(); if (!document.execCommand('copy')) throw new Error(); el.remove(); }
    toast('终端命令已复制');
  } catch { toast('浏览器无法自动复制，请手动选中命令复制', true); }
});
setInterval(() => {
  if (state && !document.hidden) {
    refreshState(true);
    if (page === 'logs') refreshLogs();
  }
}, 3000);
showApp().catch(error => {
  const message = '无法读取管理面板，请检查 SSH 转发后刷新页面。';
  if ($('#app').hidden) {
    $('#connection-error').textContent = message;
    $('#connection-error').hidden = false;
    $('#loading').querySelector('p').hidden = true;
  } else {
    toast(error.message, true);
  }
});
