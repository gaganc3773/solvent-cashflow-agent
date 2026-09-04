/** INR-aware formatters for the UI. Inputs are in INR (rupees), not paise,
 *  unless the function name says otherwise. */

export function fmtINRShort(n: number): string {
  const abs = Math.abs(n)
  const sign = n < 0 ? '−' : ''
  if (abs >= 1e7) return sign + '₹' + (abs / 1e7).toFixed(2) + 'Cr'
  if (abs >= 1e5) return sign + '₹' + (abs / 1e5).toFixed(2) + 'L'
  if (abs >= 1e3) return sign + '₹' + (abs / 1e3).toFixed(0) + 'K'
  return sign + '₹' + Math.round(abs).toLocaleString('en-IN')
}

export function fmtINRFull(n: number): string {
  const sign = n < 0 ? '−' : ''
  return sign + '₹' + Math.round(Math.abs(n)).toLocaleString('en-IN')
}

export function fmtINRPaiseFull(paise: number): string {
  return fmtINRFull(paise / 100)
}

export function fmtINRPaiseShort(paise: number): string {
  return fmtINRShort(paise / 100)
}

export function fmtUSD(n: number): string {
  return '$' + Math.round(n).toLocaleString('en-US')
}

export function fmtPct(n: number, digits = 1): string {
  const sign = n >= 0 ? '+' : '−'
  return sign + Math.abs(n * 100).toFixed(digits) + '%'
}

export function fmtRate(n: number): string {
  return n.toFixed(2)
}
