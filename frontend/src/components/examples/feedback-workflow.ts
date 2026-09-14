import { Component } from "oldman-web/core";
import { Feedback } from "oldman-web/components/feedback";

/** Confirm local user intent, then delegate the real request and results to Oldman. */
export class FeedbackWorkflow extends Component {
  override async mount(): Promise<void> {
    const form = this.root;
    const feedback = this.page?.feedback;
    if (!(form instanceof HTMLFormElement) || !(feedback instanceof Feedback)) {
      throw new Error("Feedback workflow requires a form and the Page's mounted Feedback");
    }
    // There is no plain submit path: both mutations require a dialog decision.
    this.listen(form, "submit", (event) => {
      event.preventDefault();
      event.stopPropagation();
    });
    this.on("click", "[data-feedback-operation]", async (event, element) => {
      event.preventDefault();
      if (!(element instanceof HTMLButtonElement) || element.disabled || !form.reportValidity()) return;
      const select = form.querySelector<HTMLSelectElement>("[name=project_id]");
      if (!select) return;
      const buttons = Array.from(form.querySelectorAll<HTMLButtonElement>("[data-feedback-operation]"));
      const operation = element.dataset.feedbackOperation;
      const option = select.selectedOptions[0];
      const projectName = option?.textContent?.trim() ?? "";
      buttons.forEach((button) => { button.disabled = true; });
      try {
        let name = "";
        if (operation === "review") {
          const confirmed = await feedback.confirm({
            title: this.i18n.t("Move this project to review?"),
            text: projectName,
            icon: "question",
            showCancelButton: true,
            confirmButtonText: this.i18n.t("Move to review"),
            cancelButtonText: this.i18n.t("Cancel")
          });
          if (!confirmed) return;
        } else {
          const result = await feedback.prompt<string>({
            title: this.i18n.t("Rename project"),
            inputValue: projectName,
            inputLabel: this.i18n.t("Project name"),
            inputValidator: (value) => value.trim().length < 1 || value.trim().length > 150
              ? this.i18n.t("Enter a project name between 1 and 150 characters.") : undefined,
            confirmButtonText: this.i18n.t("Save name"),
            cancelButtonText: this.i18n.t("Cancel")
          });
          if (!result.isConfirmed) return;
          name = result.value ?? "";
        }
        if (this.signal.aborted) return;
        const response = await this.preloader.withLoading(() => this.runAction(form, {
          params: { operation: operation ?? "", name },
          signal: this.signal
        }));
        if (!this.signal.aborted && response?.error_code === 0 && option
          && String(response.data.id) === option.value && typeof response.data.name === "string") {
          option.textContent = response.data.name;
        }
      } catch (error) {
        if (this.signal.aborted) return;
        this.logger.error("Feedback workflow failed", error);
        await feedback.alert({ title: this.i18n.t("Request failed"), icon: "error" });
      } finally {
        buttons.forEach((button) => { button.disabled = false; });
      }
    });
  }
}
