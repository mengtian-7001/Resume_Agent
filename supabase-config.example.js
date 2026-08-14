// Copy this file to supabase-config.js and fill in values from Supabase.
// Local one-command startup: ./dev.sh
// It auto-generates supabase-config.worker.js so the page can trigger the local Worker.
//
// Auth (local demo):
// - Enable Anonymous Sign-ins in Supabase Dashboard > Authentication > Providers.
// - Set allowAnonymousBootstrap: true ONLY for local demo workspaces.
// - Apply migrations including 20260814020000_gate_anonymous_bootstrap.sql
//   and set workspaces.allow_anonymous_bootstrap=true for that workspace.
// Production: keep allowAnonymousBootstrap false (or omit) and use Email login.
window.SUPABASE_CONFIG = {
  url: "https://your-project.supabase.co",
  anonKey: "your-public-anon-key",
  workspaceId: "your-workspace-uuid",
  // DEV ONLY. Never enable on production workspaces that store real resumes.
  allowAnonymousBootstrap: false,
};
