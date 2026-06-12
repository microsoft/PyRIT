/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_URL?: string
  readonly VITE_ENABLE_TREE_UI?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
