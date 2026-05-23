import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach } from 'vitest';

// With Vitest `globals: false`, Testing Library can't auto-register its cleanup
// in a global afterEach, so mounted components would otherwise pile up across
// tests in a file (duplicate roles → "multiple elements found"). Unmount after
// each test explicitly.
afterEach(() => {
  cleanup();
});
