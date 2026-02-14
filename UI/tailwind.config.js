/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        'aiva-dark': '#3F4E3E',
        'aiva-darker': '#2A3629',
        'aiva-cream': '#F5F2EA',
        'aiva-sage': '#EBF3E8',
        'aiva-input': '#F3EAD8',
        'aiva-card': '#FAF8F4',
        'aiva-border': '#D8CDBF',
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'Segoe UI', 'sans-serif'],
        display: ['LEMONMILK', 'system-ui', 'sans-serif'],
      }
    },
  },
  plugins: [],
}
