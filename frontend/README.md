# ARC frontend

The frontend uses the Next.js App Router, TypeScript, Tailwind CSS, and ESLint.
Install the locked dependencies with `npm ci`.

```sh
NEXT_PUBLIC_API_URL=http://127.0.0.1:8010 npm run dev -- --hostname 127.0.0.1 --port 3000
```

Open [ARC](http://127.0.0.1:3000).
Start the backend and source MCP server using the [root setup instructions](../README.md).
Set `NEXT_PUBLIC_API_URL` in `.env.local` to retain the API location.
The fallback API URL is `http://localhost:8000` when no override is configured.
Set this value before a production build.
Provider credentials belong in the backend's ignored `.env`, never in browser code or `NEXT_PUBLIC_*` variables.

`app/page.tsx` loads the workspace, while `app/u/[profileId]/page.tsx` renders public profiles.
`components/` contains conversation, evidence, graph, dossier, peer-claim, and private-journal views.
`lib/` contains API clients and the investigation event state.

Opening the application loads saved history without starting an investigation.
The evidence workbench supports explicit free source lookups and report exports.
Starting the Conversation workflow uses the backend's configured provider credits.
Saved sessions replay their persisted events; cancellation, human questions, and source failures remain visible in the interface.

```sh
npm run lint
NEXT_PUBLIC_API_URL=http://127.0.0.1:8010 npm run build
npm start -- --hostname 127.0.0.1 --port 3000
```
