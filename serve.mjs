import http from 'http';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

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

const CONFIG_DIR = path.join(__dirname, 'config');
if (!fs.existsSync(CONFIG_DIR)) fs.mkdirSync(CONFIG_DIR);

const server = http.createServer((req, res) => {
  let urlPath = req.url.split('?')[0];

  // POST /config/:filename — write a config file
  if (req.method === 'POST' && urlPath.startsWith('/config/')) {
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
        JSON.parse(body); // validate JSON before writing
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

  if (urlPath === '/') urlPath = '/index.html';

  const filePath = path.join(__dirname, urlPath);
  const ext = path.extname(filePath).toLowerCase();
  const contentType = MIME[ext] || 'application/octet-stream';

  fs.readFile(filePath, (err, data) => {
    if (err) {
      res.writeHead(404, { 'Content-Type': 'text/plain' });
      res.end('404 Not Found: ' + urlPath);
      return;
    }
    res.writeHead(200, {
      'Content-Type': contentType,
      'Cache-Control': 'no-cache',
    });
    res.end(data);
  });
});

server.listen(PORT, () => {
  console.log(`\n  AI Labs dev server running at http://localhost:${PORT}\n`);
});
