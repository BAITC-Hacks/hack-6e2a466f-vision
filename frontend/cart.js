const content = document.querySelector('#cart-content');
const sessionId = localStorage.getItem('ekt-demo-session');

function element(tag, value, className = '') {
  const node = document.createElement(tag);
  node.textContent = value;
  node.className = className;
  return node;
}

function showError(container, message) {
  container.replaceChildren(element('p', message, 'cart-error'));
}

async function changeQuantity(productId, quantity, message) {
  try {
    const response = await fetch(`/api/cart/${encodeURIComponent(sessionId)}/items/${encodeURIComponent(productId)}`, {
      method: 'PATCH', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({quantity}),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || 'Не удалось изменить количество.');
    await loadCart();
  } catch (error) { showError(message, error.message || 'Ошибка изменения.'); }
}

async function removeItem(productId, message) {
  try {
    const response = await fetch(`/api/cart/${encodeURIComponent(sessionId)}/items/${encodeURIComponent(productId)}`, {method: 'DELETE'});
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || 'Не удалось удалить товар.');
    await loadCart();
  } catch (error) { showError(message, error.message || 'Ошибка удаления.'); }
}

async function showBudgetOptions(container) {
  try {
    const response = await fetch(`/api/cart/${encodeURIComponent(sessionId)}/budget-options`);
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || 'Не удалось проверить варианты.');
    if (!result.options.length) container.append(element('p', 'Более дешёвых вариантов с подтверждённой ценой, остатком и обязательными параметрами среди найденных позиций нет.'));
    for (const option of result.options) {
      container.append(element('p', `${option.name || option.product_id} (ID ${option.product_id}): ${option.unit_price} ${option.currency} за штуку; экономия для ${option.quantity} шт. — ${option.saving} ${option.currency}. ${option.note}`));
    }
    if (!result.coverage_complete) container.append(element('p', 'Поиск охватил не весь каталог.', 'cart-warning'));
  } catch (error) { container.append(element('p', error.message || 'Варианты временно недоступны.', 'cart-warning')); }
}

function renderCart(cart) {
  content.replaceChildren();
  if (!cart.items?.length) {
    const empty = element('div', '', 'cart-empty');
    empty.append(element('div', '∅', 'empty-symbol'), element('h2', 'Здесь пока пусто'),
      element('p', 'Найдите товар и подтвердите добавление в demo cart.'));
    content.append(empty);
    return;
  }
  const list = element('div', '', 'cart-items');
  cart.items.forEach(({product, quantity}, index) => {
    const pricing = cart.pricing.lines[index];
    const card = element('article', '', 'cart-item');
    const info = element('div', '', 'cart-item-info');
    info.append(element('small', product.sku || `ID ${product.id}`), element('h2', product.name || 'Товар'));
    info.append(element('p', pricing.unit_price == null ? 'Цена не подтверждена' : `Цена за штуку: ${pricing.unit_price}${pricing.currency ? ` ${pricing.currency}` : ' · валюта неизвестна'}`));
    info.append(element('p', pricing.line_total == null ? pricing.reason : `${pricing.unit_price} × ${quantity} = ${pricing.line_total} ${pricing.currency}`));
    const controls = element('div', '', 'cart-item-actions');
    const count = document.createElement('input');
    count.type = 'number'; count.min = '1'; count.step = '1'; count.value = String(quantity);
    count.setAttribute('aria-label', `Количество для ${product.name || product.id}`);
    const save = element('button', 'Изменить количество');
    save.type = 'button';
    const remove = element('button', 'Удалить');
    remove.type = 'button';
    const message = element('div', '', 'cart-action-message');
    save.addEventListener('click', async () => {
      const next = Number(count.value);
      if (!Number.isInteger(next) || next < 1) return showError(message, 'Укажите положительное целое количество.');
      save.disabled = true;
      await changeQuantity(String(product.id), next, message);
      save.disabled = false;
    });
    remove.addEventListener('click', async () => {
      remove.disabled = true;
      await removeItem(String(product.id), message);
      remove.disabled = false;
    });
    controls.append(count, save, remove, message);
    card.append(element('div', 'э', 'cart-item-mark'), info, controls);
    list.append(card);
  });
  content.append(list);
  const summary = element('div', '', 'cart-summary');
  summary.append(element('strong', `Всего товаров: ${cart.total_items} шт.`));
  if (cart.pricing.complete) summary.append(element('p', `Расчёт по сохранённым ценам каталога: ${cart.pricing.total} ${cart.pricing.currency}. Это не окончательная стоимость заказа.`));
  else summary.append(element('p', 'Общий итог неизвестен или неполон.', 'cart-warning'));
  for (const [currency, amount] of Object.entries(cart.pricing.known_subtotals)) {
    if (!cart.pricing.complete) summary.append(element('p', `Известная часть: ${amount} ${currency}.`));
  }
  for (const reason of cart.pricing.reasons) summary.append(element('p', reason, 'cart-warning'));
  const budget = cart.pricing.budget;
  if (budget.status !== 'not_set') {
    if (budget.status === 'within') summary.append(element('p', `Лимит ${budget.amount} ${budget.currency}: укладывается; запас ${budget.difference} ${budget.currency}.`));
    else if (budget.status === 'over') summary.append(element('p', `Лимит ${budget.amount} ${budget.currency} превышен на ${budget.difference.slice(1)} ${budget.currency}.`, 'cart-warning'));
    else summary.append(element('p', 'Сравнить с бюджетом пока нельзя: проверьте цену, валюту и полноту списка.', 'cart-warning'));
    const options = element('div', '', 'cart-options');
    options.append(element('h2', 'Более дешёвые варианты'));
    summary.append(options);
    showBudgetOptions(options);
  }
  content.append(summary);
}

async function loadCart() {
  if (!sessionId) return showError(content, 'Откройте чат, чтобы начать список закупки.');
  try {
    const response = await fetch(`/api/cart/${encodeURIComponent(sessionId)}`);
    const cart = await response.json();
    if (!response.ok) throw new Error(cart.detail || 'Список временно недоступен.');
    renderCart(cart);
  } catch (error) { showError(content, error.message || 'Не удалось загрузить список.'); }
}

loadCart();
