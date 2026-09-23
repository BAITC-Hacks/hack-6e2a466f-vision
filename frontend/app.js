const $ = (selector) => document.querySelector(selector);
const productsNode = $('#products');
const messagesNode = $('#messages');
const sessionId = localStorage.getItem('ekt-demo-session') || crypto.randomUUID();
localStorage.setItem('ekt-demo-session', sessionId);
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));

function renderProducts(items, mode) {
  if (!items.length) {
    productsNode.innerHTML = '<p class="product">На этой странице товаров нет.</p>';
    return;
  }
  productsNode.innerHTML = items.map((p) => `<article class="product"><div class="product-top"><span class="sku">${esc(p.sku || `ID ${p.id ?? '—'}`)}</span><span class="badge">${mode === 'demo' ? 'Демо-данные' : 'Каталог'}</span></div><h3>${esc(p.name || 'Название не указано')}</h3><p>${esc(p.description || 'Описание в API не указано.')}</p><div class="product-price">${p.price == null ? 'Цена: нет данных' : `Цена: ${esc(p.price)}`}</div>${mode === 'live' && p.id != null ? `<button class="add-button" data-add="${esc(p.sku || p.name || p.id)}">Предложить добавить</button>` : ''}</article>`).join('');
}

function addMessage(text, role = 'assistant') {
  const bubble = document.createElement('div');
  bubble.className = `message ${role}`;
  bubble.textContent = text;
  messagesNode.append(bubble);
  messagesNode.scrollTop = messagesNode.scrollHeight;
  return bubble;
}

async function loadCatalog() {
  const status = $('#connection');
  try {
    const [stateResponse, productsResponse] = await Promise.all([fetch('/api/status'), fetch('/api/products')]);
    const state = await stateResponse.json();
    const catalog = await productsResponse.json();
    if (!productsResponse.ok) throw new Error(catalog.detail || state.message || 'Каталог временно недоступен.');
    status.className = `connection ${state.mode}`;
    status.querySelector('span:last-child').textContent = `${state.mode === 'live' ? 'Live-каталог' : 'Демо-каталог'} · ${state.ai_mode === 'openai' ? 'OpenAI API' : 'локальный ответ'}`;
    $('#notice').textContent = `${state.message} ${state.ai_mode === 'openai' ? `Ответы через OpenAI (${state.model}).` : 'Offline demo — OpenAI API is not configured.'}`;
    $('#notice').hidden = false;
    renderProducts(catalog.products || [], catalog.mode);
  } catch (error) {
    status.className = 'connection error';
    status.querySelector('span:last-child').textContent = 'Ошибка каталога';
    $('#notice').textContent = error.message || 'Не удалось связаться с backend. Обновите страницу позже.';
    $('#notice').hidden = false;
    productsNode.innerHTML = '<p class="product">Каталог временно недоступен.</p>';
  }
}

$('#chat-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const input = $('#question');
  const question = input.value.trim();
  if (!question) return;
  addMessage(question, 'user');
  input.value = '';
  const submit = event.submitter || $('#chat-form button');
  submit.disabled = true;
  submit.textContent = '…';
  const loading = addMessage('Ищу товары и готовлю ответ…', 'loading');
  try {
    const response = await fetch('/api/chat', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({message: question, session_id: sessionId})});
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || 'Не удалось получить ответ. Попробуйте ещё раз.');
    loading.remove();
    const reply = addMessage(result.answer);
    if (result.cart_url) {
      const link = document.createElement('a');
      link.href = result.cart_url;
      link.textContent = 'Открыть демо-корзину →';
      reply.append(document.createElement('br'), link);
    }
    if (result.products?.length) renderProducts(result.products, result.catalog_mode);
  } catch (error) {
    loading.remove();
    addMessage(error.message || 'Не удалось связаться с помощником. Попробуйте ещё раз.', 'error');
  } finally {
    submit.disabled = false;
    submit.textContent = '↑';
  }
});

productsNode.addEventListener('click', (event) => {
  const button = event.target.closest('[data-add]');
  if (!button) return;
  $('#question').value = `Добавь в корзину ${button.dataset.add}`;
  $('#chat-form').requestSubmit();
});

$('#reset').addEventListener('click', async () => {
  try {
    const response = await fetch(`/api/session/${encodeURIComponent(sessionId)}`, {method: 'DELETE'});
    if (!response.ok) throw new Error('Не удалось начать новый диалог. Попробуйте ещё раз.');
    messagesNode.innerHTML = '';
    addMessage('Здравствуйте! Подскажу по товарам и покажу сведения, доступные в каталоге. С чего начнём?');
  } catch (error) {
    addMessage(error.message || 'Ошибка сброса диалога.', 'error');
  }
});

async function restoreConversation() {
  try {
    const response = await fetch(`/api/session/${encodeURIComponent(sessionId)}`);
    if (!response.ok) return;
    const session = await response.json();
    if (!session.messages?.length) return;
    messagesNode.innerHTML = '';
    session.messages.forEach(({role, content}) => addMessage(content, role));
  } catch { /* A fresh chat is still usable if history cannot be loaded. */ }
}

loadCatalog();
restoreConversation();
