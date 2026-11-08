import { readFileSync } from "node:fs";
import { join } from "node:path";

export const PROJECT_DOMAIN_ROUTING_FILE_NAME = "rabetbase.domain-routing.json";
export const PROJECT_DOMAIN_ROUTING_PROTOCOL =
  "lovrabet-project-domain-routing/v1";
export const BASE_INFO_SCRIPT_MARKER = "<!-- lovrabet-base-info -->";
export const EXTERNAL_ASSETS_START_MARKER =
  "<!-- lovrabet-external-assets:start -->";
export const EXTERNAL_ASSETS_END_MARKER =
  "<!-- lovrabet-external-assets:end -->";
export const LIBRARY_CDN_PLACEHOLDER = "__LOVRABET_LIBRARY_CDN__";

export interface LovrabetProjectDomainRouting {
  protocol: typeof PROJECT_DOMAIN_ROUTING_PROTOCOL;
  mode: "official" | "independent";
  region: string | null;
  domains: {
    api: string;
    user: string;
    app: string;
    runtime: string;
    // Reserved for app-owned clients that are not created by this template yet.
    skill?: string;
    kb?: string;
  };
  cdn: {
    libraries: string;
    lovrabet: string;
  };
  local: {
    origin: string;
    certificateUrl?: string;
  };
}

export function injectBaseInfoScript(
  html: string,
  baseInfoScriptUrl: string,
): string {
  if (!html.includes(BASE_INFO_SCRIPT_MARKER)) {
    throw new Error(
      `Project index.html must contain ${BASE_INFO_SCRIPT_MARKER}.`,
    );
  }
  return html.replace(
    BASE_INFO_SCRIPT_MARKER,
    `<script src="${baseInfoScriptUrl}"></script>`,
  );
}

export function injectProjectHtmlRouting(
  html: string,
  routing: LovrabetProjectDomainRouting,
): string {
  const withBaseInfo = injectBaseInfoScript(
    html,
    `${routing.domains.user}/base-info.js`,
  );
  const start = withBaseInfo.indexOf(EXTERNAL_ASSETS_START_MARKER);
  const end = withBaseInfo.indexOf(EXTERNAL_ASSETS_END_MARKER);
  if (start < 0 || end < start) {
    throw new Error(
      "Project index.html must contain the Lovrabet external-assets block.",
    );
  }

  if (!withBaseInfo.includes(LIBRARY_CDN_PLACEHOLDER)) {
    throw new Error(
      `Project index.html must contain ${LIBRARY_CDN_PLACEHOLDER}.`,
    );
  }
  return withBaseInfo
    .replaceAll(LIBRARY_CDN_PLACEHOLDER, routing.cdn.libraries)
    .replace(EXTERNAL_ASSETS_START_MARKER, "")
    .replace(EXTERNAL_ASSETS_END_MARKER, "");
}

export function resolveProjectDomainRouting(
  projectRoot: string,
): LovrabetProjectDomainRouting {
  const filePath = join(projectRoot, PROJECT_DOMAIN_ROUTING_FILE_NAME);
  let parsed: unknown;
  try {
    parsed = JSON.parse(readFileSync(filePath, "utf8"));
  } catch (error) {
    throw new Error(
      `Unable to read ${PROJECT_DOMAIN_ROUTING_FILE_NAME}. Run rabetbase project domain-routing-sync to refresh Domain routing.`,
      { cause: error },
    );
  }
  return validateProjectRouting(parsed);
}

function validateProjectRouting(input: unknown): LovrabetProjectDomainRouting {
  if (!input || typeof input !== "object" || Array.isArray(input)) {
    throw invalidRouting();
  }
  const source = input as Record<string, any>;
  if (
    source.protocol !== PROJECT_DOMAIN_ROUTING_PROTOCOL ||
    !["official", "independent"].includes(source.mode) ||
    !isRegionValue(source.region) ||
    !source.domains ||
    !source.cdn ||
    !source.local
  ) {
    throw invalidRouting();
  }

  for (const field of ["api", "user", "app", "runtime"] as const) {
    assertOrigin(source.domains[field], `domains.${field}`, ["https:"]);
  }
  for (const field of ["skill", "kb"] as const) {
    if (source.domains[field] !== undefined) {
      assertOrigin(source.domains[field], `domains.${field}`, ["https:"]);
    }
  }
  assertHttpsBaseUrl(source.cdn.libraries, "cdn.libraries");
  assertOrigin(source.cdn.lovrabet, "cdn.lovrabet", ["https:"]);
  assertOrigin(source.local.origin, "local.origin", ["http:", "https:"]);
  if (source.local.certificateUrl !== undefined) {
    assertHttpsUrl(source.local.certificateUrl, "local.certificateUrl");
  }
  if (
    new URL(source.local.origin).protocol === "https:" &&
    !source.local.certificateUrl
  ) {
    throw new Error(
      `Invalid ${PROJECT_DOMAIN_ROUTING_FILE_NAME}: local.certificateUrl is required for an HTTPS local origin.`,
    );
  }
  return source as LovrabetProjectDomainRouting;
}

function assertHttpsBaseUrl(value: unknown, field: string): void {
  try {
    const normalized = String(value).replace(/\/$/, "");
    const url = new URL(normalized);
    if (
      url.protocol === "https:" &&
      !url.username &&
      !url.password &&
      !url.search &&
      !url.hash &&
      url.href.replace(/\/$/, "") === normalized
    ) {
      return;
    }
  } catch {
    // Fall through to the stable validation error below.
  }
  throw new Error(
    `Invalid ${PROJECT_DOMAIN_ROUTING_FILE_NAME}: ${field} must be an HTTPS base URL.`,
  );
}

function isRegionValue(value: unknown): value is string | null {
  return (
    value === null ||
    (typeof value === "string" && /^[a-z][a-z0-9-]*$/.test(value))
  );
}

function assertOrigin(
  value: unknown,
  field: string,
  protocols: string[],
): void {
  try {
    const url = new URL(String(value));
    if (protocols.includes(url.protocol) && url.href === `${url.origin}/`) {
      return;
    }
  } catch {
    // Fall through to the stable validation error below.
  }
  throw new Error(
    `Invalid ${PROJECT_DOMAIN_ROUTING_FILE_NAME}: ${field} must be a supported origin.`,
  );
}

function assertHttpsUrl(value: unknown, field: string): void {
  try {
    const url = new URL(String(value));
    if (url.protocol === "https:") return;
  } catch {
    // Fall through to the stable validation error below.
  }
  throw new Error(
    `Invalid ${PROJECT_DOMAIN_ROUTING_FILE_NAME}: ${field} must be an HTTPS URL.`,
  );
}

function invalidRouting(): Error {
  return new Error(
    `Invalid ${PROJECT_DOMAIN_ROUTING_FILE_NAME}: expected ${PROJECT_DOMAIN_ROUTING_PROTOCOL}.`,
  );
}
