import { afterEach, describe, expect, it } from 'vitest'
import { gatewayOrigin, mcpHttpUrl } from '@/utils/gatewayUrl'

describe('gatewayOrigin', () => {
  const originalOrigin = location.origin

  afterEach(() => {
    Object.defineProperty(window, 'location', {
      value: { origin: originalOrigin },
      writable: true,
      configurable: true,
    })
  })

  function setOrigin(origin: string) {
    Object.defineProperty(window, 'location', {
      value: { origin },
      writable: true,
      configurable: true,
    })
  }

  it('returns current location.origin by default', () => {
    setOrigin('http://192.168.1.10:18080')
    expect(gatewayOrigin()).toBe('http://192.168.1.10:18080')
  })

  it('works on https', () => {
    setOrigin('https://gateway.example.com')
    expect(gatewayOrigin()).toBe('https://gateway.example.com')
  })

  it('works on custom port', () => {
    setOrigin('http://localhost:9999')
    expect(gatewayOrigin()).toBe('http://localhost:9999')
  })
})

describe('mcpHttpUrl', () => {
  const originalOrigin = location.origin

  afterEach(() => {
    Object.defineProperty(window, 'location', {
      value: { origin: originalOrigin },
      writable: true,
      configurable: true,
    })
  })

  function setOrigin(origin: string) {
    Object.defineProperty(window, 'location', {
      value: { origin },
      writable: true,
      configurable: true,
    })
  }

  it('appends /mcp to origin', () => {
    setOrigin('http://127.0.0.1:18080')
    expect(mcpHttpUrl()).toBe('http://127.0.0.1:18080/mcp')
  })

  it('preserves custom host/port', () => {
    setOrigin('http://192.168.1.5:9999')
    expect(mcpHttpUrl()).toBe('http://192.168.1.5:9999/mcp')
  })

  it('preserves https scheme', () => {
    setOrigin('https://gateway.example.com')
    expect(mcpHttpUrl()).toBe('https://gateway.example.com/mcp')
  })
})