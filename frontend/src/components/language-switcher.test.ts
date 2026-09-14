import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createI18n, createOldmanContext, resetOldmanContext, setOldmanContext } from "oldman-web/core";
import { LanguageSwitcher } from "./language-switcher";

describe("LanguageSwitcher", () => {
  beforeEach(() => {
    document.documentElement.lang = "en";
    document.body.innerHTML = `
      <div data-om-component="language-switcher" data-om-language-sync="false">
        <img data-om-language-current-flag />
        <button type="button" class="language" data-lang="zh-Hans" aria-pressed="false"></button>
        <button type="button" class="language active" data-lang="en" aria-pressed="true"></button>
      </div>
      <span data-key="menu"></span>
    `;
    localStorage.clear();
    document.cookie = "preferred_language=; path=/; max-age=0";
    document.cookie = "lang=; path=/; max-age=0";
    const i18n = createI18n({
      defaultLanguage: "en",
      languages: [
        {
          code: "en",
          locale: "en",
          aliases: [],
          flag: "us",
          flagUrl: "/static/oldman/images/flags/us.svg",
          catalogPath: "i18n/en.json",
          name: "English"
        },
        {
          code: "zh-Hans",
          locale: "zh-Hans",
          aliases: ["zh-CN", "zh-SG"],
          flag: "cn",
          flagUrl: "/static/oldman/images/flags/cn.svg",
          catalogPath: "i18n/zh-hans.json",
          name: "简体中文"
        }
      ],
      aliases: { en: "en", "zh-Hans": "zh-Hans", "zh-hans": "zh-Hans", "zh-CN": "zh-Hans" },
      document,
      catalogLoader: async (language) => ({
        locale: language.locale,
        messages: language.code === "zh-Hans" ? { menu: "菜单" } : {}
      })
    });
    setOldmanContext(createOldmanContext({ assetBaseUrl: "http://localhost:5173/", document, i18n }));
    vi.spyOn(console, "warn").mockImplementation(() => undefined);
  });

  afterEach(() => {
    resetOldmanContext();
    vi.restoreAllMocks();
    document.body.replaceChildren();
  });

  it("switches language through the global i18n runtime without navigation", async () => {
    const root = document.querySelector<HTMLElement>('[data-om-component="language-switcher"]')!;
    const switcher = new LanguageSwitcher(root);

    await switcher.start();
    const event = new Event("click", { bubbles: true, cancelable: true });
    const link = document.querySelector<HTMLElement>('[data-lang="zh-Hans"]')!;
    const defaultWasAllowed = link.dispatchEvent(event);
    await waitForText("[data-key='menu']", "菜单");
    await waitForFlag("cn.svg");

    expect(defaultWasAllowed).toBe(false);
    expect(event.defaultPrevented).toBe(true);
    expect(document.querySelector<HTMLImageElement>("[data-om-language-current-flag]")?.src).toContain("cn.svg");
    expect(document.querySelector<HTMLElement>('[data-lang="zh-Hans"]')?.classList.contains("active")).toBe(true);
    expect(document.querySelector<HTMLElement>('[data-lang="zh-Hans"]')?.getAttribute("aria-pressed")).toBe("true");
    expect(localStorage.getItem("preferred_language")).toBe("zh-Hans");
    expect(document.cookie).toContain("preferred_language=zh-Hans");
    expect(document.cookie).toContain("lang=zh-Hans");

    await switcher.stop();
  });
});

async function waitForText(selector: string, expected: string): Promise<void> {
  for (let attempt = 0; attempt < 10; attempt += 1) {
    if (document.querySelector(selector)?.textContent === expected) return;
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
  throw new Error(`Timed out waiting for ${selector} to be ${expected}`);
}

async function waitForFlag(expected: string): Promise<void> {
  for (let attempt = 0; attempt < 10; attempt += 1) {
    if (document.querySelector<HTMLImageElement>("[data-om-language-current-flag]")?.src.includes(expected)) return;
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
  throw new Error(`Timed out waiting for language flag ${expected}`);
}
