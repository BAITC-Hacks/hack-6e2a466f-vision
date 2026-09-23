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
    const selected = new Set(data.products.slice(0, 4).map(entry => String(entry.product.id)));
    const title = document.createElement('h3');
    title.textContent = data.status === 'incomplete' ? 'Поиск неполон' : data.status === 'not_found' ? 'Товары не найдены' : 'Кандидаты из каталога';
    results.append(title, line(data.message), line(`Просмотрено страниц: ${data.pages_scanned}; проверено карточек: ${data.detail_checked}.`));
    if (data.status === 'found' && !data.coverage_complete) results.append(line('Охват неполный: могут быть и другие позиции.', 'requirements-warning'));
    if (data.status === 'not_found') results.append(line('Подходящих позиций в просмотренном каталоге нет.'));
    for (const entry of data.products) {
      const item = entry.product;
      const card = document.createElement('article');
      card.className = 'requirements-product';
      const selectLabel = document.createElement('label');
      selectLabel.className = 'requirements-select';
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.checked = selected.has(String(item.id));
      checkbox.addEventListener('change', () => {
        if (checkbox.checked && selected.size >= 4) {
          checkbox.checked = false;
          return;
        }
        if (checkbox.checked) selected.add(String(item.id));
        else selected.delete(String(item.id));
      });
      selectLabel.append(checkbox, document.createTextNode('Сравнить'));
      card.append(selectLabel);
      const name = document.createElement('strong');
      name.textContent = item.name || item.sku || `Товар ${item.id}`;
      card.append(name, line(`ID: ${item.id}${item.sku ? ` · Артикул: ${item.sku}` : ''}`));
      if (item.price != null) card.append(line(`Цена в каталоге: ${item.price}`));
      if (item.stock != null) card.append(line(`Остаток в карточке: ${item.stock}`));
      card.append(line(entry.matched.length ? `Совпало: ${entry.matched.join('; ')}` : 'Совпадение характеристик пока не подтверждено.'));
      if (entry.conflicted?.length) card.append(line(`Противоречит: ${entry.conflicted.join('; ')}.`, 'requirements-warning'));
      if (entry.unknown.length) card.append(line(`Не удалось проверить: ${entry.unknown.join(', ')}.`));
      if (draft?.max_budget != null) card.append(line('Расчёт бюджета для выбранных позиций появится в demo cart.'));
      const addArea = document.createElement('div');
      addArea.className = 'requirements-cart-action';
      const stock = Number(item.stock);
      if (item.stock == null || !Number.isInteger(stock) || stock <= 0) {
        addArea.append(line('Точный положительный остаток не подтверждён: добавить в demo cart нельзя.'));
      } else {
        const quantity = document.createElement('input');
        quantity.type = 'number';
        quantity.min = '1';
        quantity.step = '1';
        quantity.value = String(draft?.quantity || 1);
        quantity.setAttribute('aria-label', `Количество для ${item.name || item.id}`);
        const prepare = document.createElement('button');
        prepare.type = 'button';
        prepare.textContent = 'Добавить в demo cart';
        const pending = document.createElement('div');
        prepare.addEventListener('click', async () => {
          const count = Number(quantity.value);
          if (!Number.isInteger(count) || count < 1) {
            pending.replaceChildren(line('Укажите положительное целое количество.', 'requirements-warning'));
            return;
          }
          prepare.disabled = true;
          try {
            const response = await fetch('/api/cart/prepare', {
              method: 'POST', headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({session_id: sessionId, product_id: String(item.id), quantity: count}),
            });
            const payload = await response.json();
            if (!response.ok) throw new Error(payload.detail || 'Не удалось подготовить добавление.');
            pending.replaceChildren(line(`Подтвердите: ${payload.quantity} шт. «${payload.name}» в демонстрационную корзину.`));
            const confirm = document.createElement('button');
            confirm.type = 'button';
            confirm.textContent = 'Подтвердить добавление';
            confirm.addEventListener('click', async () => {
              confirm.disabled = true;
              try {
                const response = await fetch('/api/cart/confirm', {
                  method: 'POST', headers: {'Content-Type': 'application/json'},
                  body: JSON.stringify({session_id: sessionId, product_id: String(item.id), quantity: count}),
                });
                const payload = await response.json();
                if (!response.ok) throw new Error(payload.detail || 'Добавление не выполнено.');
                const link = document.createElement('a');
                link.href = '/cart';
                link.textContent = 'Открыть demo cart';
                pending.replaceChildren(line(payload.already_confirmed ? 'Это подтверждение уже обработано; дубль не создан.' : 'Товар добавлен в demo cart.'), link);
              } catch (error) {
                pending.append(line(error.message || 'Ошибка добавления.', 'requirements-warning'));
                confirm.disabled = false;
              }
            });
            const cancel = document.createElement('button');
            cancel.type = 'button';
            cancel.textContent = 'Отмена';
            cancel.addEventListener('click', async () => {
              cancel.disabled = true;
              try {
                const response = await fetch('/api/cart/cancel', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({session_id: sessionId})});
                if (!response.ok) throw new Error('Не удалось отменить добавление.');
                pending.replaceChildren(line('Добавление отменено.'));
              } catch (error) {
                pending.append(line(error.message, 'requirements-warning'));
                cancel.disabled = false;
              }
            });
            pending.append(confirm, cancel);
          } catch (error) {
            pending.replaceChildren(line(error.message || 'Ошибка проверки остатка.', 'requirements-warning'));
          } finally { prepare.disabled = false; }
        });
        addArea.append(quantity, prepare, pending);
      }
      card.append(addArea);
      results.append(card);
    }
    if (data.products.length >= 2 && draft?.requirements.length) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'requirements-compare-button';
      button.textContent = 'Сравнить выбранные товары';
      const comparison = document.createElement('div');
      comparison.className = 'requirements-comparison';
      button.addEventListener('click', async () => {
        if (selected.size < 2 || selected.size > 4) {
          comparison.replaceChildren(line('Выберите от двух до четырёх товаров.', 'requirements-warning'));
          return;
        }
        button.disabled = true;
        comparison.replaceChildren(line('Сравниваю проверенные карточки…'));
        try {
          const response = await fetch('/api/requirements/compare', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({session_id: sessionId, product_ids: [...selected]}),
          });
          const payload = await response.json();
          if (!response.ok) throw new Error(payload.detail || 'Не удалось сравнить товары.');
          renderComparison(comparison, payload);
        } catch (error) {
          comparison.replaceChildren(line(error.message || 'Ошибка сравнения.', 'requirements-warning'));
        } finally { button.disabled = false; }
      });
      results.append(button, comparison);
    }
  }

  function renderComparison(container, payload) {
    container.replaceChildren();
    const matrix = payload.matrix;
    const heading = document.createElement('h3');
    heading.textContent = 'Сравнение по вашим условиям';
    container.append(heading);
    if (!payload.coverage_complete) container.append(line('Поиск охватил не весь каталог.', 'requirements-warning'));
    const scroll = document.createElement('div');
    scroll.className = 'requirements-table-scroll';
    const table = document.createElement('table');
    const head = document.createElement('thead');
    const headingRow = document.createElement('tr');
    const requirementHeading = document.createElement('th');
    requirementHeading.textContent = 'Условие';
    headingRow.append(requirementHeading);
    for (const column of matrix.columns) {
      const cell = document.createElement('th');
      const link = document.createElement('a');
      link.href = column.url;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.textContent = `${column.name} · ${column.sku || column.id}`;
      cell.append(link);
      headingRow.append(cell);
    }
    head.append(headingRow);
    table.append(head);
    const body = document.createElement('tbody');
    for (const row of matrix.rows) {
      const tr = document.createElement('tr');
      const label = document.createElement('th');
      label.textContent = `${row.attribute}: ${row.wanted}${row.required ? ' · обязательно' : ''}`;
      tr.append(label);
      for (const value of row.cells) {
        const cell = document.createElement('td');
        cell.className = `comparison-${value.status === 'совпадает' ? 'match' : value.status === 'противоречит' ? 'conflict' : 'unknown'}`;
        cell.textContent = `${value.status} · ${value.actual ?? '—'}`;
        tr.append(cell);
      }
      body.append(tr);
    }
    table.append(body);
    scroll.append(table);
    container.append(scroll, line(payload.explanation));
    if (matrix.alternative_candidate_ids.length) container.append(line(`Кандидаты на замену по указанным обязательным полям: ${matrix.alternative_candidate_ids.join(', ')}. ${matrix.alternative_note}`));
    if (payload.ai_mode === 'offline') container.append(line('Объяснение сформировано локально: OpenAI API не настроен.'));
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
