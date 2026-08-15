import { copyFileSync, cpSync, existsSync, mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

const outputPath = resolve(process.cwd(), "supabase-config.js");
const env = process.env;
const publicDir = resolve(process.cwd(), "public");

const publicConfig = {
  url: env.SUPABASE_URL?.trim() || "",
  anonKey: (env.SUPABASE_PUBLISHABLE_KEY || env.SUPABASE_ANON_KEY || "").trim(),
  workspaceId: env.SUPABASE_WORKSPACE_ID?.trim() || "",
  // Vercel is the public demo: each visitor gets an isolated anonymous workspace.
  allowAnonymousBootstrap: Boolean(env.VERCEL)
    || /^(1|true|yes)$/i.test(env.ALLOW_ANONYMOUS_BOOTSTRAP || "false"),
  ...(env.PUBLIC_WORKER_URL?.trim() ? { workerUrl: env.PUBLIC_WORKER_URL.trim().replace(/\/$/, "") } : {}),
};

const required = {
  SUPABASE_URL: publicConfig.url,
  "SUPABASE_PUBLISHABLE_KEY (or SUPABASE_ANON_KEY)": publicConfig.anonKey,
  SUPABASE_WORKSPACE_ID: publicConfig.workspaceId,
};
const configuredCount = Object.values(required).filter(Boolean).length;

if (configuredCount > 0 && configuredCount < Object.keys(required).length) {
  const missing = Object.entries(required)
    .filter(([, value]) => !value)
    .map(([name]) => name)
    .join(", ");
  throw new Error(`Incomplete deployment configuration. Missing: ${missing}`);
}

if (configuredCount === 0 && existsSync(outputPath) && !env.VERCEL) {
  console.log("Keeping the existing local supabase-config.js.");
  process.exit(0);
}

const serialized = JSON.stringify(publicConfig, null, 2).replaceAll("<", "\\u003c");
const output = `// Generated at deploy time. Do not edit or commit this file.\nwindow.SUPABASE_CONFIG = ${serialized};\n`;
writeFileSync(outputPath, output, { encoding: "utf8", mode: 0o600 });

if (env.VERCEL) {
  mkdirSync(publicDir, { recursive: true });
  const publicAssets = [
    "index.html",
    "frontend.js",
    "frontend-agent-chain.js",
    "frontend-checker.js",
    "frontend-feedback.js",
    "frontend-document.js",
    "interview-workspace.js",
    "README.md",
  ];
  for (const asset of publicAssets) {
    copyFileSync(resolve(process.cwd(), asset), resolve(publicDir, asset));
  }
  cpSync(resolve(process.cwd(), "samples"), resolve(publicDir, "samples"), { recursive: true });
  writeFileSync(resolve(publicDir, "supabase-config.js"), output, { encoding: "utf8", mode: 0o600 });
}

console.log(
  configuredCount === Object.keys(required).length
    ? "Generated browser configuration for live Supabase mode."
    : "No public Supabase variables were supplied; generated demo-mode configuration.",
);
