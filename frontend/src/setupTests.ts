import "@testing-library/jest-dom";
import { configure } from "@testing-library/react";
import { TextEncoder, TextDecoder } from "util";

// Give async data and rendering assertions headroom under parallel test load.
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

function isDisplayed(element: HTMLElement): boolean {
  if (!element.isConnected) {
    return false;
  }
  for (let ancestor: HTMLElement | null = element; ancestor; ancestor = ancestor.parentElement) {
    if (getComputedStyle(ancestor).display === "none") {
      return false;
    }
  }
  return true;
}

// JSDOM has no layout. Without these boxes, Tabster cannot focus dialog controls
// and can mark the focused dialog surface aria-hidden on its deferred update.
Object.defineProperty(HTMLElement.prototype, "offsetParent", {
  configurable: true,
  get(this: HTMLElement): Element | null {
    if (!isDisplayed(this) || this === document.body || getComputedStyle(this).position === "fixed") {
      return null;
    }
    return this.parentElement;
  },
});

document.body.getBoundingClientRect = (): DOMRect =>
  isDisplayed(document.body)
    ? new DOMRect(0, 0, window.innerWidth, window.innerHeight)
    : new DOMRect();

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
