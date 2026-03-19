import { cleanup } from '@testing-library/react'
import '@testing-library/jest-dom/vitest'
import { afterAll, afterEach, beforeAll, vi } from 'vitest'

const MockNotification = vi.fn()
MockNotification.permission = 'denied'
MockNotification.requestPermission = vi.fn().mockResolvedValue('denied')

const localStorageState = new Map()
const mockLocalStorage = {
  getItem: vi.fn((key) => localStorageState.get(key) ?? null),
  setItem: vi.fn((key, value) => {
    localStorageState.set(key, String(value))
  }),
  removeItem: vi.fn((key) => {
    localStorageState.delete(key)
  }),
  clear: vi.fn(() => {
    localStorageState.clear()
  }),
}

let playSpy

beforeAll(() => {
  vi.stubGlobal('Notification', MockNotification)
  vi.stubGlobal('localStorage', mockLocalStorage)
  Object.defineProperty(window, 'localStorage', {
    value: mockLocalStorage,
    configurable: true,
  })
  playSpy = vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue()
})

afterEach(() => {
  cleanup()
  window.localStorage.clear()
  vi.clearAllMocks()
})

afterAll(() => {
  playSpy?.mockRestore()
  vi.unstubAllGlobals()
})
