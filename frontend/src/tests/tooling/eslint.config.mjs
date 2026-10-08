import tseslint from "typescript-eslint";
import vue from "eslint-plugin-vue";
import vueParser from "vue-eslint-parser";

// Syntax + debugger baseline only; not a claim of comprehensive style linting.
export default [
  { ignores: ["dist/**", "node_modules/**", "playwright-report/**", "test-results/**"] },
  {
    files: ["**/*.{js,mjs,ts}"],
    languageOptions: { parser: tseslint.parser, ecmaVersion: "latest", sourceType: "module" },
    rules: { "no-debugger": "error" },
  },
  {
    files: ["**/*.vue"],
    plugins: { vue },
    languageOptions: {
      parser: vueParser,
      parserOptions: { parser: tseslint.parser, ecmaVersion: "latest", sourceType: "module" },
    },
    rules: { "no-debugger": "error", "vue/no-parsing-error": "error" },
  },
];
