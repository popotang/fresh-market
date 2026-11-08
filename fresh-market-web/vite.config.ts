import { defineConfig, loadEnv } from "vite";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import htmlPlugin from "vite-plugin-index-html";
import pluginExternal from "vite-plugin-external";
import Pages from "vite-plugin-pages";
import externalGlobals from "rollup-plugin-external-globals";
import pkgJson from "./package.json";
import {
  BASE_INFO_SCRIPT_MARKER,
  EXTERNAL_ASSETS_START_MARKER,
  LIBRARY_CDN_PLACEHOLDER,
  injectProjectHtmlRouting,
  resolveProjectDomainRouting,
} from "./vite.region";

const version = pkgJson.version;
const appName = pkgJson.name.split("/").pop();
const __dirname = dirname(fileURLToPath(import.meta.url));

// https://vitejs.dev/config/
export default defineConfig(async ({ mode, command }) => {
  const env = loadEnv(mode, __dirname);
  const isDev = mode === "development";
  const port = Number(env.PORT) || 5173;
  const isCdn = !isDev && Boolean(process.env.CDN_DOMAIN);
  const outDir = isCdn ? `dist/${appName}/${version}` : "dist";
  const base = isCdn ? `${process.env.CDN_DOMAIN}${outDir}/` : "/";
  const routing = resolveProjectDomainRouting(__dirname);
  const templateContent = injectProjectHtmlRouting(
    readFileSync(join(__dirname, "index.html"), "utf8"),
    routing,
  );

  const localUrl = new URL(routing.local.origin);
  const httpsConfig =
    command === "serve" && routing.local.certificateUrl
      ? await (await fetch(routing.local.certificateUrl)).json()
      : undefined;
  const localUrlWithPort = (targetPort: number) => {
    const url = new URL(routing.local.origin);
    url.port = String(targetPort);
    return url.origin;
  };

  return {
    base,
    envDir: __dirname,
    // Service domains are resolved in app code by importing the routing
    // snapshot JSON directly (see src/api/public-domains.ts). No
    // bundler-specific `define` injection is needed for domain routing.
    plugins: [
      react(),
      {
        name: "lovrabet-html-routing",
        transformIndexHtml: {
          order: "pre",
          handler: (html) =>
            html.includes(BASE_INFO_SCRIPT_MARKER) ||
            html.includes(LIBRARY_CDN_PLACEHOLDER) ||
            html.includes(EXTERNAL_ASSETS_START_MARKER)
              ? injectProjectHtmlRouting(html, routing)
              : html,
        },
      },
      Pages({
        dirs: "src/pages",
        extensions: ["tsx"],
        routeStyle: "remix", // 使用 remix 风格，更好兼容 React Router v6
        importMode: "sync", // 使用同步导入避免 Suspense 问题
      }),
      htmlPlugin({
        input: "/src/main.tsx", // 使用绝对路径避免嵌套路由问题
        templateContent,
        preserveEntrySignatures: "exports-only",
      }),
      pluginExternal({
        // Vite 6+ external proxies cannot reliably expose named exports from
        // dependencies such as Ant Design when a package is imported directly
        // by the app entry. The legacy resolver plus externalGlobals performs
        // an explicit module-to-browser-global rewrite for both app and
        // transitive imports, without leaving bare module specifiers in ESM.
        rollback: true,
        externalGlobals,
        externals: {
          react: "React",
          "react-dom": "ReactDOM",
          antd: "antd",
          dayjs: "dayjs",
        },
      }),
    ],
    resolve: {
      alias: {
        "@": "/src",
      },
    },
    // 可选配置：提供https自签名证书及跨域访问能力
    // The local origin and certificate come from the generated public routing manifest.
    // 这些配置不是必须的，你也可以使用 proxy 等任意手段自行处理跨域问题
    server: isDev
      ? {
          port,
          open: localUrlWithPort(port),
          strictPort: true,
          host: "0.0.0.0",
          https: httpsConfig || undefined,
          hmr: {
            host: localUrl.hostname,
            port,
            protocol: localUrl.protocol === "https:" ? "wss" : "ws",
          },
          headers: {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods":
              "GET, POST, PUT, DELETE, PATCH, OPTIONS",
            "Access-Control-Allow-Headers":
              "X-Requested-With, Content-Type, Authorization",
          },
        }
      : undefined,
    // Preview follows the same generated local routing.
    preview: {
      port: 4173,
      open: localUrlWithPort(4173),
      strictPort: true,
      host: "0.0.0.0",
      https: httpsConfig || undefined,
      headers: {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods":
          "GET, POST, PUT, DELETE, PATCH, OPTIONS",
        "Access-Control-Allow-Headers":
          "X-Requested-With, Content-Type, Authorization",
      },
    },
    build: {
      outDir,
      target: "esnext",
      rollupOptions: {
        output: {
          format: "es" as const,
          entryFileNames: `assets/[name].js`,
          assetFileNames: `assets/[name].css`,
        },
      },
    },
    optimizeDeps: {
      include: ["react", "react-dom", "antd", "dayjs"],
    },
  };
});
