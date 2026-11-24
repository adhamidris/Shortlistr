(dialog) => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const visible = e => !!(e.getClientRects().length) && getComputedStyle(e).visibility !== 'hidden';
  const text = e => clean(e?.innerText || e?.textContent);
  const labelled = e => clean((e.getAttribute('aria-labelledby') || '').split(' ').map(id => document.getElementById(id)?.textContent || '').join(' '));
  const fields = [], grouped = new Set();
  const mark = e => { const id = `f${fields.length + 1}`; e.setAttribute('data-job-field', id); return id; };
  for (const e of dialog.querySelectorAll('input, textarea, select, [role="combobox"], [contenteditable="true"]')) {
    if (!visible(e) || e.type === 'hidden' || e.type === 'file') continue;
    const group = e.closest('fieldset, [role="radiogroup"]');
    if (group && ['radio','checkbox'].includes(e.type)) {
      if (grouped.has(group)) continue;
      grouped.add(group);
      const options = [...group.querySelectorAll('input')].filter(i => visible(i) && ['radio','checkbox'].includes(i.type));
      const id = mark(group);
      const choices = options.map((i,n) => {
        i.setAttribute('data-job-option', `${id}-${n}`);
        const label = clean([...i.labels || []].map(text).join(' '));
        let row = i.parentElement;
        while (row && row !== group && (!text(row) || row.querySelectorAll('input').length > 1)) row = row.parentElement;
        if (row && row !== group) row.setAttribute('data-job-choice', `${id}-${n}`);
        let optionLabel = label || (row !== group ? text(row) : '') || i.getAttribute('aria-label');
        return {key:`${id}-${n}`, label:optionLabel || '(unlabelled option)', checked:i.checked};
      });
      fields.push({id, kind:e.type === 'radio' ? 'radio' : 'checkboxes',
        label:labelled(group) || text(group.querySelector('legend')) || e.getAttribute('aria-label') || text(group).split(/Yes|No/)[0],
        required:group.getAttribute('aria-required') === 'true' || options.some(i => i.required) || /Required|\*/i.test(text(group.parentElement)), options:choices});
      continue;
    }
    const label = clean([...e.labels || []].map(text).join(' ')) || labelled(e) || e.getAttribute('aria-label') || e.getAttribute('placeholder');
    const kind = e.tagName === 'SELECT' ? 'select' : e.tagName === 'TEXTAREA' ? 'text' : e.getAttribute('role') === 'combobox' || e.getAttribute('aria-autocomplete') ? 'autocomplete' : e.type || 'unsupported';
    const f = {id:mark(e), kind, label:label || '(unlabelled control)', required:e.required || e.getAttribute('aria-required') === 'true', disabled:e.disabled || false,
      value:e.type === 'checkbox' ? e.checked : e.value ?? text(e)};
    if (kind === 'select') {
      const options = [...e.options].map(o => ({label:clean(o.text), value:o.value, disabled:o.disabled}));
      f.selected = [...e.selectedOptions].map(o => clean(o.text));
      f.option_count = options.length;
      if(options.length <= 30) f.options = options;
    }
    fields.push(f);
  }
  const buttons = [...dialog.querySelectorAll('button')].filter(visible).map(b => ({name:b.getAttribute('aria-label') || text(b), text:text(b), disabled:b.disabled}));
  const errors = [...dialog.querySelectorAll('[role="alert"], [aria-invalid="true"], p, span')].filter(visible).map(text)
    .filter(t => t && t.length < 350 && /invalid input|this field is required|please (?:enter|select|provide)|must be|maximum.*characters/i.test(t));
  const clone = dialog.cloneNode(true);
  clone.querySelectorAll('select, script, style, [aria-hidden="true"]').forEach(e => e.remove());
  clone.querySelectorAll('[data-job-field]').forEach(e => (e.closest('label') || e).remove());
  return {heading:dialog.getAttribute('aria-label') || labelled(dialog), fields, buttons,
    errors:[...new Set(errors)], text:clean([...clone.querySelectorAll('*')].filter(e => !e.children.length).map(e => e.textContent).join(' ')),
    unsupported:!!dialog.querySelector('iframe, [contenteditable="true"]')};
}
