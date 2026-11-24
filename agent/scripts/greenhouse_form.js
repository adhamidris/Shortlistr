(root) => {
  const visible = e => !!e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden';
  const fields = [];
  for (const e of root.querySelectorAll('input,textarea,select')) {
    if (!e.id || !visible(e) || ['hidden','submit','search'].includes(e.type) || /recaptcha|captcha/.test(e.id)) continue;
    const label = [...(e.labels || [])].map(x=>x.innerText.trim()).join(' ');
    const group = e.closest('.select__container') || e.closest('.select') || e.parentElement.parentElement;
    const selected = e.getAttribute('role') === 'combobox' ? [...group.querySelectorAll('[class*="singleValue"],[class*="single-value"],[class*="multiValueLabel"],[class*="multi-value__label"]')].map(x=>x.textContent.trim()) : [];
    fields.push({id:e.id, label, kind:e.type==='file'?'file':e.type==='checkbox'?'checkbox':e.type==='radio'?'unsupported':e.tagName==='SELECT'?'select':e.getAttribute('role')==='combobox'?'choice':'text',
      required:e.required || label.endsWith('*'), disabled:e.disabled,
      value:e.type==='file'?[...e.files].map(x=>x.name):e.type==='checkbox'?e.checked:e.getAttribute('role')==='combobox'?selected:e.value,
      options:e.tagName==='SELECT'?[...e.options].map(x=>x.text):[]});
  }
  // Uploaded files replace the input with a filename and remove control.
  for (const group of root.querySelectorAll('.file-upload')) {
    const filename = group.querySelector('.file-upload__filename');
    const label = group.querySelector('label[for]');
    if (!filename || !visible(filename) || !label) continue;
    const id = label.htmlFor;
    const existing = fields.find(f=>f.id===id);
    const value = [filename.textContent.trim()];
    if (existing) existing.value=value;
    else fields.push({id,label:label.textContent.trim(),kind:'file',required:false,disabled:false,value,options:[]});
  }
  const uploading = [...root.querySelectorAll('.file-upload__progressbar')].some(visible);
  const errors = [...root.querySelectorAll('[role="alert"],[class*="error-message"],.field-error,[id$="-error"]')].filter(visible).map(e=>e.innerText.trim()).filter(Boolean);
  return {fields,uploading,errors:[...new Set(errors)],context:root.innerText,
    unsupported:[...root.querySelectorAll('input[type=radio],select[multiple],[contenteditable=true]')].some(visible)};
}
