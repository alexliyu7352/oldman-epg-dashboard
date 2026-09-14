import { OldmanFeedback } from "@app/components/feedback";
import { Component } from "oldman-web/core";

/**
 * 通知中心页面行为，只处理当前视图内的批量清除和反馈。
 */
export class NotificationsCenter extends Component {
  static readonly componentName = "notifications-center";
  private feedbackInstance: OldmanFeedback | null = null;

  /**
   * 绑定批量清除按钮，保留 table 组件负责筛选、排序和分页。
   */
  override async mount(): Promise<void> {
    this.on("click", "[data-notifications-clear-selected]", (event) => {
      event.preventDefault();
      void this.clearSelectedRows();
    });
  }

  /**
   * 返回页面反馈组件实例，测试和运行时代码共用同一个入口。
   */
  feedback(): OldmanFeedback {
    if (this.feedbackInstance) return this.feedbackInstance;
    const target = this.root.querySelector<HTMLElement>("#notifications-feedback") ?? this.root;
    this.feedbackInstance = new OldmanFeedback(target, { page: this.page, i18n: this.i18n });
    return this.feedbackInstance;
  }

  /**
   * 删除当前表格中已选通知行，并通过统一 Feedback 显示结果。
   */
  private async clearSelectedRows(): Promise<void> {
    const selected = Array.from(this.root.querySelectorAll<HTMLInputElement>("[data-om-table-select-row]:checked"));
    if (selected.length === 0) {
      await this.feedback().alert({
        icon: "info",
        text: this.i18n.t("Select at least one notification before clearing."),
        title: this.i18n.t("No notifications selected")
      });
      return;
    }

    for (const input of selected) {
      input.closest("tr")?.remove();
    }
    this.syncEmptyState();
    await this.feedback().toast({
      icon: "success",
      text: this.i18n.t("Selected notifications were removed from the current view."),
      title: this.i18n.t("Notifications cleared")
    });
  }

  /**
   * 当前页表格行被清空时显示页面内空状态提示。
   */
  private syncEmptyState(): void {
    const empty = this.root.querySelector<HTMLElement>("[data-notifications-empty]");
    if (!empty) return;
    empty.hidden = this.root.querySelectorAll("tbody tr").length > 0;
  }
}
