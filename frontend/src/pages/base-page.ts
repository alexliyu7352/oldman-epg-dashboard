import {
  createDashboardComponentLoaders,
  DashboardPage,
  DashboardSidebar,
  DashboardTopbar,
  type DashboardComponentLoaders,
  type DashboardPageOptions
} from "oldman-web/dashboard";
import { Component } from "oldman-web/core";

type BasePageConstructorOptions = Omit<DashboardPageOptions, "componentLoaders" | "layoutAttributes"> & {
  componentLoaders?: DashboardComponentLoaders;
  layoutAttributes?: Record<string, string>;
};

const DEFAULT_SIDEBAR_SIZE = "lg";
const DASHBOARD_PATH = "/dashboard";
const EMPTY_NOTIFICATION_ASSET = new URL("../theme/assets/images/svg/bell.svg", import.meta.url).href;

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
    return new DashboardSidebar(this.root, {
      defaultDashboardPath: DASHBOARD_PATH,
      ...this.applicationSidebarOptions,
      page: this,
      i18n: this.i18n
    });
  }

  protected override createTopbar(): Component {
    return new DashboardTopbar(this.root, {
      defaultNotificationHref: DASHBOARD_PATH,
      // 本站主题自带的空状态插画，其余顶栏行为都来自 oldman-web/dashboard。
      emptyNotificationTemplate: () => `
        <div class="empty-notification-elem px-6 py-8 text-center">
          <img src="${EMPTY_NOTIFICATION_ASSET}" class="mx-auto h-16 w-16" alt="">
          <p class="mt-3 text-sm font-medium text-default-700">${this.i18n.t("Hey! You have no any notifications")}</p>
        </div>
      `,
      ...this.applicationTopbarOptions,
      page: this,
      i18n: this.i18n
    });
  }
}

function createApplicationComponentLoaders(overrides: DashboardComponentLoaders = {}): DashboardComponentLoaders {
  return createDashboardComponentLoaders({
    "language-switcher": async () => (await import("oldman-web/components/language-switcher")).LanguageSwitcher,
    "dashboard-overview": async () => (await import("@app/components/dashboard-overview")).DashboardOverview,
    "notifications-center": async () => (await import("@app/components/notifications-center")).NotificationsCenter,
    popover: async () => (await import("oldman-web/components/popover")).Popover,
    tooltip: async () => (await import("oldman-web/components/tooltip")).Tooltip,
    ...overrides
  });
}
