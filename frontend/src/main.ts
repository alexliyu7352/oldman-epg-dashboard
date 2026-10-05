import "@app/css/app.css";
import { BasePage } from "@app/pages/base-page";
import { startDashboard } from "oldman-web/dashboard";
import { defaultLanguage, languageAliases, languageDefinitions, languagePreferencePath } from "./i18n/generated";

// The sign-in and password reset pages are the framework's: startDashboard mounts its AuthPage on them.
const pageEntries = import.meta.glob([
  "./pages/backend.ts",
  "./pages/examples.ts",
  "!./pages/**/*.test.ts"
]);

void startDashboard({
  i18n: {
    aliases: languageAliases,
    defaultLanguage,
    languagePreferencePath,
    languages: languageDefinitions
  },
  // Unknown entry names (a typo, a page without its own pages/<name>.ts) still get the dashboard base page.
  fallbackPage: BasePage,
  pageLoader: async (pageName) => {
    const loader = pageEntries[`./pages/${pageName}.ts`];
    if (loader) await loader();
  }
}).catch((error: unknown) => {
  console.error("Dashboard startup failed", error);
});
