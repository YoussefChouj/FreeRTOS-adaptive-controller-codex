// Compatibility shim for `node --test <dir>`.
//
// On Node 22+ a positional directory argument to `node --test` is resolved as
// a module to execute instead of being searched for test files, so
// `node --test tests/` fails with "Cannot find module". Importing the .mjs
// test files here restores the directory behaviour on every Node version.
import('./tilt_core.test.mjs');
import('./zloop_core.test.mjs');
