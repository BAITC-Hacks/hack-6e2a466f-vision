const $ = (selector) => document.querySelector(selector);
const productsNode = $('#products');
const messagesNode = $('#messages');
const sessionId = localStorage.getItem('ekt-demo-session') || crypto.randomUUID();
localStorage.setItem('ekt-demo-session', sessionId);
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));

function officialProductUrl(raw) {
  if (!raw) return null;
  try {
    const url = new URL(raw, 'https://ekt.kz');
    return url.protocol === 'https:' && url.hostname === 'ekt.kz' ? url.href : null;
  } catch { return null; }
}

function productFacts(product) {
  const stock = product.stock == null ? 'Остаток: нет данных' : `Остаток: ${esc(product.stock)} шт.`;
  const category = product.category ? `Категория: ${esc(product.category)}` : 'Категория: нет данных';
  const characteristics = product.characteristics && typeof product.characteristics === 'object' && !Array.isArray(product.characteristics)
    ? Object.entries(product.characteristics).filter(([key, value]) => value != null && !/article|artikul|novinka|priority|cml2_|blog_post/i.test(key)).slice(0, 3).map(([key, value]) => `${esc(key)}: ${esc(value)}`).join(' · ')
    : '';
  const certificate = product.certificates ? 'Сертификаты: сведения есть в API' : 'Сертификаты: нет данных';
  return `<p class="product-facts">${category}<br>${stock}<br>${characteristics ? `Характеристики: ${characteristics}<br>` : 'Характеристики: нет данных<br>'}${certificate}</p>`;
}

function renderProducts(items, mode) {
  if (!items.length) {
    productsNode.innerHTML = '<p class="product">На этой странице товаров нет.</p>';
    return;
  }
  productsNode.innerHTML = items.map((p) => {
    const url = mode === 'live' ? officialProductUrl(p.url) : null;
    const canAdd = p.id != null && Number.isInteger(Number(p.stock)) && Number(p.stock) > 0;
    return `<article class="product"><div class="product-top"><span class="sku">${esc(p.sku || `ID ${p.id ?? '—'}`)}</span><span class="badge">${mode === 'demo' ? 'Демо-данные' : 'Каталог'}</span></div><h3>${esc(p.name || 'Название не указано')}</h3><p>${esc(p.description || 'Описание в API не указано.')}</p>${productFacts(p)}<div class="product-price">${p.price == null ? 'Цена: нет данных' : `Цена: ${esc(p.price)}`}</div>${url ? `<a class="product-url" href="${esc(url)}" target="_blank" rel="noopener noreferrer">Страница товара ekt.kz ↗</a>` : ''}${canAdd ? `<button class="add-button" data-add="${esc(p.sku || p.name || p.id)}">Предложить в демо-корзину</button>` : ''}</article>`;
  }).join('');
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
    if (!stateResponse.ok || !productsResponse.ok) throw new Error(catalog.detail || state.message || 'Каталог временно недоступен.');
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
      if (document.body.dataset.embedded === 'true') {
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
      }
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
    document.dispatchEvent(new Event('ekt:session-reset'));
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
