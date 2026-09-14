import { DashboardSidebar, type DashboardSidebarOptions } from "oldman-web/dashboard";

export type SidebarOptions = DashboardSidebarOptions;

/** EPG route adapter; shared sidebar behavior remains owned by oldman-web/dashboard. */
export class Sidebar extends DashboardSidebar {
  constructor(root: HTMLElement, options: SidebarOptions = {}) {
    super(root, {
      ...options,
      defaultDashboardPath: options.defaultDashboardPath ?? "/dashboard"
    });
  }
}
