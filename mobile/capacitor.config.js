const RAILWAY_HOST = process.env.REHABAI_HOST
  || 'https://rehabai-api-production.up.railway.app';

const config = {
  appId: 'ai.rehab.consumer',
  appName: 'RehabAI',
  webDir: 'www',
  server: {
    // Shareable APK loads the Talk-first consumer shell from the hosted FastAPI studio.
    // Override locally: set REHABAI_HOST=http://10.0.2.2:8000 before `npm run cap:sync`.
    url: `${RAILWAY_HOST.replace(/\/$/, '')}/?consumer=1#/app`,
    cleartext: RAILWAY_HOST.startsWith('http://'),
  },
  android: {
    allowMixedContent: true,
  },
};

module.exports = config;
