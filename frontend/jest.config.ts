import type { Config } from "jest";

const tsJestOptions = {
  tsconfig: "tsconfig.test.json",
  useESM: false,
  astTransformers: {
    before: [{ path: "./jest-import-meta-transformer.ts" }],
  },
};

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
    // Two entries on purpose — do NOT collapse them back into a single
    // `^.+\.[tj]sx?$` pattern. ts-jest runs `astTransformers` over TypeScript
    // sources only, so `jest-import-meta-transformer.ts` never sees the .js in
    // node_modules and any `import.meta` there survives into the CommonJS
    // output, where it is a syntax error. Every ESM-only dependency added to
    // the allowlist below depends on the wrapper to neutralize it first
    // (react-router's `import.meta.hot` guard is the current example).
    "^.+\\.tsx?$": ["ts-jest", tsJestOptions],
    // Also transform JavaScript so ts-jest can down-compile the ESM-only
    // react-markdown and react-router dependency chains to CommonJS. The
    // `[cm]?` covers .mjs/.cjs dist files such as cookie-es/dist/index.mjs.
    "^.+\\.[cm]?jsx?$": ["<rootDir>/jest-esm-js-transformer.cjs", tsJestOptions],
  },
  moduleFileExtensions: ["ts", "tsx", "js", "jsx", "json", "node"],
  testPathIgnorePatterns: ["/node_modules/", "/dist/", "/e2e/"],
  // By default Jest never transforms node_modules. react-markdown and its
  // remark/micromark/unified/mdast/hast/unist dependencies are ESM-only, as are
  // react-router and its cookie-es dependency, so they must be transformed.
  // Everything else in node_modules stays ignored.
  transformIgnorePatterns: [
    "/node_modules/(?!(@fluentui|axios|react-markdown|react-router|cookie-es|remark-.*|rehype-.*|micromark.*|mdast-.*|hast-.*|unist-.*|unified|bail|trough|vfile.*|is-plain-obj|trim-lines|property-information|comma-separated-tokens|space-separated-tokens|decode-named-character-reference|character-entities.*|html-url-attributes|devlop|zwitch|longest-streak|markdown-table|ccount|escape-string-regexp|estree-util-.*|hastscript|web-namespaces|stringify-entities|inline-style-parser|style-to-object)/)",
  ],
  globals: {},
};

export default config;
