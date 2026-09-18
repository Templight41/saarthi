import { describe, expect, it } from 'vitest'
import { duration, metricDisplay, rupees } from './format'

describe('formatting', () => {
  it('formats rupees in the Indian numbering system', () => {
    expect(rupees('15000')).toContain('15,000')
    expect(rupees(null)).toBe('—')
  })

  it('formats durations the way the memory panel reads them', () => {
    expect(duration(6420)).toBe('1h 47m')
    expect(duration(45)).toBe('45s')
    expect(duration(null)).toBe('—')
  })

  it('renders a ratio as a percentage and a null as a dash', () => {
    expect(metricDisplay('ratio', 1)).toBe('100%')
    expect(metricDisplay('ratio', null)).toBe('—')
    expect(metricDisplay('hours', 3)).toBe('3.0h')
  })
})
