import '@testing-library/jest-dom/vitest'
import { afterEach } from 'vitest'
import { cleanup } from '@testing-library/react'

// Not using vitest's `test.globals`, so RTL's automatic per-test cleanup
// (which relies on a global afterEach) needs to be wired up explicitly.
afterEach(() => {
  cleanup()
})
