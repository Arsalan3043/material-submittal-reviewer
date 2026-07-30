import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
    // Vendored design-handoff prototype (design_handoff_clause_qc_review/README.md: "Not
    // part of the deliverable" — its own runtime, not our app code).
    "design_handoff_clause_qc_review/**",
  ]),
]);

export default eslintConfig;
