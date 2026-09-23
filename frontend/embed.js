/* Drop-in prototype launcher. It never receives API credentials. */
(() => {
  if (document.getElementById('ekt-assistant-embed')) return;
  const source = document.currentScript?.src;
  if (!source) return;
  const origin = new URL(source).origin;
  const host = document.createElement('div');
  host.id = 'ekt-assistant-embed';
  const shadow = host.attachShadow({mode: 'open'});
  const style = document.createElement('style');
  style.textContent = `
    button{position:fixed;right:20px;bottom:20px;z-index:2147483647;border:0;border-radius:999px;padding:14px 18px;background:#176744;color:white;font:600 14px Arial,sans-serif;box-shadow:0 8px 24px #10251f40;cursor:pointer}
    iframe{position:fixed;right:20px;bottom:76px;z-index:2147483646;width:min(420px,calc(100vw - 24px));height:min(680px,calc(100vh - 96px));border:1px solid #dfe8de;border-radius:18px;background:white;box-shadow:0 14px 44px #10251f33}
    iframe[hidden]{display:none}
    @media(max-width:600px){button{right:12px;bottom:12px}iframe{right:0;bottom:64px;width:100vw;height:calc(100vh - 72px);border-radius:14px 14px 0 0}}
  `;
  const button = document.createElement('button');
  button.type = 'button';
  button.textContent = 'Помощник ЭКТ';
  button.setAttribute('aria-expanded', 'false');
  button.setAttribute('aria-controls', 'ekt-assistant-frame');
  const frame = document.createElement('iframe');
  frame.id = 'ekt-assistant-frame';
  frame.title = 'Чат помощника ЭКТ — демо-прототип';
  frame.src = `${origin}/widget`;
  frame.hidden = true;
  button.addEventListener('click', () => {
    frame.hidden = !frame.hidden;
    button.textContent = frame.hidden ? 'Помощник ЭКТ' : 'Закрыть помощника';
    button.setAttribute('aria-expanded', String(!frame.hidden));
  });
  shadow.append(style, button, frame);
  document.body.append(host);
})();
