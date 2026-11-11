/// <reference types="vite/client" />

/**
 * Client-side environment variables injected by Vite at build time.
 * Only `VITE_`-prefixed values are exposed to the browser.
 */
interface ImportMetaEnv {
  readonly VITE_SUPABASE_URL?: string;
  readonly VITE_SUPABASE_ANON_KEY?: string;
  readonly VITE_API_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
