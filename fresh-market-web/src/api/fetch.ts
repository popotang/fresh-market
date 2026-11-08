import { LovrabetFetch } from "@lovrabet/fetch";
import { LOVRABET_API_DOMAIN, LOVRABET_RUNTIME_DOMAIN } from "./public-domains";

/**
 * Resolve the API service origin used by model calls and other platform APIs.
 *
 * The generated routing snapshot is the safe default. Customize this function
 * only when the application has an explicit business routing source, such as a
 * host-provided Domain configuration or a tenant-specific Domain mapping.
 * Return one absolute origin without a path, query, hash, or trailing slash.
 *
 * Do not infer the request target from `window.location.hostname`: the page can
 * be served from a local page address while intentionally calling any
 * configured Domain. If one feature needs another backend, create a
 * separate named `LovrabetFetch` instance instead of changing this shared one.
 */
export function resolveApiOrigin(): string {
  return LOVRABET_API_DOMAIN;
}

/**
 * Resolve the Runtime service origin used by files, users, and rich-editor
 * uploads.
 *
 * Keep this separate from `resolveApiOrigin`: API and Runtime may be deployed
 * on different Domains. Applications may replace the snapshot value with an
 * explicit host-provided Domain, but should keep one deterministic source
 * of truth and must not derive it from the browser page address.
 */
export function resolveRuntimeOrigin(): string {
  return LOVRABET_RUNTIME_DOMAIN;
}

/**
 * Create the two platform clients consumed by `YtConfigProvider`.
 *
 * This factory is also the preferred extension point for tests, previews, or
 * applications that receive final Domains during startup. Keep feature-specific
 * headers, response transforms, or separate business services in additional
 * named clients so these shared platform clients remain predictable.
 */
export function createLovrabetClients(domains: {
  api: string;
  runtime: string;
}) {
  return {
    apiFetch: new LovrabetFetch({
      baseURL: normalizeServiceOrigin(domains.api, "API Domain"),
      credentials: "include",
    }),
    runtimeFetch: new LovrabetFetch({
      baseURL: normalizeServiceOrigin(domains.runtime, "Runtime Domain"),
      credentials: "include",
    }),
  };
}

/**
 * Reject ambiguous service addresses at the application boundary.
 *
 * A service client owns request paths, so its base address must be an HTTPS
 * origin only. Keeping this check here also protects business overrides that
 * do not pass through the generated snapshot validator.
 */
function normalizeServiceOrigin(value: string, label: string): string {
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw new Error(`${label} must be an absolute HTTPS origin.`);
  }

  if (
    url.protocol !== "https:" ||
    url.username ||
    url.password ||
    url.pathname !== "/" ||
    url.search ||
    url.hash
  ) {
    throw new Error(`${label} must be an absolute HTTPS origin.`);
  }

  return url.origin;
}

/** Shared Lovrabet platform clients injected once from `src/main.tsx`. */
export const { apiFetch, runtimeFetch } = createLovrabetClients({
  api: resolveApiOrigin(),
  runtime: resolveRuntimeOrigin(),
});
