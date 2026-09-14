export interface LanguageDefinition {
  readonly code: string;
  readonly locale: string;
  readonly aliases: readonly string[];
  readonly flag: string;
  readonly flagUrl?: string;
  readonly catalogPath: string;
  readonly name: string;
}

export const defaultLanguage = "en";

export const languageDefinitions = [
  {
    code: "en",
    locale: "en",
    aliases: ["en-US"],
    flag: "us",
    flagUrl: "/static/oldman/images/flags/us.svg",
    catalogPath: "i18n/en.json",
    name: "English",
  },
  {
    code: "zh-Hans",
    locale: "zh-Hans",
    aliases: ["zh-CN", "zh-SG"],
    flag: "cn",
    flagUrl: "/static/oldman/images/flags/cn.svg",
    catalogPath: "i18n/zh-hans.json",
    name: "简体中文",
  },
  {
    code: "zh-Hant",
    locale: "zh-Hant",
    aliases: ["zh-TW", "zh-HK"],
    flag: "tw",
    flagUrl: "/static/oldman/images/flags/tw.svg",
    catalogPath: "i18n/zh-hant.json",
    name: "繁體中文",
  },
] as const satisfies readonly LanguageDefinition[];

export const supportedLanguageCodes = languageDefinitions.map((language) => language.code);

export const languageAliases: Record<string, string> = {
  "en": "en",
  "en-US": "en",
  "en-us": "en",
  "zh-CN": "zh-Hans",
  "zh-HK": "zh-Hant",
  "zh-Hans": "zh-Hans",
  "zh-Hant": "zh-Hant",
  "zh-SG": "zh-Hans",
  "zh-TW": "zh-Hant",
  "zh-cn": "zh-Hans",
  "zh-hans": "zh-Hans",
  "zh-hant": "zh-Hant",
  "zh-hk": "zh-Hant",
  "zh-sg": "zh-Hans",
  "zh-tw": "zh-Hant",
};

export const localeToLanguageCode: Record<string, string> = {
  "en": "en",
  "zh-Hans": "zh-Hans",
  "zh-hans": "zh-Hans",
  "zh-Hant": "zh-Hant",
  "zh-hant": "zh-Hant",
};
