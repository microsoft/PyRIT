import js from "@eslint/js";
import tseslint from "@typescript-eslint/eslint-plugin";
import tsParser from "@typescript-eslint/parser";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import globals from "globals";

export default [
  js.configs.recommended,
  {
    ignores: ["dist/**", "node_modules/**", "coverage/**", "playwright-report/**", "test-results/**", ".vite/**"],
  },
  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      parser: tsParser,
      parserOptions: {
        ecmaVersion: "latest",
        sourceType: "module",
        ecmaFeatures: {
          jsx: true,
        },
      },
      globals: {
        ...globals.browser,
        ...globals.es2021,
        React: "readonly",
      },
    },
    plugins: {
      "@typescript-eslint": tseslint,
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
    },
    rules: {
      ...tseslint.configs.recommended.rules,
      ...reactHooks.configs.recommended.rules,
      "react-refresh/only-export-components": ["warn", { allowConstantExport: true }],
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
    },
  },
  // Test files configuration
  {
    files: ["**/*.test.{ts,tsx}", "**/setupTests.ts"],
    languageOptions: {
      globals: {
        ...globals.jest,
        ...globals.node,
        React: "readonly",
      },
    },
  },
  // Node.js config files (vite, playwright, etc.)
  {
    files: ["vite.config.ts", "playwright.config.ts", "jest.config.ts"],
    languageOptions: {
      globals: {
        ...globals.node,
      },
    },
  },
  // E2E test files (Playwright, run in Node.js)
  {
    files: ["e2e/**/*.{ts,tsx}"],
    languageOptions: {
      globals: {
        ...globals.node,
      },
    },
  },
];
