# Arc landing site

The public face of Arc: a scrolling site with a graph of people, an explanation of the
pipeline, and a search box that can start a live research run on the backend.
Built with Vite, React and TypeScript. It lives next to `frontend/` (the Next.js
workspace) and does not replace it.

## Run it

Start the backend as described in the root README (port 8010), then:

```sh
cd landing
npm ci
npm run dev
```

Open http://localhost:5173.

In development Vite forwards `/api` to the backend, because the API only allows its own
frontend origin. Point it somewhere else with `ARC_API_URL`:

```sh
ARC_API_URL=http://127.0.0.1:8010 npm run dev
```

For a build served from another origin, set `VITE_API_URL` at build time and add that
origin to `ARC_FRONTEND_ORIGINS` on the backend.

## Check and build

```sh
npm run build   # type-checks, then builds to dist/
```

## Adding a person from the search box

Typing a first and last name that is not in the record and pressing Search calls
`POST /api/investigations` and follows `GET /api/investigate/stream/{id}`.
**Each run spends model and source credits on the backend.** Single words never start a run.

## Data

`src/data.ts` holds the people shown in the graph. The names, titles and companies come
from the first round judges slide. Replace the file with the backend's profile data when
it is wired in.
