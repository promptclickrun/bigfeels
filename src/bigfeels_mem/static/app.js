'use strict';

let credential = '';
let activeCorrection = null;
let connectionGeneration = 0;
let refreshSequence = 0;

const byId = (id) => document.getElementById(id);
const welcome = byId('welcome');
const workspace = byId('workspace');
const disconnectButton = byId('disconnect');
const notice = byId('notice');

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = String(text);
  return node;
}

function announce(message, success = false) {
  notice.textContent = message || '';
  notice.className = success ? 'notice success' : 'notice';
}

async function api(operation, payload) {
  const response = await fetch(`/v1/${operation}`, {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${credential}`,
      'Content-Type': 'application/json',
      'Accept': 'application/json'
    },
    body: JSON.stringify(payload)
  });
  let result;
  try {
    result = await response.json();
  } catch (_) {
    throw new Error('The service returned an unreadable response.');
  }
  if (!response.ok) throw new Error(result?.error?.message || 'The request failed.');
  return result;
}

function statusCard(label, value, variant = '') {
  const card = element('article', `status-card ${variant}`.trim());
  card.append(element('span', '', label), element('strong', '', value));
  return card;
}

function renderStatus(status) {
  const cards = byId('status-cards');
  const queue = status.queue || {};
  const processing = status.processing || {};
  const provider = status.providers?.extraction || 'not_configured';
  const queueSummary = [
    `${queue.pending ?? 0} pending`,
    queue.failed ? `${queue.failed} failed` : '',
    queue.processing ? `${queue.processing} active` : ''
  ].filter(Boolean).join(' · ');
  const nextWork = processing.next_retry_at
    ? `retry ${formatTime(Number(processing.next_retry_at) * 1000)}`
    : processing.oldest_pending_at
      ? `oldest ${formatTime(processing.oldest_pending_at)}`
      : 'idle';
  cards.replaceChildren(
    statusCard('Memories', status.memories ?? 0),
    statusCard('Evidence', status.evidence ?? 0),
    statusCard('Queue', queueSummary),
    statusCard('Processing', `${provider} · ${nextWork}`),
    statusCard('Spaces', (status.spaces || []).join(', ') || 'None', 'spaces')
  );
}

function tag(text, status) {
  return element('span', `tag ${status || ''}`, text);
}

function formatTime(value) {
  if (!value) return 'No timestamp';
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString();
}

function action(label, className, handler) {
  const button = element('button', className, label);
  button.type = 'button';
  button.addEventListener('click', handler);
  return button;
}

function renderMemory(memory) {
  const card = element('article', 'memory');
  const head = element('div', 'memory-head');
  const tags = element('div', 'tag-row');
  tags.append(tag(memory.kind || 'memory'), tag(memory.status || 'unknown', memory.status));
  if (memory.basis) tags.append(tag(memory.basis));
  if (memory.outcome && memory.outcome !== 'unspecified') tags.append(tag(memory.outcome));
  head.append(tags, element('span', 'metadata', `rev ${memory.revision ?? '—'}`));

  const content = element('p', 'memory-content', memory.content);
  const reason = Array.isArray(memory.reason) ? memory.reason.join(', ') : 'browse';
  const metadata = element(
    'div',
    'metadata',
    `${memory.space} · ${formatTime(memory.valid_from)} · ${memory.source_available ? 'source available' : 'source unavailable'} · ${reason}`
  );
  const actions = element('div', 'memory-actions');
  actions.append(
    action('Inspect', 'quiet', () => inspectRecord(memory.id)),
    action('Correct', 'quiet', () => openCorrection(memory)),
    action('Forget', 'quiet danger', () => forgetRecord(memory))
  );
  card.append(head, content, metadata, actions);
  return card;
}

function renderTrace(result) {
  const section = byId('trace');
  const values = byId('trace-values');
  const trace = result.trace;
  if (!trace) {
    section.hidden = true;
    values.replaceChildren();
    return;
  }
  const items = [
    ['Eligible', trace.eligible],
    ['Matched', trace.matched],
    ['Returned', trace.returned],
    ['Budget used', `${result.tokens ?? 0} / ${trace.budget ?? '—'}`],
    ['Embeddings', trace.embedding_status],
    ['As of', formatTime(trace.as_of)],
    ['Accounting', trace.token_accounting],
    ['Trust', trace.trust]
  ];
  const nodes = [];
  for (const [name, value] of items) {
    nodes.push(element('dt', '', name), element('dd', '', value ?? '—'));
  }
  values.replaceChildren(...nodes);
  section.hidden = false;
}

function renderMemories(result) {
  const memories = result.memories || [];
  byId('result-count').textContent = `${memories.length} ${memories.length === 1 ? 'record' : 'records'}`;
  const nodes = memories.length
    ? memories.map(renderMemory)
    : [element('div', 'empty', 'No memory records matched this view.')];
  byId('memories').replaceChildren(...nodes);
  renderTrace(result);
}

async function refresh(generation = connectionGeneration) {
  if (generation !== connectionGeneration) return;
  const sequence = ++refreshSequence;
  announce('');
  try {
    const [status, result] = await Promise.all([
      api('status', {}),
      api('search', {query: byId('query').value, budget: 32000, include_inactive: true})
    ]);
    if (generation !== connectionGeneration || sequence !== refreshSequence) return;
    renderStatus(status);
    renderMemories(result);
  } catch (error) {
    if (generation !== connectionGeneration || sequence !== refreshSequence) return;
    announce(error.message);
  }
}

async function inspectRecord(id) {
  const generation = connectionGeneration;
  announce('');
  try {
    const detail = await api('inspect', {id});
    if (generation !== connectionGeneration) return;
    byId('inspect-content').textContent = JSON.stringify(detail, null, 2);
    byId('inspect-dialog').showModal();
  } catch (error) {
    if (generation !== connectionGeneration) return;
    announce(error.message);
  }
}

function openCorrection(memory) {
  activeCorrection = {id: memory.id, revision: memory.revision};
  byId('correct-content').value = memory.content || '';
  byId('correct-time').value = '';
  byId('correct-dialog').showModal();
}

async function forgetRecord(memory) {
  const generation = connectionGeneration;
  announce('');
  try {
    const preview = await api('forget_preview', {id: memory.id});
    if (generation !== connectionGeneration) return;
    const memories = Array.isArray(preview.memories) ? preview.memories : [];
    const counts = preview.counts || {};
    const lines = memories.map(item => `• ${item.content || item.id}`).join('\n');
    const prompt = [
      `Forget ${counts.memories ?? memories.length} memory record(s) and ${counts.evidence ?? 0} evidence record(s)?`,
      '', lines, '', 'This cannot be undone from the UI.'
    ].join('\n');
    if (!preview.plan_token || !window.confirm(prompt)) return;
    await api('forget', {id: memory.id, plan_token: preview.plan_token});
    if (generation !== connectionGeneration) return;
    announce('The record was forgotten.', true);
    await refresh(generation);
  } catch (error) {
    if (generation !== connectionGeneration) return;
    announce(error.message);
  }
}

async function processPending() {
  const generation = connectionGeneration;
  announce('');
  try {
    const result = await api('process', {limit: 8});
    if (generation !== connectionGeneration) return;
    const status = result.status || {};
    const queue = status.queue || {};
    const processing = status.processing || {};
    const provider = status.providers?.extraction || 'not_configured';
    renderStatus(status);
    if (result.processed) {
      announce(`Processed ${result.processed} queued record(s).`, true);
    } else if (provider === 'not_configured' || provider === 'credential_missing') {
      announce(`Processed 0. Extraction is ${provider}; ${queue.pending ?? 0} record(s) remain pending.`);
    } else if (queue.processing) {
      announce('Processed 0. Another processing call is active; check status before retrying.');
    } else if (processing.next_retry_at) {
      announce('Processed 0. Pending work is waiting for its retry time.');
    } else if (queue.pending) {
      announce('Processed 0. No pending record was ready; check processing status.');
    } else {
      announce('Processed 0. The queue is empty.', true);
    }
    await refresh(generation);
  } catch (error) {
    if (generation !== connectionGeneration) return;
    announce(error.message);
  }
}

byId('connect-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const generation = ++connectionGeneration;
  const input = byId('credential');
  credential = input.value.trim();
  input.value = '';
  try {
    const status = await api('status', {});
    if (generation !== connectionGeneration) return;
    welcome.hidden = true;
    workspace.hidden = false;
    disconnectButton.hidden = false;
    renderStatus(status);
    await refresh(generation);
    if (generation !== connectionGeneration) return;
    byId('query').focus();
  } catch (error) {
    if (generation !== connectionGeneration) return;
    credential = '';
    notice.textContent = '';
    const help = byId('credential-help');
    help.textContent = error.message;
    input.focus();
  }
});

disconnectButton.addEventListener('click', () => {
  connectionGeneration += 1;
  credential = '';
  activeCorrection = null;
  workspace.hidden = true;
  disconnectButton.hidden = true;
  welcome.hidden = false;
  byId('memories').replaceChildren();
  byId('status-cards').replaceChildren();
  byId('trace-values').replaceChildren();
  byId('trace').hidden = true;
  byId('result-count').textContent = '';
  byId('inspect-content').textContent = '';
  byId('correct-content').value = '';
  byId('correct-time').value = '';
  if (byId('remember-content')) byId('remember-content').value = '';
  announce('');
  for (const dialog of [
    byId('inspect-dialog'), byId('correct-dialog'), byId('remember-dialog')
  ].filter(Boolean)) {
    if (dialog.open) dialog.close();
  }
  byId('credential').focus();
});

byId('search-form').addEventListener('submit', (event) => {
  event.preventDefault();
  refresh();
});
byId('refresh').addEventListener('click', () => refresh());
byId('process')?.addEventListener('click', processPending);
byId('remember')?.addEventListener('click', () => byId('remember-dialog')?.showModal());

for (const button of document.querySelectorAll('[data-close]')) {
  button.addEventListener('click', () => byId(button.dataset.close).close());
}

byId('correct-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (!activeCorrection) return;
  const generation = connectionGeneration;
  const payload = {
    id: activeCorrection.id,
    revision: activeCorrection.revision,
    content: byId('correct-content').value
  };
  const effective = byId('correct-time').value;
  if (effective) payload.valid_from = new Date(effective).toISOString();
  try {
    await api('correct', payload);
    if (generation !== connectionGeneration) return;
    byId('correct-dialog').close();
    activeCorrection = null;
    announce('A corrected revision was saved.', true);
    await refresh(generation);
  } catch (error) {
    if (generation !== connectionGeneration) return;
    announce(error.message);
    byId('correct-dialog').close();
  }
});

byId('remember-form')?.addEventListener('submit', async (event) => {
  event.preventDefault();
  const generation = connectionGeneration;
  const payload = {
    space: byId('remember-space').value,
    content: byId('remember-content').value,
    kind: byId('remember-kind').value,
    basis: byId('remember-basis').value
  };
  try {
    await api('remember', payload);
    if (generation !== connectionGeneration) return;
    byId('remember-dialog').close();
    byId('remember-content').value = '';
    announce('The memory was saved.', true);
    await refresh(generation);
  } catch (error) {
    if (generation !== connectionGeneration) return;
    announce(error.message);
  }
});
