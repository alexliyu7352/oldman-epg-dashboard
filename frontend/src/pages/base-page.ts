import {
  createDashboardComponentLoaders,
  DashboardPage,
  type DashboardComponentLoaders,
  type DashboardPageOptions
} from "oldman-web/dashboard";
import { Component } from "oldman-web/core";
import { Sidebar } from "@app/components/sidebar";
import { Topbar } from "@app/components/topbar";

type BasePageConstructorOptions = Omit<DashboardPageOptions, "componentLoaders" | "layoutAttributes"> & {
  componentLoaders?: DashboardComponentLoaders;
  layoutAttributes?: Record<string, string>;
};

const DEFAULT_SIDEBAR_SIZE = "lg";

/**
 * 当前后台应用页面生命周期。Dashboard shell 来自 oldman-web/dashboard，业务组件通过懒加载覆盖。
 */
export class BasePage extends DashboardPage {
  private readonly applicationSidebarOptions: NonNullable<DashboardPageOptions["sidebarOptions"]>;
  private readonly applicationTopbarOptions: NonNullable<DashboardPageOptions["topbarOptions"]>;

  constructor(rootOrOptions: HTMLElement | BasePageConstructorOptions = document.documentElement) {
    const options = rootOrOptions instanceof HTMLElement ? { root: rootOrOptions } : rootOrOptions;
    const defaultSidebarSize = options.defaultSidebarSize
      ?? options.sidebarOptions?.defaultSidebarSize
      ?? options.layoutAttributes?.["data-sidebar-size"]
      ?? DEFAULT_SIDEBAR_SIZE;
    const sidebarOptions = {
      ...options.sidebarOptions,
      defaultSidebarSize
    };
    super({
      ...options,
      componentLoaders: createApplicationComponentLoaders(options.componentLoaders),
      defaultSidebarSize,
      sidebarOptions
    });
    this.applicationSidebarOptions = sidebarOptions;
    this.applicationTopbarOptions = options.topbarOptions ?? {};
  }

  protected override createSidebar(): Component {
    return new Sidebar(this.root, {
      ...this.applicationSidebarOptions,
      page: this,
      i18n: this.i18n
    });
  }

  protected override createTopbar(): Component {
    return new Topbar(this.root, {
      ...this.applicationTopbarOptions,
      page: this,
      i18n: this.i18n
    });
  }
}

function createApplicationComponentLoaders(overrides: DashboardComponentLoaders = {}): DashboardComponentLoaders {
  return createDashboardComponentLoaders({
    "language-switcher": async () => (await import("@app/components/language-switcher")).LanguageSwitcher,
    "dashboard-overview": async () => (await import("@app/components/dashboard-overview")).DashboardOverview,
    "notifications-center": async () => (await import("@app/components/notifications-center")).NotificationsCenter,
    ...overrides
  });
}
