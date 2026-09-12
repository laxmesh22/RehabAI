const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..', '..');
const web = path.join(root, 'web');
const www = path.join(root, 'mobile', 'www');
const apiOrigin = (process.env.REHABAI_HOST
  || 'https://rehabai-api-production.up.railway.app').replace(/\/$/, '');
// Clinic LAN fallback when Railway DNS fails on some phones (same Wi-Fi as PC).
const lanFallback = (process.env.REHABAI_LAN_API || 'http://172.16.6.7:8000').replace(/\/$/, '');
const fallbacks = [...new Set([lanFallback].filter(Boolean))];

fs.mkdirSync(www, { recursive: true });
for (const name of fs.readdirSync(web)) {
  const src = path.join(web, name);
  const dest = path.join(www, name);
  const stat = fs.statSync(src);
  if (stat.isDirectory()) {
    fs.cpSync(src, dest, { recursive: true });
  } else {
    fs.copyFileSync(src, dest);
  }
}

// Packaged Capacitor shell: assets at www root; API on hosted FastAPI / LAN.
let index = fs.readFileSync(path.join(www, 'index.html'), 'utf8');
index = index
  .replace(/href="\/ui\//g, 'href="./')
  .replace(/src="\/ui\//g, 'src="./')
  .replace(/"three": "\/ui\/vendor\/three\.module\.js"/g, '"three": "./vendor/three.module.js"');

const bootScript = `  <script>
    window.REHABAI_API_ORIGIN = ${JSON.stringify(apiOrigin)};
    window.REHABAI_API_FALLBACKS = ${JSON.stringify(fallbacks)};
    window.REHABAI_UI_BASE = './';
    window.REHABAI_PACKAGED = true;
    if (!/consumer=1/.test(location.search)) {
      location.replace(location.pathname + '?consumer=1' + (location.hash || '#/app'));
    }
  </script>
`;
if (index.includes('REHABAI_API_ORIGIN')) {
  index = index.replace(/<script>\s*window\.REHABAI_API_ORIGIN[\s\S]*?<\/script>\s*/m, bootScript);
} else {
  index = index.replace('<body>', '<body>\n' + bootScript);
}

fs.writeFileSync(path.join(www, 'index.html'), index);
console.log('Synced web/ -> mobile/www (API', apiOrigin + ', fallbacks', fallbacks.join(', ') + ')');
