// Copy this file to supabase-config.js and fill in values from Supabase.
// Local one-command startup: ./dev.sh
// It auto-generates supabase-config.worker.js so the page can trigger the local Worker.
//
// Auth: enable Anonymous Sign-ins in Supabase Dashboard > Authentication > Providers.
// The page calls signInAnonymously() on load; no email login is required.
// Run scripts/apply-anonymous-auth.sh once to apply the workspace bootstrap RPC.
window.SUPABASE_CONFIG = {
  url: "https://your-project.supabase.co",
  anonKey: "your-public-anon-key",
  workspaceId: "your-workspace-uuid",
};
