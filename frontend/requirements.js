/* Editable requirements and bounded catalog candidate search. */
(() => {
  const form = document.querySelector('#requirements-form');
  if (!form) return;
  const input = document.querySelector('#requirements-input');
  const feedback = document.querySelector('#requirements-feedback');
  const panel = document.querySelector('#requirements-draft');
  const fields = document.querySelector('#requirements-fields');
  const question = document.querySelector('#requirements-question');
  const results = document.querySelector('#requirements-results');
  const sessionId = localStorage.getItem('ekt-demo-session');
  let draft = null;
  let attributes = [];

  function control(labelText, value, onChange, options = {}) {
    const label = document.createElement('label');
    label.className = 'requirement-field';
    const caption = document.createElement('span');
    caption.textContent = labelText;
    label.append(caption);
    let field;
    if (options.choices) {
      field = document.createElement('select');
      for (const choice of options.choices) {
        const option = document.createElement('option');
        option.value = choice;
        option.textContent = choice;
        field.append(option);
      }
      field.value = value ?? options.choices[0];
    } else {
      field = document.createElement('input');
      field.type = options.type || 'text';
      if (options.type === 'number') { field.min = '0'; field.step = options.step || '1'; }
      field.value = value ?? '';
      field.maxLength = 160;
      field.placeholder = options.placeholder || 'Не указано';
    }
    field.addEventListener('input', () => { onChange(field.value); results.replaceChildren(); });
    field.addEventListener('change', () => { onChange(field.value); results.replaceChildren(); });
    label.append(field);
    return label;
  }

  function render() {
    if (!draft) { panel.hidden = true; return; }
    panel.hidden = false;
    fields.replaceChildren();
    const basics = document.createElement('div');
    basics.className = 'requirements-grid';
    basics.append(
      control('Тип запроса', draft.intent, value => { draft.intent = value; }, {choices: ['find', 'compare', 'alternative', 'explain', 'budget', 'list', 'unclear']}),
      control('Товарные слова', draft.product_terms.join(', '), value => { draft.product_terms = value.split(',').map(part => part.trim()).filter(Boolean); }, {placeholder: 'Например: автомат'}),
      control('Артикул', draft.article, value => { draft.article = value.trim() || null; }),
      control('Количество, шт.', draft.quantity, value => { draft.quantity = value ? Number(value) : null; }, {type: 'number'}),
      control('Бюджет', draft.max_budget, value => { draft.max_budget = value ? Number(value) : null; }, {type: 'number', step: 'any'}),
      control('Валюта (если указана)', draft.budget_currency, value => { draft.budget_currency = value.trim() || null; }),
    );
    fields.append(basics);
    const heading = document.createElement('h3');
    heading.textContent = 'Характеристики';
    fields.append(heading);
    draft.requirements.forEach((item, index) => {
      const row = document.createElement('div');
      row.className = 'requirement-row';
      row.append(
        control('Параметр', item.attribute, value => { item.attribute = value; }, {choices: attributes}),
        control('Значение', item.value, value => { item.value = value; }, {placeholder: 'Например: 16 А'}),
      );
      const requiredLabel = document.createElement('label');
      requiredLabel.className = 'requirement-check';
      const required = document.createElement('input');
      required.type = 'checkbox';
      required.checked = item.required;
      required.addEventListener('change', () => { item.required = required.checked; results.replaceChildren(); });
      requiredLabel.append(required, document.createTextNode('Обязательно'));
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.textContent = 'Убрать';
      remove.addEventListener('click', () => { draft.requirements.splice(index, 1); results.replaceChildren(); render(); });
      row.append(requiredLabel, remove);
      fields.append(row);
    });
    question.textContent = draft.clarification_question ? `Уточнение: ${draft.clarification_question}` : 'Проверьте условия и при необходимости исправьте их.';
  }

  form.addEventListener('submit', async event => {
    event.preventDefault();
    const message = input.value.trim();
    if (!message) return;
    const button = form.querySelector('button');
    button.disabled = true;
    feedback.className = 'requirements-feedback';
    feedback.textContent = 'Разбираю запрос…';
    try {
      const response = await fetch('/api/requirements/extract', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({message, session_id: sessionId}),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || 'Не удалось разобрать запрос.');
      draft = result.requirements;
      results.replaceChildren();
      attributes = result.available_attributes;
      feedback.textContent = result.ai_mode === 'openai' ? 'Условия распознаны через OpenAI. Проверьте их перед подбором.' : 'Offline demo — OpenAI API is not configured. Условия выделены локальными правилами.';
      render();
    } catch (error) {
      feedback.className = 'requirements-feedback error';
      feedback.textContent = error.message || 'Ошибка разбора. Попробуйте ещё раз.';
    } finally { button.disabled = false; }
  });

  document.querySelector('#requirements-add').addEventListener('click', () => {
    if (!draft || !attributes.length) return;
    draft.requirements.push({attribute: attributes[0], value: '', required: true});
    results.replaceChildren();
    render();
  });

  async function saveDraft() {
    if (!draft) return false;
    try {
      const response = await fetch(`/api/requirements/${encodeURIComponent(sessionId)}`, {
        method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({requirements: draft}),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Проверьте заполненные условия.');
      draft = result.requirements;
      feedback.className = 'requirements-feedback';
      feedback.textContent = 'Условия сохранены. Можно искать товары.';
      render();
      return true;
    } catch (error) {
      feedback.className = 'requirements-feedback error';
      feedback.textContent = error.message || 'Не удалось сохранить условия.';
      return false;
    }
  }

  document.querySelector('#requirements-save').addEventListener('click', saveDraft);

  function line(text, className = '') {
    const node = document.createElement('p');
    node.textContent = text;
    node.className = className;
    return node;
  }

  function renderResults(data) {
    results.replaceChildren();
    const title = document.createElement('h3');
    title.textContent = data.status === 'incomplete' ? 'Поиск неполон' : data.status === 'not_found' ? 'Товары не найдены' : 'Кандидаты из каталога';
    results.append(title, line(data.message), line(`Просмотрено страниц: ${data.pages_scanned}; проверено карточек: ${data.detail_checked}.`));
    if (data.status === 'found' && !data.coverage_complete) results.append(line('Охват неполный: могут быть и другие позиции.', 'requirements-warning'));
    if (data.status === 'not_found') results.append(line('Подходящих позиций в просмотренном каталоге нет.'));
    for (const entry of data.products) {
      const item = entry.product;
      const card = document.createElement('article');
      card.className = 'requirements-product';
      const name = document.createElement('strong');
      name.textContent = item.name || item.sku || `Товар ${item.id}`;
      card.append(name, line(`ID: ${item.id}${item.sku ? ` · Артикул: ${item.sku}` : ''}`));
      if (item.price != null) card.append(line(`Цена в каталоге: ${item.price}`));
      if (item.stock != null) card.append(line(`Остаток в карточке: ${item.stock}`));
      card.append(line(entry.matched.length ? `Совпало: ${entry.matched.join('; ')}` : 'Совпадение характеристик пока не подтверждено.'));
      if (entry.unknown.length) card.append(line(`Не удалось проверить: ${entry.unknown.join(', ')}.`));
      if (draft?.max_budget != null) card.append(line('Бюджет требует отдельной проверки валюты и цены.'));
      results.append(card);
    }
  }

  async function runSearch(refresh) {
    if (!draft || !await saveDraft()) return;
    const buttons = [document.querySelector('#requirements-search'), document.querySelector('#requirements-refresh')];
    buttons.forEach(button => { button.disabled = true; });
    results.replaceChildren(line('Ищу позиции в каталоге…'));
    try {
      const response = await fetch('/api/requirements/search', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({session_id: sessionId, refresh}),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Не удалось выполнить поиск.');
      renderResults(data);
    } catch (error) {
      results.replaceChildren(line(error.message || 'Поиск не выполнен.', 'requirements-warning'));
    } finally { buttons.forEach(button => { button.disabled = false; }); }
  }

  document.querySelector('#requirements-search').addEventListener('click', () => runSearch(false));
  document.querySelector('#requirements-refresh').addEventListener('click', () => runSearch(true));

  document.addEventListener('ekt:session-reset', () => {
    draft = null;
    input.value = '';
    feedback.textContent = '';
    results.replaceChildren();
    render();
  });

  fetch(`/api/requirements/${encodeURIComponent(sessionId)}`).then(response => response.json()).then(result => {
    if (!result.requirements || draft) return;
    draft = result.requirements;
    attributes = result.available_attributes;
    feedback.textContent = 'Сохранённые условия этого диалога.';
    render();
  }).catch(() => {});
})();
