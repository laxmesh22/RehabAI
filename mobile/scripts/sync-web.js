const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..', '..');
const web = path.join(root, 'web');
const www = path.join(root, 'mobile', 'www');

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

// Force consumer entry when the packaged www is opened without server.url.
let index = fs.readFileSync(path.join(www, 'index.html'), 'utf8');
if (!index.includes('consumer=1')) {
  index = index.replace(
    '<body>',
    `<body>
  <script>if (!/consumer=1/.test(location.search)) { location.replace('/?consumer=1#/app'); }</script>`
  );
  // Offline www fallback: relative API will fail; prefer server.url in capacitor.config.js.
  fs.writeFileSync(path.join(www, 'index.html'), index);
}
console.log('Synced web/ -> mobile/www');
