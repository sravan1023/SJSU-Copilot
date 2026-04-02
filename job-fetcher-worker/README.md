# SJSU Job Fetcher Worker

Cloudflare Worker that pulls live public job listings from multiple public APIs and returns a normalized list to the UI.

## Sources
- RemoteOK
- Remotive
- Arbeitnow

## Local setup
1. Install dependencies:
   - `npm install`
2. Run locally:
   - `npm run dev`
3. Deploy:
   - `npm run deploy`

## UI integration
Set this in `UI/.env`:

```env
VITE_JOB_FETCHER_API_URL=http://127.0.0.1:8787/fetch
```

For production, point it at your deployed worker URL.

## Notes
- The worker only uses public sources with CORS-friendly endpoints.
- It normalizes jobs into the same schema used by the UI and Supabase.
