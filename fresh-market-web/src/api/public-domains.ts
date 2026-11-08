/**
 * Public Lovrabet domains for this project.
 *
 * Source of truth is the generated routing snapshot (`rabetbase.domain-routing.json`).
 * We import it directly as JSON — a standard, bundler-agnostic mechanism
 * (Vite / webpack / rspack / esbuild / rollup all support JSON imports), so
 * domain resolution is not tied to any Vite-specific `define` injection.
 * Values are public service domains and contain no credentials.
 */
import routing from "../../rabetbase.domain-routing.json";

const domains = routing.domains as {
  api: string;
  app: string;
  runtime: string;
};

export const LOVRABET_API_DOMAIN = domains.api;
export const LOVRABET_APP_DOMAIN = domains.app;
export const LOVRABET_RUNTIME_DOMAIN = domains.runtime;
