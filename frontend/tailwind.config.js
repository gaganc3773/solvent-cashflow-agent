/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        ground: {
          light: '#f4f2ec',
          DEFAULT: '#0e1013',
        },
        panel: {
          light: '#ffffff',
          DEFAULT: '#161a1f',
          hi: '#1e232a',
        },
        line: {
          light: '#e2ddd0',
          DEFAULT: '#262b33',
          hi: '#3a404a',
        },
        ink: {
          light: '#1a1c20',
          DEFAULT: '#e8e6df',
          muted: '#7c828b',
          dim: '#4a4f57',
        },
        brass: {
          DEFAULT: '#e4a63c',
          hi: '#f5b74a',
          tint: '#3a2f16',
        },
        gain: '#5fbf85',
        loss: '#e56b6b',
        info: '#6b9de5',
      },
      fontFamily: {
        serif: ['Instrument Serif', 'Times New Roman', 'serif'],
        sans: ['IBM Plex Sans', '-apple-system', 'BlinkMacSystemFont', 'sans-serif'],
        mono: ['IBM Plex Mono', 'SF Mono', 'Consolas', 'monospace'],
      },
    },
  },
  plugins: [],
}
