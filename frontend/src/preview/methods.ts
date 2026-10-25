/** Render methods in Raycaster C++ order (index.html methodSelect). */

export const RENDER_METHODS: string[] = [
  'Linear',
  'Linear, Pre-Integrated',
  'Linear, Peak-Finding',
  'Linear + virtual linear Sampling',
  'Linear + virtual CR Sampling',
  'Linear + virtual Hermite Sampling',
  'Linear + virtual monotone Hermite Sampling',
  'Linear + lighting',
  'Linear, Peak-Finding + lighting',
  'Linear + lighting + virtual linear Sampling',
  'Linear + lighting + virtual CR Sampling',
  'Linear + lighting + virtual Hermite Sampling',
  'Linear + lighting + virtual monotone Hermite Sampling',
  'Quadratic B-Spline',
  'Quadratic B-Spline, Pre-Integrated',
  'Quadratic B-Spline, Peak-Finding',
  'Quadratic B-Spline + virtual linear Sampling',
  'Quadratic B-Spline + virtual CR Sampling',
  'Quadratic B-Spline + virtual Hermite Sampling',
  'Quadratic B-Spline + virtual monotone Hermite Sampling',
  'Quadratic B-Spline + lighting',
  'Quadratic B-Spline, Peak-Finding + lighting',
  'Quadratic B-Spline + lighting + virtual linear Sampling',
  'Quadratic B-Spline + lighting + virtual CR Sampling',
  'Quadratic B-Spline + lighting + virtual Hermite Sampling',
  'Quadratic B-Spline + lighting + virtual monotone Hermite Sampling',
  'Quadratic B-Spline, Prefiltered',
  'Quadratic B-Spline, Pre-Integrated, Prefiltered',
  'Quadratic B-Spline, Peak-Finding, Prefiltered',
  'Quadratic B-Spline + virtual linear Sampling, Prefiltered',
  'Quadratic B-Spline + virtual CR Sampling, Prefiltered',
  'Quadratic B-Spline + virtual Hermite Sampling, Prefiltered',
  'Quadratic B-Spline + virtual monotone Hermite Sampling, Prefiltered',
  'Quadratic B-Spline + lighting, Prefiltered',
  'Quadratic B-Spline, Peak-Finding + lighting, Prefiltered',
  'Quadratic B-Spline + lighting + virtual linear Sampling, Prefiltered',
  'Quadratic B-Spline + lighting + virtual CR Sampling, Prefiltered',
  'Quadratic B-Spline + lighting + virtual Hermite Sampling, Prefiltered',
  'Quadratic B-Spline + lighting + virtual monotone Hermite Sampling, Prefiltered',
  'Cubic B-Spline',
  'Cubic B-Spline, Pre-Integrated',
  'Cubic B-Spline, Peak-Finding',
  'Cubic B-Spline + virtual linear Samples',
  'Cubic B-Spline + virtual CR Samples',
  'Cubic B-Spline + virtual Hermite Samples',
  'Cubic B-Spline + virtual monotone Hermite Samples',
  'Cubic B-Spline + lighting',
  'Cubic B-Spline, Peak-Finding + lighting',
  'Cubic B-Spline + lighting + virtual linear Samples',
  'Cubic B-Spline + lighting + virtual CR Samples',
  'Cubic B-Spline + lighting + virtual Hermite Samples',
  'Cubic B-Spline + lighting + virtual monotone Hermite Samples',
  'Cubic B-Spline, Prefiltered',
  'Cubic B-Spline, Pre-Integrated, Prefiltered',
  'Cubic B-Spline, Peak-Finding, Prefiltered',
  'Cubic B-Spline + virtual linear Samples, Prefiltered',
  'Cubic B-Spline + virtual CR Samples, Prefiltered',
  'Cubic B-Spline + virtual Hermite Samples, Prefiltered',
  'Cubic B-Spline + virtual monotone Hermite Samples, Prefiltered',
  'Cubic B-Spline + lighting, Prefiltered',
  'Cubic B-Spline, Peak-Finding + lighting, Prefiltered',
  'Cubic B-Spline + lighting + virtual linear Samples, Prefiltered',
  'Cubic B-Spline + lighting + virtual CR Samples, Prefiltered',
  'Cubic B-Spline + lighting + virtual Hermite Samples, Prefiltered',
  'Cubic B-Spline + lighting + virtual monotone Hermite Samples, Prefiltered',
  'Catmull-Rom',
  'Catmull-Rom, Pre-Integrated',
  'Catmull-Rom, Peak-Finding',
  'Catmull-Rom + virtual linear Samples',
  'Catmull-Rom + virtual CR Samples',
  'Catmull-Rom + virtual Hermite Samples',
  'Catmull-Rom + virtual monotone Hermite Samples',
  'Catmull-Rom + lighting',
  'Catmull-Rom, Peak-Finding + lighting',
  'Catmull-Rom + lighting + virtual linear Samples',
  'Catmull-Rom + lighting + virtual CR Samples',
  'Catmull-Rom + lighting + virtual Hermite Samples',
  'Catmull-Rom + lighting + virtual monotone Hermite Samples',
  'Analytic',
  'Analytic + lighting',
]

export function isVirtualMethod(index: number): boolean {
  const name = RENDER_METHODS[index] ?? ''
  return name.toLowerCase().includes('virtual')
}

/** Sampling-rate exponents shown in the reference page (-5..6 → 2^k). */
export const SAMPLING_RATE_EXPS: number[] = [-5, -4, -3, -2, -1, 0, 1, 2, 3, 4, 5, 6]

export function samplingRateFromExp(exp: number): number {
  return Math.pow(2, exp)
}

export function samplingExpFromRate(rate: number): number {
  if (!Number.isFinite(rate) || rate <= 0) return 0
  const exp = Math.round(Math.log2(rate))
  if (exp < -5) return -5
  if (exp > 6) return 6
  return exp
}

export function formatSamplingRate(exp: number): string {
  const v = samplingRateFromExp(exp)
  if (exp >= 0) return String(v)
  return v.toFixed(6).replace(/0+$/, '').replace(/\.$/, '')
}
