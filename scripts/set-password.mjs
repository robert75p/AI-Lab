#!/usr/bin/env node
// scripts/set-password.mjs
// Interactive utility to generate the credentials hash for serve.mjs.
//
// Usage:
//   node scripts/set-password.mjs
//
// It prints the SHA-256 hash of "username:password" and the exact line to
// paste into serve.mjs.  No credentials are written to any file.

import crypto   from 'crypto';
import readline from 'readline';
import fs       from 'fs';
import path     from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SERVE     = path.join(__dirname, '..', 'serve.mjs');

const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
const ask = (q) => new Promise(res => rl.question(q, res));

console.log('\n  TRABA Labs — Set Login Password');
console.log('  ================================\n');

const user = await ask('  Username: ');
const pass = await ask('  Password: ');
rl.close();

const hash = crypto.createHash('sha256').update(`${user}:${pass}`).digest('hex');

console.log('\n  Hash:');
console.log(`    ${hash}\n`);

// Offer to patch serve.mjs automatically
const src = fs.readFileSync(SERVE, 'utf8');
const current = src.match(/const CREDENTIALS_HASH = '([a-f0-9]+)'/)?.[1];

if (current === hash) {
  console.log('  serve.mjs already has this hash. Nothing to do.\n');
  process.exit(0);
}

const updated = src.replace(
  /const CREDENTIALS_HASH = '[a-f0-9]+'/,
  `const CREDENTIALS_HASH = '${hash}'`
);

if (updated === src) {
  console.log('  Could not locate CREDENTIALS_HASH in serve.mjs.');
  console.log('  Paste the line below into serve.mjs manually:\n');
  console.log(`    const CREDENTIALS_HASH = '${hash}';\n`);
  process.exit(0);
}

fs.writeFileSync(SERVE, updated);
console.log('  serve.mjs updated. Restart the server for the change to take effect.\n');
