import { createApp, server } from '@databricks/appkit';

// The engine API (FastAPI) base URL. The AppKit app is a thin frontend that proxies to it,
// forwarding the signed-in user's OBO token so the engine acts as the user (AK5). The token
// is read server-side from x-forwarded-access-token and NEVER exposed to the client bundle.
const ENGINE_URL = (process.env.APP_ENGINE_URL ?? '').replace(/\/+$/, '');

function bearerHeaders(req: {
  headers: Record<string, string | string[] | undefined>;
}): Record<string, string> {
  const raw = req.headers['x-forwarded-access-token'];
  const token = Array.isArray(raw) ? raw[0] : raw;
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (token) headers.Authorization = `Bearer ${token}`; // forward OBO token to the engine
  return headers;
}

function engineConfigured(): boolean {
  // Never forward the user's OBO token over cleartext.
  return ENGINE_URL.startsWith('https://');
}

createApp({
  plugins: [server()],
  onPluginsReady(appkit) {
    appkit.server.extend((app) => {
      // The signed-in user's identity (for the AI-trust badge). x-forwarded-email is
      // proxy-trust-only — display only, never an authorization input.
      app.get('/api/whoami', (req, res) => {
        const raw = req.headers['x-forwarded-email'];
        res.json({ email: (Array.isArray(raw) ? raw[0] : raw) ?? null });
      });

      // The signed-in user's Genie spaces (OBO) — proxied to the engine API.
      app.get('/api/spaces', async (req, res) => {
        if (!engineConfigured()) {
          res.status(500).json({ error: 'APP_ENGINE_URL must be set to an https engine API URL' });
          return;
        }
        try {
          const r = await fetch(`${ENGINE_URL}/spaces`, {
            headers: bearerHeaders(req),
            signal: AbortSignal.timeout(30_000),
          });
          res.status(r.status).json(await r.json());
        } catch {
          res.status(502).json({ error: 'engine API unreachable' });
        }
      });

      // The promotion review for a space (OBO) — proxied to the engine API.
      app.post('/api/review', async (req, res) => {
        if (!engineConfigured()) {
          res.status(500).json({ error: 'APP_ENGINE_URL must be set to an https engine API URL' });
          return;
        }
        const spaceId = (req.body as { space_id?: string } | undefined)?.space_id;
        if (!spaceId) {
          res.status(400).json({ error: 'space_id required' });
          return;
        }
        try {
          const r = await fetch(`${ENGINE_URL}/review`, {
            method: 'POST',
            headers: bearerHeaders(req),
            body: JSON.stringify({ space_id: spaceId }),
            signal: AbortSignal.timeout(180_000), // the review runs an LLM call
          });
          res.status(r.status).json(await r.json());
        } catch {
          res.status(502).json({ error: 'engine API unreachable' });
        }
      });
    });
  },
}).catch(console.error);
