import type { Config } from "jest";

const config: Config = {
  preset: "ts-jest",
  testEnvironment: "jsdom",
  roots: ["<rootDir>/src"],
  testMatch: ["**/*.test.ts", "**/*.test.tsx"],
  moduleNameMapper: {
    "^@/(.*)$": "<rootDir>/src/$1",
    "\\.(css|less|scss|sass)$": "identity-obj-proxy",
    // d3-hierarchy ships ESM source as `main`. ts-jest's transform
    // ignores `.js` and jest's CJS require trips on its `import`
    // statements. Redirect to the UMD bundle at /dist which works
    // under CJS without any transform. Production keeps the ESM
    // path (Vite handles it natively).
    "^d3-hierarchy$": "<rootDir>/node_modules/d3-hierarchy/dist/d3-hierarchy.js",
  },
  setupFilesAfterEnv: ["<rootDir>/src/setupTests.ts"],
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
    "^.+\\.tsx?$": [
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
  transformIgnorePatterns: [
    "/node_modules/(?!(@fluentui|axios)/)",
  ],
  globals: {},
};

export default config;
