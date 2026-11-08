[Changelog](./CHANGELOG.md)

# Rabetbase React micro-frontend template

This is the React project template used by `rabetbase project create`. It shows how to load a React 18 + Vite app into the Lovrabet host app and call platform data models through `@lovrabet/sdk`.

You do not need to memorize a command list. Describe the development goal in Claude Code, Cursor, Codex, or a similar agent. `rabetbase` provides project config, model sync, build checks, and menu integration; the agent calls those capabilities from your prompt.

Use this template when you want to:

- develop a Lovrabet sub-app locally;
- embed the built app into the Lovrabet host app with icestark;
- have an agent refresh SDK model config with `rabetbase`;
- continue from the sample pages with prompts.

## Docs

This README only covers local template usage. Full agent workflows, prompt examples, SDK usage, and the publish path live in the Feishu wiki:

- [Rabetbase CLI docs home](https://qizhiyuntu.feishu.cn/wiki/EaApwb1Wpi2j0ykAkhNcya0ZnEh)
- [Onboarding](https://qizhiyuntu.feishu.cn/wiki/T5gJwrcQqixLCikNYO2caZYDnsc)
- [Core capabilities](https://qizhiyuntu.feishu.cn/wiki/S71hw5c2HiwyoHkLgyecKrI8n3M)
- [System page development with the SDK](https://qizhiyuntu.feishu.cn/wiki/XCdWwnN7ViYOc0knRu8c411uneh)
- [TypeScript SDK](https://qizhiyuntu.feishu.cn/wiki/B1PLw34AwiDtNmkCAlCcO3T2ngh)

## Stack

- `@lovrabet/sdk`: Lovrabet TypeScript SDK and model clients.
- `@lovrabet/components`: shared Lovrabet business components and AI controls.
- React 18 + TypeScript: pages and types.
- React Router v6: routing.
- Ant Design v5: UI components.
- Vite v7: local dev and production builds.
- `@ice/stark-app`: icestark runtime detection and basename.
- `vite-plugin-pages`: file-system routing from `src/pages`.
- ECharts: chart and data-screen samples.

Default UI copy in this template is English (including Ant Design `en_US`). Platform dataset names in generated `src/api/models.generated.ts` stay whatever the app models use.

## Layout

```text
.
├── src/
│   ├── api/
│   │   ├── api.ts              # stable SDK model entrypoint
│   │   ├── client.ts           # stable Lovrabet SDK client
│   │   ├── model-runtime.ts    # generated profile selector
│   │   ├── models.generated.ts # generated Dataset facts per profile
│   │   └── sdk-config.ts       # generated SDK compatibility settings
│   ├── layouts/
│   │   └── MainLayout.tsx
│   ├── pages/
│   │   ├── index.tsx
│   │   ├── sdk-demo/index.tsx
│   │   ├── components-demo/index.tsx
│   │   ├── workbench/index.tsx
│   │   ├── dashboard/index.tsx
│   │   └── data-screen/index.tsx
│   ├── router/index.tsx
│   ├── utils/api.ts
│   ├── main.tsx
│   └── style.css
├── docs/
│   └── quick-start.md
├── vite.config.ts
├── tsconfig.json
└── package.json
```

## Built-in pages

`vite-plugin-pages` generates routes from files under `src/pages`.

| File                                  | Route              | Purpose                                           |
| ------------------------------------- | ------------------ | ------------------------------------------------- |
| `src/pages/index.tsx`                 | `/`                | Prompt entry, project status, and agent scenarios |
| `src/pages/sdk-demo/index.tsx`        | `/sdk-demo`        | `@lovrabet/sdk` model list and data operations    |
| `src/pages/components-demo/index.tsx` | `/components-demo` | Reusable `@lovrabet/components` examples          |
| `src/pages/workbench/index.tsx`       | `/workbench`       | Workbench-style sample page                       |
| `src/pages/dashboard/index.tsx`       | `/dashboard`       | Dashboard sample                                  |
| `src/pages/data-screen/index.tsx`     | `/data-screen`     | Full-screen data display sample                   |

To add a page, put a `.tsx` file under `src/pages`. For example `src/pages/customer/index.tsx` becomes `/customer`.

### Components demo

Open `/components-demo` to explore working examples from `@lovrabet/components`. The page covers user selection, address selection, attachment uploads, rich-text editing and preview, JSON editing, and AI button and input interactions. Each example can reveal its corresponding source snippet for reuse in another page.

The user selector and attachment upload use the AppCode configured during `project create` or by a later `rabetbase config set appcode` command. Attachment uploads use the Lovrabet production service, while rich-text image uploads create local preview URLs and do not write files to a remote service. AI examples require a Prompt ID that the current app is authorized to use; the template does not provide or hard-code one.

## Quick start

### Requirements

- Node.js 20+
- `rabetbase` CLI installed; log in if you need to pull real API config

```bash
npm install -g @lovrabet/rabetbase-cli
rabetbase --help
```

The template does not bundle `rabetbase`. Commands such as `rabetbase run start` need the global CLI.

### Create a project from the template

Replace `YOUR_APP_CODE` with the current app's AppCode.

```bash
rabetbase project create my-sub-app --appcode YOUR_APP_CODE
cd my-sub-app
rabetbase run start
```

If you cloned this repo to debug the template itself, install dependencies first:

```bash
npm install
rabetbase run start
```

Rabetbase writes final public Domain routing to `rabetbase.domain-routing.json`. The official
local origins are:

```text
cn (or omitted): https://dev.lovrabet.com:5173
id:              https://dev.lovrabet.id:5173
```

Mainland China projects currently load React, Day.js, and Ant Design from
AliCDN, while ID projects currently use cdnjs. Every official node reads its
own final `cdn.libraries` and `cdn.lovrabet` values, so a future node can use an
independent CDN. Independent deployments read both explicit values from the
shared routing manifest. User, App, and Runtime Domains still follow the
selected region or explicit customer Domains.

To change the port: `PORT=3000 rabetbase run start`.

### Pull SDK config

When `project create` receives an AppCode, the CLI tries to generate `src/api/api.ts`. If you skipped AppCode, set it in the project and pull API config:

```bash
rabetbase config set appcode YOUR_APP_CODE
rabetbase api pull
```

If you cloned the template source and there is no `.rabetbase.json` yet:

```bash
rabetbase workspace init --appcode YOUR_APP_CODE
rabetbase api pull
```

Export the SDK client from `src/api/client.ts`:

```typescript
import { lovrabetClient } from "@/api/client";

const models = lovrabetClient.getModelList();
const data = await lovrabetClient.models.requirements.filter({
  currentPage: 1,
  pageSize: 20,
});
```

Model aliases and profile-specific Dataset codes come from the generated
`src/api/models.generated.ts`. Application code should continue importing the
stable `src/api/api.ts` and `src/api/client.ts` entrypoints.
`src/api/model-runtime.ts`, `src/api/sdk-config.ts`, and
`rabetbase.domain-routing.json` are generated by Rabetbase.
They contain only public routing fields and never CLI credentials. Run
`rabetbase project domain-routing-sync` after changing Domain configuration.
ID routing requires `@lovrabet/sdk` 1.5.3 or newer.

## Development notes

### Local scripts

Run scripts through the CLI:

```bash
rabetbase run start
rabetbase run build
rabetbase run preview
```

The underlying scripts in `package.json` map to Vite `start`, `build`, and `preview`.

### Micro-frontend entry

`src/main.tsx` supports both standalone and embedded runs:

- Standalone: `isInIcestark()` is false and the app renders into `#root`.
- Loaded by the Lovrabet host app: export `mount` / `unmount`.
- The React root is cached on the container so it is not created twice.

`src/router/index.tsx` reads icestark `getBasename()`, so one build can match the basename in Lovrabet page config.

### Build artifacts

```bash
rabetbase run build
```

Default output:

```text
dist/assets/main.js
dist/assets/main.css
```

Versioned CDN paths:

```bash
CDN_DOMAIN=https://your-cdn.com/ rabetbase run build
```

The build directory becomes `dist/<package-name>/<version>/`, and Vite `base` points at the matching CDN URL.

## Load into the Lovrabet host app

After uploading artifacts to CDN or a static host, configure a page in the Lovrabet app:

```text
Page name: SDK Demo
Route: /sdk-demo
Micro-app id: sub-app-react-demo
Load mode: import
Assets:
  https://your-cdn.com/path/to/assets/main.js
  https://your-cdn.com/path/to/assets/main.css
```

Notes:

- The route must match the route generated from `src/pages`.
- Vite artifacts must use the `import` load mode.
- Multiple Lovrabet pages in the same micro-frontend can share the same JS/CSS.
- If the page has nested routes, keep the `getBasename()` adapter.

## Common tasks

### Add a page

```text
src/pages/customer/index.tsx  ->  /customer
src/pages/customer/[id].tsx   ->  /customer/:id
```

If standalone mode should also show the page in the left menu, update `src/layouts/MainLayout.tsx`.

### Call Lovrabet data

Prefer the SDK client from `src/api/client.ts`. Use `src/utils/api.ts` only for custom runtime requests the SDK does not cover.

### Hide the local chrome when embedded

In icestark, `MainLayout` already returns `<Outlet />` only, so the host app does not get a second sidebar and header.

## FAQ

1. Port in use: run `PORT=3000 rabetbase run start`.
2. HTTPS cert fetch failed: check `local.certificateUrl` in `rabetbase.domain-routing.json` and confirm that the generated URL is reachable.
3. Route not found: the page must live under `src/pages` with a `.tsx` suffix.
4. SDK call failed: run `rabetbase api pull` and check the selected profile, AppCode, and model aliases in `src/api/models.generated.ts`.
5. Blank page after embed: confirm the Lovrabet page uses `import` and points at the built `main.js` and `main.css`.

## More docs

- [Rabetbase CLI docs home](https://qizhiyuntu.feishu.cn/wiki/EaApwb1Wpi2j0ykAkhNcya0ZnEh)
- [System page development with the SDK](https://qizhiyuntu.feishu.cn/wiki/XCdWwnN7ViYOc0knRu8c411uneh)
- [TypeScript SDK](https://qizhiyuntu.feishu.cn/wiki/B1PLw34AwiDtNmkCAlCcO3T2ngh)
- [Quick start](./docs/quick-start.md)
- [API rule changes](./docs/API_RULE_CHANGE.md)
- [Changelog](./CHANGELOG.md)
