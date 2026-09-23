const content = document.querySelector('#cart-content');
const sessionId = localStorage.getItem('ekt-demo-session');
const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));

async function loadCart() {
  if (!sessionId) {
    content.innerHTML = '<div class="cart-empty"><div class="empty-symbol">∅</div><h2>Здесь пока пусто</h2><p>Найдите товар в каталоге и подтвердите добавление в чате.</p></div>';
    return;
  }
  try {
    const response = await fetch(`/api/cart/${encodeURIComponent(sessionId)}`);
    const cart = await response.json();
    if (!response.ok) throw new Error('Корзина временно недоступна.');
    if (!cart.items?.length) {
      content.innerHTML = '<div class="cart-empty"><div class="empty-symbol">∅</div><h2>Здесь пока пусто</h2><p>Найдите товар в каталоге и подтвердите добавление в чате.</p></div>';
      return;
    }
    content.innerHTML = `<div class="cart-items">${cart.items.map(({product, quantity}) => `<article class="cart-item"><div class="cart-item-mark">э</div><div class="cart-item-info"><small>${escapeHtml(product.sku || `ID ${product.id}`)}</small><h2>${escapeHtml(product.name || 'Товар')}</h2><p>${product.price == null ? 'Цена не указана в каталоге' : `Цена: ${escapeHtml(product.price)}`}</p></div><strong class="cart-quantity">${escapeHtml(quantity)} шт.</strong></article>`).join('')}</div><div class="cart-total"><span>Всего товаров</span><strong>${escapeHtml(cart.total_items)} шт.</strong></div>`;
  } catch (error) {
    content.innerHTML = `<div class="cart-empty"><p>${escapeHtml(error.message)}</p></div>`;
  }
}

loadCart();
