const $ = (selector) => document.querySelector(selector);
const productsNode = $('#products');
const messagesNode = $('#messages');
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));

function renderProducts(items, mode) {
  if (!items.length) {
    productsNode.innerHTML = '<p class="product">На этой странице товаров нет.</p>';
    return;
  }
  productsNode.innerHTML = items.map((p) => `<article class="product"><div class="product-top"><span class="sku">${esc(p.sku || `ID ${p.id ?? '—'}`)}</span><span class="badge">${mode === 'demo' ? 'Демо-данные' : 'Каталог'}</span></div><h3>${esc(p.name || 'Название не указано')}</h3><p>${esc(p.description || 'Описание в API не указано.')}</p><div class="product-price">${p.price == null ? 'Цена: нет данных' : `Цена: ${esc(p.price)}`}</div></article>`).join('');
}

function addMessage(text, role = 'assistant') {
  const bubble = document.createElement('div');
  bubble.className = `message ${role}`;
  bubble.textContent = text;
  messagesNode.append(bubble);
  messagesNode.scrollTop = messagesNode.scrollHeight;
}

async function loadCatalog() {
  const status = $('#connection');
  try {
    const [stateResponse, productsResponse] = await Promise.all([fetch('/api/status'), fetch('/api/products')]);
    const state = await stateResponse.json();
    const catalog = await productsResponse.json();
    status.className = `connection ${state.mode}`;
    status.querySelector('span:last-child').textContent = `${state.mode === 'live' ? 'Live-каталог' : 'Демо-каталог'} · ${state.ai_mode === 'openai' ? 'OpenAI API' : 'локальный ответ'}`;
    $('#notice').textContent = `${state.message} ${state.ai_mode === 'openai' ? `Ответы через OpenAI (${state.model || 'настроенная модель'}).` : 'Для ответов OpenAI задайте OPENAI_API_KEY.'}`;
    $('#notice').hidden = false;
    renderProducts(catalog.products || [], catalog.mode);
  } catch {
    status.className = 'connection demo';
    status.querySelector('span:last-child').textContent = 'Нет соединения';
    $('#notice').textContent = 'Не удалось связаться с backend. Обновите страницу позже.';
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
  try {
    const response = await fetch('/api/chat', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({message: question})});
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || 'Не удалось получить ответ. Попробуйте ещё раз.');
    addMessage(result.answer);
    if (result.products?.length) renderProducts(result.products, result.catalog_mode);
  } catch (error) {
    addMessage(error.message || 'Не удалось связаться с помощником. Попробуйте ещё раз.');
  } finally {
    submit.disabled = false;
    submit.textContent = '↑';
  }
});

$('#reset').addEventListener('click', () => {
  messagesNode.innerHTML = '';
  addMessage('Здравствуйте! Подскажу по товарам и покажу сведения, доступные в каталоге. С чего начнём?');
});

loadCatalog();
