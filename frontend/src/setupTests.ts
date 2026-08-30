import "@testing-library/jest-dom";
import { configure } from "@testing-library/react";
import { TextEncoder, TextDecoder } from "util";

// Give async queries a little more headroom than the 1s default: Fluent modal
// dialogs (tabster modalizer + Textarea) can take longer to mount under load.
configure({ asyncUtilTimeout: 5000 });

// jsdom omits TextEncoder/TextDecoder, which react-router references at
// import time. Node's util provides spec-compatible implementations.
global.TextEncoder = TextEncoder;
global.TextDecoder = TextDecoder as typeof global.TextDecoder;

// Set Vite-equivalent env vars for tests (the AST transformer rewrites
// import.meta.env.X → process.env.X, so these must exist as process.env).
process.env.VITE_API_URL = "http://localhost:8000/api";
process.env.MODE = "test";
process.env.DEV = "true";
process.env.PROD = "false";

// Mock window.matchMedia for Fluent UI components
Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: jest.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: jest.fn(),
    removeListener: jest.fn(),
    addEventListener: jest.fn(),
    removeEventListener: jest.fn(),
    dispatchEvent: jest.fn(),
  })),
});

// Mock ResizeObserver for Fluent UI components
global.ResizeObserver = jest.fn().mockImplementation(() => ({
  observe: jest.fn(),
  unobserve: jest.fn(),
  disconnect: jest.fn(),
}));

// Mock IntersectionObserver
global.IntersectionObserver = jest.fn().mockImplementation(() => ({
  observe: jest.fn(),
  unobserve: jest.fn(),
  disconnect: jest.fn(),
}));

// Mock scrollTo and scrollIntoView
Element.prototype.scrollTo = jest.fn();
Element.prototype.scrollIntoView = jest.fn();

// Mock URL.createObjectURL and URL.revokeObjectURL for file handling
global.URL.createObjectURL = jest.fn(() => "blob:mock-url");
global.URL.revokeObjectURL = jest.fn();
