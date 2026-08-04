/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Base URL for the backend API. Empty string / unset means same-origin,
   * relying on the Vite dev proxy (see vite.config.ts). */
  readonly VITE_API_BASE?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
