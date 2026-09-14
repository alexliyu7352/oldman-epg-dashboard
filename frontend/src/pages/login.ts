import { Page, setupPage } from "oldman-web/core";
import { Dropdown } from "oldman-web/components/dropdown";
import { Form } from "oldman-web/components/form";
import { Preloader } from "oldman-web/components/preloader";
import { LanguageSwitcher } from "@app/components/language-switcher";

/**
 * 登录页面入口，挂载框架共享的表单与外壳组件。
 */
export class LoginPage extends Page {
  override async mount(): Promise<void> {
    this.components.register("dropdown", Dropdown);
    this.components.register("form", Form);
    this.components.register("language-switcher", LanguageSwitcher);
    this.components.register("preloader", Preloader);
    await this.components.mount(this.root);
  }

  override async beforeUnmount(): Promise<void> {
    await this.unmountComponents();
  }
}

setupPage("login", LoginPage);
