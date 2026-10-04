// Dev-only lint config for the dashboard front end (WP-35). The dashboard itself has no build step and no
// runtime dependency: nothing here is loaded by the browser and no npm install is needed to run it.
// To lint (Node + network once, from the repo root):
//   npx eslint@9 -c docs/dashboard-platform/eslint.config.mjs docs/dashboard-platform/shell
// The rules that matter most also run without npm in ground_station/service/tests/ui_components_harness.js (L1-L3).
const browserGlobals = Object.fromEntries([
  'window', 'document', 'console', 'fetch', 'setTimeout', 'clearTimeout', 'setInterval', 'clearInterval',
  'requestAnimationFrame', 'cancelAnimationFrame', 'localStorage', 'sessionStorage', 'EventSource', 'WebSocket',
  'AbortController', 'Event', 'location', 'navigator', 'performance', 'getComputedStyle', 'ResizeObserver',
].map((g) => [g, 'readonly']));

export default [
  { ignores: ['**/shell/vendor/**'] },
  {
    files: ['**/dashboard-platform/shell/**/*.js'],
    languageOptions: { ecmaVersion: 2020, sourceType: 'script', globals: browserGlobals },
    rules: {
      'no-alert': 'error',                              // confirm/alert can be blocked: use GSUI.twoClick / toast
      'no-empty': ['error', { allowEmptyCatch: false }], // no silent catch {}: route errors through GSUI.report
      'no-undef': 'error',
      'no-implied-eval': 'error',
      'no-unused-vars': ['warn', { args: 'none' }],
      'eqeqeq': ['warn', 'smart'],
      'no-restricted-properties': ['error',
        { object: 'window', property: 'confirm', message: 'use GSUI.twoClick' },
        { object: 'window', property: 'alert', message: 'use GSUI.toast or an in-panel line' }],
    },
  },
];
