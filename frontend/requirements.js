/* Editable extraction preview. Search-by-requirements follows in the next extension sprint. */
(() => {
  const form = document.querySelector('#requirements-form');
  if (!form) return;
  const input = document.querySelector('#requirements-input');
  const feedback = document.querySelector('#requirements-feedback');
  const panel = document.querySelector('#requirements-draft');
  const fields = document.querySelector('#requirements-fields');
  const question = document.querySelector('#requirements-question');
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
    field.addEventListener('input', () => onChange(field.value));
    field.addEventListener('change', () => onChange(field.value));
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
      required.addEventListener('change', () => { item.required = required.checked; });
      requiredLabel.append(required, document.createTextNode('Обязательно'));
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.textContent = 'Убрать';
      remove.addEventListener('click', () => { draft.requirements.splice(index, 1); render(); });
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
    render();
  });

  document.querySelector('#requirements-save').addEventListener('click', async () => {
    if (!draft) return;
    try {
      const response = await fetch(`/api/requirements/${encodeURIComponent(sessionId)}`, {
        method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({requirements: draft}),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Проверьте заполненные условия.');
      draft = result.requirements;
      feedback.className = 'requirements-feedback';
      feedback.textContent = 'Исправления сохранены для этого диалога. Для поиска по артикулу или названию используйте чат.';
      render();
    } catch (error) {
      feedback.className = 'requirements-feedback error';
      feedback.textContent = error.message || 'Не удалось сохранить условия.';
    }
  });

  document.addEventListener('ekt:session-reset', () => {
    draft = null;
    input.value = '';
    feedback.textContent = '';
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
