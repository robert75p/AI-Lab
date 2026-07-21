import http from 'http';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import crypto from 'crypto';
import { spawn } from 'child_process';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PORT = 3000;

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.css':  'text/css',
  '.js':   'text/javascript',
  '.mjs':  'text/javascript',
  '.json': 'application/json',
  '.png':  'image/png',
  '.jpg':  'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.svg':  'image/svg+xml',
  '.ico':  'image/x-icon',
  '.woff2':'font/woff2',
  '.woff': 'font/woff',
  '.txt':  'text/plain',
};

// SHA-256 hash of "USERNAME:PASSWORD" — plaintext never stored
const CREDENTIALS_HASH = '99dc1bbbeae936500ee0b130042cd20e033d2ded0bd625004230328cff1131f4';
const PROTECTED_PAGES  = new Set([
  '/pages/ai-tools.html',
  '/pages/ai-topics.html',
  '/pages/ai-robot.html',
  '/pages/ai-pattern.html',
  '/pages/jira-ai.html',
  '/pages/market-intelligence.html',
]);
const READONLY = false; // set to true on the deploy server

// In-memory session tokens (cleared on server restart)
const sessions = new Set();

function getSessionToken(req) {
  const cookies = req.headers['cookie'] || '';
  const match   = cookies.match(/(?:^|;\s*)traba_session=([a-f0-9]+)/);
  return match ? match[1] : null;
}

function isAuthenticated(req) {
  const token = getSessionToken(req);
  return token ? sessions.has(token) : false;
}

function redirectToLogin(res, nextUrl) {
  res.writeHead(302, { 'Location': '/login.html?next=' + encodeURIComponent(nextUrl) });
  res.end();
}

const CONFIG_DIR  = path.join(__dirname, 'config');
if (!fs.existsSync(CONFIG_DIR)) fs.mkdirSync(CONFIG_DIR);

const PYTHON = process.platform === 'win32' ? 'python' : 'python3';

// Canonical filenames the pipeline expects, keyed by slot name sent from the browser
const RECON_SLOT_MAP = {
  'ORDER__source':      'ORDER__source.csv',
  'ORDER__target':      'ORDER__target.csv',
  'ORDER_FILL__source': 'ORDER_FILL__source.csv',
  'ORDER_FILL__target': 'ORDER_FILL__target.csv',
  'FILL__source':       'FILL__source.csv',
  'FILL__target':       'FILL__target.csv',
};

function todayUTC() {
  return new Date().toISOString().slice(0, 10);
}

function readBody(req, maxBytes = 64 * 1024 * 1024) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let total = 0;
    req.on('data', chunk => {
      total += chunk.length;
      if (total > maxBytes) { req.destroy(); reject(new Error('Request too large')); }
      else chunks.push(chunk);
    });
    req.on('end', () => resolve(Buffer.concat(chunks)));
    req.on('error', reject);
  });
}

const server = http.createServer((req, res) => {
  let urlPath = req.url.split('?')[0];

  // POST /auth — process login form
  if (req.method === 'POST' && urlPath === '/auth') {
    let body = '';
    req.on('data', chunk => { body += chunk; });
    req.on('end', () => {
      const params   = new URLSearchParams(body);
      const user     = params.get('username') || '';
      const pass     = params.get('password') || '';
      const next     = params.get('next')     || '/';
      const hash     = crypto.createHash('sha256').update(user + ':' + pass).digest('hex');
      if (hash === CREDENTIALS_HASH) {
        const token = crypto.randomBytes(32).toString('hex');
        sessions.add(token);
        res.writeHead(302, {
          'Set-Cookie': 'traba_session=' + token + '; HttpOnly; SameSite=Strict; Path=/',
          'Location':   next,
        });
        res.end();
      } else {
        res.writeHead(302, {
          'Location': '/login.html?next=' + encodeURIComponent(next) + '&error=1',
        });
        res.end();
      }
    });
    return;
  }

  // Auth guard for protected pages
  if (PROTECTED_PAGES.has(urlPath) && !isAuthenticated(req)) {
    redirectToLogin(res, urlPath);
    return;
  }

  // POST /config/:filename — blocked in read-only mode
  if (req.method === 'POST' && urlPath.startsWith('/config/')) {
    if (READONLY) {
      res.writeHead(403, { 'Content-Type': 'text/plain' });
      res.end('Server is read-only');
      return;
    }
    const filename = path.basename(urlPath);
    if (!filename.endsWith('.json')) {
      res.writeHead(400, { 'Content-Type': 'text/plain' });
      res.end('Only .json files allowed');
      return;
    }
    const dest = path.join(CONFIG_DIR, filename);
    let body = '';
    req.on('data', chunk => { body += chunk; });
    req.on('end', () => {
      try {
        JSON.parse(body);
        fs.writeFile(dest, body, err => {
          if (err) {
            res.writeHead(500, { 'Content-Type': 'text/plain' });
            res.end('Write failed');
          } else {
            res.writeHead(200, { 'Content-Type': 'application/json' });
            res.end('{"ok":true}');
          }
        });
      } catch(e) {
        res.writeHead(400, { 'Content-Type': 'text/plain' });
        res.end('Invalid JSON');
      }
    });
    return;
  }

  // POST /recon/upload — save CSV pairs to data/db_recon/<today>/
  if (req.method === 'POST' && urlPath === '/recon/upload') {
    if (!isAuthenticated(req)) {
      res.writeHead(401, { 'Content-Type': 'text/plain' }); res.end('Unauthorized'); return;
    }
    readBody(req).then(buf => {
      const payload = JSON.parse(buf.toString());
      const date    = todayUTC();
      const dir     = path.join(__dirname, 'data', 'db_recon', date);
      fs.mkdirSync(dir, { recursive: true });

      const saved = [];
      for (const f of (payload.files || [])) {
        const canonical = RECON_SLOT_MAP[f.slot];
        if (!canonical) {
          res.writeHead(400, { 'Content-Type': 'text/plain' });
          res.end(`Unknown slot: ${f.slot}`); return;
        }
        fs.writeFileSync(path.join(dir, canonical), Buffer.from(f.content_b64, 'base64'));
        saved.push(canonical);
      }
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, date, folder: dir, saved }));
    }).catch(e => {
      res.writeHead(400, { 'Content-Type': 'text/plain' }); res.end('Upload error: ' + e.message);
    });
    return;
  }

  // POST /recon/execute — run comparison pipeline on an already-uploaded folder
  if (req.method === 'POST' && urlPath === '/recon/execute') {
    if (!isAuthenticated(req)) {
      res.writeHead(401, { 'Content-Type': 'text/plain' }); res.end('Unauthorized'); return;
    }
    readBody(req).then(buf => {
      const { folder } = JSON.parse(buf.toString());
      // Validate: folder must be inside data/db_recon/ and end with YYYY-MM-DD
      const absFolder = path.resolve(folder);
      const reconRoot = path.resolve(path.join(__dirname, 'data', 'db_recon'));
      if (!absFolder.startsWith(reconRoot) || !/\d{4}-\d{2}-\d{2}$/.test(absFolder)) {
        res.writeHead(400, { 'Content-Type': 'text/plain' }); res.end('Invalid folder'); return;
      }

      const orchestrator = path.join(__dirname, 'scripts', 'db_recon', 'run_daily_recon.py');
      const config       = path.join(__dirname, 'scripts', 'db_recon', 'db2_recon_config.yaml');
      const logs = [];

      const child = spawn(PYTHON, [
        orchestrator,
        '--config',       config,
        '--folder',       absFolder,
        '--skip-extract',
      ], { cwd: __dirname });

      child.stdout.on('data', d => logs.push({ s: 'out', t: d.toString() }));
      child.stderr.on('data', d => logs.push({ s: 'err', t: d.toString() }));
      child.on('close', code => {
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: code === 0 || code === 2, exit_code: code, logs }));
      });
      child.on('error', e => {
        res.writeHead(500, { 'Content-Type': 'text/plain' }); res.end('Spawn error: ' + e.message);
      });
    }).catch(e => {
      res.writeHead(400, { 'Content-Type': 'text/plain' }); res.end('Execute error: ' + e.message);
    });
    return;
  }

  if (urlPath === '/') urlPath = '/index.html';

  const filePath    = path.join(__dirname, urlPath);
  const ext         = path.extname(filePath).toLowerCase();
  const contentType = MIME[ext] || 'application/octet-stream';

  fs.readFile(filePath, (err, data) => {
    if (err) {
      res.writeHead(404, { 'Content-Type': 'text/plain' });
      res.end('404 Not Found: ' + urlPath);
      return;
    }
    res.writeHead(200, {
      'Content-Type':  contentType,
      'Cache-Control': 'no-cache',
    });
    res.end(data);
  });
});

server.listen(PORT, () => {
  console.log(`\n  AI Labs dev server running at http://localhost:${PORT}\n`);
});
