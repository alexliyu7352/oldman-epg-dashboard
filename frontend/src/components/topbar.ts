import { DashboardTopbar, type DashboardTopbarOptions } from "oldman-web/dashboard";

const EMPTY_NOTIFICATION_ASSET = new URL("../theme/assets/images/svg/bell.svg", import.meta.url).href;

/**
 * 当前应用顶栏只补充业务主题空通知占位；通用行为由 oldman-web/dashboard 提供。
 */
export class Topbar extends DashboardTopbar {
  constructor(root: HTMLElement, options: DashboardTopbarOptions = {}) {
    super(root, {
      ...options,
      defaultNotificationHref: options.defaultNotificationHref ?? "/dashboard"
    });
  }

  protected override emptyNotificationTemplate(): string {
    return `
      <div class="empty-notification-elem px-6 py-8 text-center">
        <img src="${EMPTY_NOTIFICATION_ASSET}" class="mx-auto h-16 w-16" alt="">
        <p class="mt-3 text-sm font-medium text-default-700">${this.i18n.t("Hey! You have no any notifications")}</p>
      </div>
    `;
  }
}
