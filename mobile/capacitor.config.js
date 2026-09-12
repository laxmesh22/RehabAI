const RAILWAY_HOST = (process.env.REHABAI_HOST
  || 'https://rehabai-api-production.up.railway.app').replace(/\/$/, '');

// Default: load UI from the APK package (always opens). API still hits Railway.
// Old remote-WebView mode (can show "webpage not available"): REHABAI_REMOTE_UI=1
const useRemoteUi = String(process.env.REHABAI_REMOTE_UI || '').trim() === '1';

const config = {
  appId: 'ai.rehab.consumer',
  appName: 'RehabAI',
  webDir: 'www',
  android: {
    allowMixedContent: true,
  },
};

if (useRemoteUi) {
  config.server = {
    url: `${RAILWAY_HOST}/?consumer=1#/app`,
    cleartext: RAILWAY_HOST.startsWith('http://'),
  };
} else {
  // Packaged www. cleartext=true so LAN http://IP:8000 API works when Railway DNS fails.
  config.server = {
    cleartext: true,
    allowNavigation: [
      RAILWAY_HOST,
      'http://172.*',
      'http://192.168.*',
      'http://10.*',
    ],
  };
}

module.exports = config;
