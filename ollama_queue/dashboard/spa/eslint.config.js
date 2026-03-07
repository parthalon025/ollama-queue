import js from "@eslint/js";
import globals from "globals";
import prettierConfig from "eslint-config-prettier";

/**
 * ESLint config for ollama-queue dashboard SPA (Preact + esbuild).
 * JSX parsed natively via ecmaFeatures.jsx = true.
 * @type {import('eslint').Linter.Config[]}
 */
export default [
  js.configs.recommended,
  prettierConfig,
  {
    files: ["src/**/*.{js,jsx}"],
    languageOptions: {
      globals: {
        ...globals.browser,
        h: "readonly", // Preact JSX factory (imported from preact)
      },
      parserOptions: {
        ecmaFeatures: { jsx: true },
      },
    },
    rules: {
      // h is imported for JSX in some files but used implicitly by esbuild's JSX transform
      "no-unused-vars": ["warn", { argsIgnorePattern: "^_", varsIgnorePattern: "^_|^h$" }],
      "no-console": "off",
      "prefer-const": "error",
      "no-var": "error",
      // eqeqeq: warn only — existing codebase uses == in some places; enforce in new code
      eqeqeq: ["warn", "always"],
    },
  },
  {
    // Jest test files: expose describe/it/expect/beforeEach etc. as globals
    files: ["src/**/*.test.{js,jsx}", "src/**/*.spec.{js,jsx}"],
    languageOptions: {
      globals: { ...globals.jest },
    },
  },
  {
    files: ["esbuild.config.mjs", "scripts/**/*.js"],
    languageOptions: {
      globals: { ...globals.node },
    },
    rules: {
      "no-unused-vars": ["warn", { argsIgnorePattern: "^_" }],
      "prefer-const": "error",
    },
  },
  {
    ignores: ["node_modules/", "dist/"],
  },
];
