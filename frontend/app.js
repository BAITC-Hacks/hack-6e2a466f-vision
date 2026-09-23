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
    status.querySelector('span:last-child').textContent = state.mode === 'live' ? 'Live-каталог подключён' : 'Демо-режим';
    $('#notice').textContent = state.message;
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
  const normalized = question.toLocaleLowerCase('ru');
  const cards = [...productsNode.querySelectorAll('.product')];
  const match = cards.find((card) => normalized.split(/\s+/).some((term) => term.length > 2 && card.textContent.toLocaleLowerCase('ru').includes(term)));
  if (/привет|здравствуй/.test(normalized)) {
    addMessage('Здравствуйте! Напишите название или артикул товара — покажу совпадения из каталога.');
  } else if (match) {
    addMessage(`${match.querySelector('h3')?.textContent}. ${match.querySelector('p')?.textContent} ${match.querySelector('.product-price')?.textContent}`);
  } else {
    addMessage('Пока это первая часть прототипа: поиск по каталогу отображает найденные позиции выше. Сейчас точного совпадения среди загруженных товаров нет.');
  }
});

$('#reset').addEventListener('click', () => {
  messagesNode.innerHTML = '';
  addMessage('Здравствуйте! Подскажу по товарам и покажу сведения, доступные в каталоге. С чего начнём?');
});

loadCatalog();
