import '@testing-library/jest-dom/vitest'

// jsdom has no media stack: HTMLMediaElement.play() throws "Not implemented",
// which would fail any component test that renders a playable message.
Object.defineProperty(HTMLMediaElement.prototype, 'play', {
  configurable: true,
  value: () => Promise.resolve(),
})
Object.defineProperty(HTMLMediaElement.prototype, 'pause', {
  configurable: true,
  value: () => {},
})
