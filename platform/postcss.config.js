// Tailwind v4 moved the PostCSS plugin out of the `tailwindcss` package into
// `@tailwindcss/postcss`. Naming `tailwindcss` here under v4 fails with an explicit
// "it moved" error rather than silently emitting no styles.
//
// autoprefixer is deliberately absent: v4 handles vendor prefixing itself, so keeping it
// would re-walk every stylesheet to no effect.
export default {
  plugins: {
    "@tailwindcss/postcss": {},
  },
};
