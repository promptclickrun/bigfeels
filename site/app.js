"use strict";
const examples = {
  remember: {
    command: 'bigfeels-mem remember \\n  "Use SQLite for the portable build." \\n  --space project:portable --kind decision',
    label: 'Saved decision', content: 'Use SQLite for the portable build.',
    detail: 'A new memory retains the evidence from the save.'
  },
  inspect: {
    command: 'bigfeels-mem inspect MEMORY_ID \\n  --space project:portable',
    label: 'Source evidence', content: '“Use SQLite for the portable build.”',
    detail: 'Inspection exposes the stored record and its source evidence. MEMORY_ID stands for the ID returned by the save.'
  },
  correct: {
    command: 'bigfeels-mem correct MEMORY_ID \\n  "Use SQLite with WAL." \\n  --revision 1 --space project:portable',
    label: 'Corrected decision', content: 'Use SQLite with WAL.',
    detail: 'The current revision is required. The correction supersedes the old record while retaining its evidence.'
  }
};
const controls = document.querySelectorAll('[data-step]');
function selectStep(button) {
  const example = examples[button.dataset.step];
  if (!example) return;
  controls.forEach(control => {
    const active = control === button;
    control.classList.toggle('active', active);
    control.setAttribute('aria-pressed', String(active));
  });
  document.getElementById('demo-command').textContent = example.command.replaceAll('\\n', String.fromCharCode(92, 10));
  document.getElementById('demo-label').textContent = example.label;
  document.getElementById('demo-content').textContent = example.content;
  document.getElementById('demo-detail').textContent = example.detail;
}
controls.forEach(button => button.addEventListener('click', () => selectStep(button)));
const copyButton = document.getElementById('copy-install');
copyButton.addEventListener('click', async () => {
  const code = document.getElementById('install-commands');
  const status = document.getElementById('copy-status');
  try {
    await navigator.clipboard.writeText(code.textContent);
    status.textContent = 'Commands copied.';
  } catch {
    const range = document.createRange();
    range.selectNodeContents(code);
    const selection = window.getSelection();
    selection.removeAllRanges(); selection.addRange(range);
    status.textContent = 'Copy was blocked. The commands are selected so you can copy them manually.';
  }
});
