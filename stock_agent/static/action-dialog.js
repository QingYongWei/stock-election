'use strict';
(() => {
  const dialog = document.getElementById('action-dialog');
  const byId = id => document.getElementById('action-dialog-' + id);
  let active = null;
  const variants = {
    danger: { icon: '!', badge: '危险操作', button: '确认删除' },
    fee: { icon: '◇', badge: '外部服务商费用', button: '确认并继续' },
    discard: { icon: '↶', badge: '放弃未保存修改', button: '放弃修改' },
    info: { icon: 'i', badge: '请确认操作边界', button: '确认继续' }
  };
  const value = input => input == null ? '' : String(input);
  function restoreFocus(previous) {
    const target = previous && previous.isConnected && !previous.disabled && !previous.closest('[inert]') ? previous : document.querySelector('main');
    if (!target) return;
    const temporary = !target.hasAttribute('tabindex') && target.tagName === 'MAIN';
    if (temporary) target.setAttribute('tabindex', '-1');
    target.focus({ preventScroll: true });
    if (temporary) target.removeAttribute('tabindex');
  }
  function cancel() { if (active) active.finish(false); }
  function confirm(options = {}) {
    // Never queue: a second business action must not run after this decision.
    if (active || dialog.open) return Promise.resolve(false);
    const variant = variants[options.kind] || variants.info;
    dialog.dataset.kind = Object.hasOwn(variants, options.kind) ? options.kind : 'info';
    byId('icon').textContent = variant.icon;
    byId('badge').textContent = variant.badge;
    byId('title').textContent = value(options.title || '确认继续此操作？');
    byId('description').textContent = value(options.description);
    byId('confirm').textContent = value(options.confirmText || variant.button);
    byId('cancel').textContent = value(options.cancelText || '取消');
    const context = byId('context');
    context.replaceChildren();
    for (const item of Array.isArray(options.details) ? options.details : []) {
      if (!item) continue;
      const label = document.createElement('dt');
      const content = document.createElement('dd');
      label.textContent = value(item.label);
      content.textContent = value(item.value);
      context.append(label, content);
    }
    context.hidden = !context.childNodes.length;
    const previous = document.activeElement;
    const overflow = document.body.style.overflow;
    return new Promise(resolve => {
      let settled = false;
      const listeners = [];
      const listen = (node, type, handler) => {
        node.addEventListener(type, handler);
        listeners.push(() => node.removeEventListener(type, handler));
      };
      const finish = accepted => {
        if (settled) return;
        settled = true;
        listeners.forEach(remove => remove());
        if (dialog.open) dialog.close();
        document.body.style.overflow = overflow;
        active = null;
        restoreFocus(previous);
        resolve(accepted === true);
      };
      active = { finish };
      listen(byId('cancel'), 'click', () => finish(false));
      listen(byId('close'), 'click', () => finish(false));
      listen(byId('confirm'), 'click', () => finish(true));
      listen(dialog, 'cancel', event => { event.preventDefault(); finish(false); });
      // A queued native close event from the previous invocation must not cancel a newly opened decision.
      listen(dialog, 'close', () => { if (!dialog.open) finish(false); });
      listen(dialog, 'click', event => {
        if (event.target !== dialog) return;
        const box = dialog.getBoundingClientRect();
        if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) finish(false);
      });
      listen(dialog, 'keydown', event => {
        if (event.key === 'Escape') { event.preventDefault(); finish(false); return; }
        // Enter on the container or descriptive content is never consent.
        if (event.key === 'Enter' && ![byId('confirm'), byId('cancel'), byId('close')].includes(document.activeElement)) {
          event.preventDefault(); return;
        }
        if (event.key !== 'Tab') return;
        const focusable = [...dialog.querySelectorAll('button:not(:disabled), [href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])')].filter(node => !node.hidden);
        const first = focusable[0], last = focusable[focusable.length - 1];
        if (!first) { event.preventDefault(); byId('cancel').focus(); return; }
        if (event.shiftKey && (document.activeElement === first || !dialog.contains(document.activeElement))) { event.preventDefault(); last.focus(); }
        else if (!event.shiftKey && (document.activeElement === last || !dialog.contains(document.activeElement))) { event.preventDefault(); first.focus(); }
      });
      try {
        document.body.style.overflow = 'hidden';
        dialog.showModal();
        byId('cancel').focus({ preventScroll: true });
      } catch (_) { finish(false); }
    });
  }
  window.AppDialog = Object.freeze({ confirm, cancel, get isOpen() { return active !== null; } });
})();
