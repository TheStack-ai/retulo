(() => {
  "use strict";
  const status = document.getElementById("copy-status");
  for (const button of document.querySelectorAll("button[data-copy]")) {
    button.hidden = false;
    button.addEventListener("click", async () => {
      const code = document.getElementById(button.dataset.copy);
      if (!code) return;
      try {
        if (!navigator.clipboard || !window.isSecureContext) throw new Error("clipboard unavailable");
        await navigator.clipboard.writeText(code.textContent);
        if (status) status.textContent = button.dataset.copied;
        button.textContent = button.dataset.copied;
        window.setTimeout(() => { button.textContent = button.dataset.label; }, 1800);
      } catch (_) {
        const selection = window.getSelection();
        const range = document.createRange();
        range.selectNodeContents(code);
        selection.removeAllRanges();
        selection.addRange(range);
        if (status) status.textContent = button.dataset.failed;
      }
    });
  }
})();
