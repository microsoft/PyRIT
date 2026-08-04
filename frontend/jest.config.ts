import type { Config } from "jest";

const config: Config = {
  preset: "ts-jest",
  testEnvironment: "jsdom",
  roots: ["<rootDir>/src"],
  testMatch: ["**/*.test.ts", "**/*.test.tsx"],
  moduleNameMapper: {
    "^@/(.*)$": "<rootDir>/src/$1",
    "\\.(css|less|scss|sass)$": "identity-obj-proxy",
  },
  setupFilesAfterEnv: ["<rootDir>/src/setupTests.ts"],
  testTimeout: 15000,
  collectCoverageFrom: [
    "src/**/*.{ts,tsx}",
    "!src/**/*.d.ts",
    "!src/main.tsx",
    "!src/vite-env.d.ts",
    "!src/services/api.ts", // Mocked in tests - thin axios wrapper
  ],
  coverageThreshold: {
    global: {
      branches: 85,
      functions: 90,
      lines: 90,
      statements: 85,
    },
  },
  transform: {
    // Also transform .js/.jsx so ts-jest can down-compile the ESM-only
    // react-markdown dependency chain (whitelisted below) to CommonJS.
    "^.+\\.[tj]sx?$": [
      "ts-jest",
      {
        tsconfig: "tsconfig.test.json",
        useESM: false,
        astTransformers: {
          before: [{ path: "./jest-import-meta-transformer.ts" }],
        },
      },
    ],
  },
  moduleFileExtensions: ["ts", "tsx", "js", "jsx", "json", "node"],
  testPathIgnorePatterns: ["/node_modules/", "/dist/", "/e2e/"],
  // By default Jest never transforms node_modules. react-markdown and its
  // remark/micromark/unified/mdast/hast/unist dependencies are ESM-only, so
  // they must be transformed. Everything else in node_modules stays ignored.
  transformIgnorePatterns: [
    "/node_modules/(?!(@fluentui|axios|react-markdown|remark-.*|rehype-.*|micromark.*|mdast-.*|hast-.*|unist-.*|unified|bail|trough|vfile.*|is-plain-obj|trim-lines|property-information|comma-separated-tokens|space-separated-tokens|decode-named-character-reference|character-entities.*|html-url-attributes|devlop|zwitch|longest-streak|markdown-table|ccount|escape-string-regexp|estree-util-.*|hastscript|web-namespaces|stringify-entities|inline-style-parser|style-to-object)/)",
  ],
  globals: {},
};

export default config;
