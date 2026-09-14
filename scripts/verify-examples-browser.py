#!/usr/bin/env python3
"""Verify the Dashboard example shell in a real Chrome browser."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
SUPPORT_PATH = ROOT / "scripts" / "verify-dashboard-browser.py"
DEFAULT_URL = "http://localhost:17998/"
DEFAULT_USERNAME = "oldman_admin"
DEFAULT_PASSWORD = "oldman_admin_123"
SECONDARY_USERNAME = "oldman_examples_other"
SECONDARY_PASSWORD = "oldman_examples_other_123"


def load_browser_support() -> ModuleType:
    """Reuse the established CDP client and login helpers."""
    spec = importlib.util.spec_from_file_location(
        "oldman_dashboard_browser_support", SUPPORT_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load Dashboard browser support")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def ensure_secondary_user() -> None:
    """Create the second isolated browser identity through the existing helper."""
    config_file = os.environ.get("OLDMAN_GATE_CONFIG_FILE")
    if not config_file:
        raise RuntimeError("The examples gate requires OLDMAN_GATE_CONFIG_FILE")
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "create_admin.py"),
            "--username",
            SECONDARY_USERNAME,
            "--password",
            SECONDARY_PASSWORD,
            "--email",
            "oldman-examples-other@example.com",
            "--config",
            config_file,
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )


def assert_example_page(
    client, support: ModuleType, path: str, category: str, page: str, result
) -> None:
    """Check the route, Page entry and active two-level navigation."""
    payload = client.evaluate(
        f"""
(() => {{
  const failures = [];
  const shell = document.querySelector("[data-examples-shell]");
  const activeLink = document.querySelector('a[href="{path}"]');
  if (location.pathname !== "{path}") failures.push(`unexpected path: ${{location.pathname}}`);
  if (document.body.dataset.omPage !== "examples") failures.push("examples Page entry is not active");
  if (document.body.dataset.omExamplesReady !== "true") failures.push("ExamplesPage did not mount");
  if (!shell) failures.push("missing examples shell");
  if (shell?.dataset.exampleCategory !== "{category}") failures.push("wrong example category");
  if (shell?.dataset.examplePage !== "{page}") failures.push("wrong example page");
  if (!activeLink?.classList.contains("active")) failures.push("sidebar link is not active");
  if (document.querySelector(".sub-menu .sub-menu")) failures.push("examples navigation has a third level");
  return {{ failures }};
}})()
""",
        timeout=5.0,
    )
    result.pageErrors.extend(
        f"{path}: {failure}" for failure in support.assertion_failures(payload)
    )


def assert_all_example_routes(client, support: ModuleType, result) -> None:
    """Fetch every sidebar example route with the real authenticated browser session."""
    payload = client.evaluate(
        r"""
(async () => {
  const failures = [];
  const paths = [...new Set(
    [...document.querySelectorAll('a[href^="/examples/"]')]
      .map((link) => new URL(link.href, location.href).pathname)
  )];
  for (const path of paths) {
    const response = await fetch(path, { headers: { Accept: "text/html" } });
    const body = await response.text();
    if (response.status !== 200) failures.push(`${path} returned ${response.status}`);
    if (!body.includes("data-examples-shell")) failures.push(`${path} did not render the examples shell`);
  }
  return { failures, routeCount: paths.length };
})()
""",
        timeout=60.0,
    )
    failures = support.assertion_failures(payload)
    if not payload.get("routeCount"):
        failures.append("no example routes were discovered in the sidebar")
    result.pageErrors.extend(
        f"example route inventory: {failure}" for failure in failures
    )


def assert_json_list_form(client, support: ModuleType, base_url: str, result) -> None:
    """Exercise the real Text-backed JSON list through the mounted Form component."""
    payload = client.evaluate(
        r"""
(async () => {
  const failures = [];
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const createForm = document.querySelector("[data-example-json-list-create] form[data-om-form]");
  if (!createForm) return { failures: ["missing JSON list create Form"] };
  const createName = createForm.querySelector("[name='create-name']");
  const createStatus = createForm.querySelector("[name='create-status']");
  const createSource = createForm.querySelector("[name='create-sources_json-0']");
  if (!createName || !createStatus || !createSource) return { failures: ["JSON list create fields are missing"] };
  createName.value = `Browser Gate Stream Profile ${Date.now()}`;
  createStatus.value = "active";
  createSource.value = "https://browser-gate.example.test/create.m3u8";
  createName.dispatchEvent(new Event("input", { bubbles: true }));
  createStatus.dispatchEvent(new Event("change", { bubbles: true }));
  createSource.dispatchEvent(new Event("input", { bubbles: true }));
  createForm.requestSubmit();
  for (let index = 0; index < 70 && !createForm.querySelector("[data-example-form-success]"); index += 1) await sleep(100);
  if (!createForm.querySelector("[data-example-form-success]")) failures.push("HTML Form did not create a real stream profile");

  const container = document.querySelector("#json-stream-profile-form");
  const form = container?.querySelector("form[data-om-form-mode='json']");
  const repeater = form?.querySelector("[data-om-component='form-repeater']");
  for (let index = 0; index < 50 && repeater?.dataset.omComponentState !== "mounted"; index += 1) await sleep(100);
  if (!form || !repeater) return { failures: ["missing JSON list edit Form"] };
  if (repeater.dataset.omComponentState !== "mounted") failures.push("form repeater did not mount");

  const rows = () => Array.from(repeater.querySelectorAll("[data-om-repeater-rows] > [data-om-repeater-row]"));
  const inputs = () => rows().map((row) => row.querySelector("input")).filter(Boolean);
  const original = inputs().map((input) => input.value);
  if (original.length < 2) failures.push(`fixture supplied ${original.length} JSON list rows`);
  if (failures.length) return { failures };

  repeater.querySelector("[data-om-repeater-add]")?.click();
  for (let index = 0; index < 30 && rows().length !== original.length + 1; index += 1) await sleep(50);
  if (rows().length !== original.length + 1) return { failures: [...failures, "add did not create a repeater row"] };
  let currentInputs = inputs();
  const addedValue = "https://browser-gate.example.test/live.m3u8";
  currentInputs.at(-1).value = addedValue;
  currentInputs.at(-1).dispatchEvent(new Event("input", { bubbles: true }));
  rows().at(-1).querySelector("[data-om-repeater-up]")?.click();
  await sleep(100);
  currentInputs = inputs();
  if (currentInputs.at(-2)?.value !== addedValue) failures.push("up did not reorder the added JSON row");
  if (currentInputs.some((input, index) => input.name !== `edit-sources_json-${index}`)) failures.push("reordered rows were not reindexed");

  currentInputs.at(-2).value = currentInputs[0].value;
  currentInputs.at(-2).dispatchEvent(new Event("input", { bubbles: true }));
  form.requestSubmit();
  const message = form.querySelector("[data-om-form-message]");
  for (let index = 0; index < 60 && (message?.hidden || !message?.textContent?.trim()); index += 1) await sleep(100);
  if (message?.hidden || !message?.textContent?.trim() || form.dataset.omStatus !== "error") failures.push("duplicate sources did not render the top Form message");

  currentInputs = inputs();
  currentInputs.at(-2).value = addedValue;
  currentInputs.at(-2).dispatchEvent(new Event("input", { bubbles: true }));
  rows().at(-1).querySelector("[data-om-repeater-remove]")?.click();
  for (let index = 0; index < 30 && rows().length !== original.length; index += 1) await sleep(50);
  if (rows().length !== original.length) failures.push("remove did not delete a repeater row");
  const savedValues = inputs().map((input) => input.value);
  form.requestSubmit();
  for (let index = 0; index < 80 && container.querySelector("form") === form; index += 1) await sleep(100);
  const replacement = container.querySelector("form");
  if (!replacement || replacement === form) failures.push("successful JSON submit did not replace the Form");
  const replacementValues = Array.from(replacement?.querySelectorAll("[data-om-repeater-rows] input") || []).map((input) => input.value);
  if (JSON.stringify(replacementValues) !== JSON.stringify(savedValues)) failures.push("replacement Form did not contain the saved order");
  return { failures, savedValues };
})()
""",
        timeout=30.0,
    )
    failures = support.assertion_failures(payload)
    result.pageErrors.extend(f"JSON list Form: {failure}" for failure in failures)
    if failures:
        return

    saved_values = payload.get("savedValues")
    support.navigate(
        client, urllib.parse.urljoin(base_url, "/examples/forms/json-list")
    )
    persisted = client.evaluate(
        """
(() => ({
  failures: [],
  values: Array.from(document.querySelectorAll("#json-stream-profile-form [data-om-repeater-rows] input")).map((input) => input.value)
}))()
""",
        timeout=5.0,
    )
    if persisted.get("values") != saved_values:
        result.pageErrors.append(
            "JSON list Form: saved order did not survive a full page reload"
        )


def assert_remote_modal_form(client, support: ModuleType, result) -> None:
    """Prove an ordinary Form mounts and submits inside the shared remote Modal."""
    payload = client.evaluate(
        r"""
(async () => {
  const failures = [];
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  document.querySelector("[data-om-modal-target='#example-form-modal']")?.click();
  for (let index = 0; index < 60 && !document.querySelector("#example-form-modal:not([hidden]) form[data-om-form]"); index += 1) await sleep(100);
  const modal = document.querySelector("#example-form-modal:not([hidden])");
  const form = modal?.querySelector("form[data-om-form]");
  if (!modal || !form) return { failures: ["remote Modal Form did not open"] };
  if (form.dataset.omComponentState !== "mounted") failures.push("remote Modal Form did not mount as an ordinary Form");
  const name = form.querySelector("[name='modal-name']");
  const email = form.querySelector("[name='modal-email']");
  if (!name || !email) return { failures: [...failures, "remote Modal Form fields are missing"] };
  name.value = "Browser gate";
  email.value = "browser@example.test";
  name.dispatchEvent(new Event("input", { bubbles: true }));
  email.dispatchEvent(new Event("input", { bubbles: true }));
  form.requestSubmit();
  for (let index = 0; index < 70 && !modal.hidden; index += 1) await sleep(100);
  if (!modal.hidden) failures.push("successful remote Modal Form did not close the Modal");
  return { failures };
})()
""",
        timeout=20.0,
    )
    result.pageErrors.extend(
        f"remote Modal Form: {failure}"
        for failure in support.assertion_failures(payload)
    )


def assert_slug_form(client, support: ModuleType, base_url: str, result) -> None:
    """Exercise live Slug behavior, native errors and both response modes."""
    path = "/examples/forms/slug"
    support.navigate(client, urllib.parse.urljoin(base_url, path))
    assert_example_page(client, support, path, "forms", "slug", result)
    payload = client.evaluate(
        r"""
(async () => {
  const failures = [];
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const input = (form, name, value) => {
    const control = form.querySelector(`[name='${name}']`);
    if (!control) {
      failures.push(`missing ${name}`);
      return null;
    }
    control.value = value;
    control.dispatchEvent(new Event("input", { bubbles: true }));
    return control;
  };

  const htmlForm = document.querySelector("[data-example-form-card='html'] form");
  const jsonForm = document.querySelector("[data-example-form-card='json'] form");
  if (!htmlForm || !jsonForm) return { failures: ["HTML and JSON Slug Forms are required"] };
  const slugInputs = Array.from(document.querySelectorAll("[data-om-component='slug-input']"));
  for (let index = 0; index < 40 && slugInputs.some((field) => field.dataset.omComponentState !== "mounted"); index += 1) await sleep(50);
  if (slugInputs.length !== 4 || slugInputs.some((field) => field.dataset.omComponentState !== "mounted")) failures.push("Slug inputs did not mount in both Forms");

  jsonForm.requestSubmit();
  const invalidTitle = jsonForm.querySelector("[name='json-title']");
  const invalidError = jsonForm.querySelector("[data-om-error-for='json-title']");
  const invalidMessage = jsonForm.querySelector("[data-om-form-message]");
  if (jsonForm.dataset.omStatus !== "error") failures.push("native validation did not mark the Form as error");
  if (invalidTitle?.getAttribute("aria-invalid") !== "true") failures.push("native validation did not mark the first field invalid");
  if (!invalidError?.textContent?.trim() || invalidError.hidden) failures.push("native field error is not visible");
  if (!invalidMessage?.textContent?.trim() || invalidMessage.hidden) failures.push("native Form message is not visible");
  if (document.activeElement !== invalidTitle) failures.push("native validation did not focus the first invalid field");

  input(htmlForm, "html-title", "Browser Gate Release");
  input(htmlForm, "html-unicode_title", "中文 发布页面");
  const htmlSlug = htmlForm.querySelector("[name='html-slug']");
  const htmlUnicodeSlug = htmlForm.querySelector("[name='html-unicode_slug']");
  if (htmlSlug?.value !== "browser-gate-release") failures.push("ASCII slug did not update while typing");
  if (htmlUnicodeSlug?.value !== "中文-发布页面") failures.push("Unicode slug did not update while typing");
  input(htmlForm, "html-slug", "Manual Path!");
  input(htmlForm, "html-title", "Changed Title");
  if (htmlSlug?.value !== "manual-path") failures.push("manual slug was overwritten by a later title");
  input(htmlForm, "html-slug", "");
  if (htmlSlug?.value !== "changed-title") failures.push("clearing the slug did not restore automatic generation");
  htmlForm.requestSubmit();
  for (let index = 0; index < 70 && !document.querySelector("[data-example-form-card='html'] [data-example-form-success]"); index += 1) await sleep(100);
  const htmlResult = document.querySelector("[data-example-form-card='html'] [data-example-form-result='slug'] dd");
  if (htmlResult?.textContent?.trim() !== "changed-title") failures.push("HTML response did not show the final server slug");

  input(jsonForm, "json-title", "JSON Browser Release");
  input(jsonForm, "json-unicode_title", "中文 JSON 页面");
  jsonForm.requestSubmit();
  for (let index = 0; index < 70 && !jsonForm.querySelector("[data-example-form-success]"); index += 1) await sleep(100);
  const jsonResult = jsonForm.querySelector("[data-example-form-result='slug'] dd");
  if (jsonResult?.textContent?.trim() !== "json-browser-release") failures.push("JSON response did not show the final server slug");
  return { failures };
})()
""",
        timeout=20.0,
    )
    result.pageErrors.extend(
        f"Slug Form: {failure}"
        for failure in support.assertion_failures(payload)
    )


def assert_input_spinner_form(client, support: ModuleType, base_url: str, result) -> None:
    """Exercise integer, decimal and boundary behavior in both response modes."""
    path = "/examples/forms/input-spinner"
    support.navigate(client, urllib.parse.urljoin(base_url, path))
    assert_example_page(client, support, path, "forms", "input-spinner", result)
    payload = client.evaluate(
        r"""
(async () => {
  const failures = [];
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const htmlForm = document.querySelector("[data-example-form-card='html'] form");
  const jsonForm = document.querySelector("[data-example-form-card='json'] form");
  if (!htmlForm || !jsonForm) return { failures: ["HTML and JSON Input Spinner Forms are required"] };

  const spinners = Array.from(document.querySelectorAll("[data-om-component='input-spinner']"));
  for (let index = 0; index < 40 && spinners.some((spinner) => spinner.dataset.omComponentState !== "mounted"); index += 1) await sleep(50);
  if (spinners.length !== 4 || spinners.some((spinner) => spinner.dataset.omComponentState !== "mounted")) failures.push("Input Spinners did not mount in both Forms");

  const replicas = jsonForm.querySelector("[name='json-replicas']");
  replicas.value = "9";
  replicas.dispatchEvent(new Event("input", { bubbles: true }));
  const replicasIncrease = replicas.closest("[data-om-component='input-spinner']")?.querySelector("[data-om-input-spinner-increase]");
  replicasIncrease?.click();
  if (replicas.value !== "10" || !replicasIncrease?.disabled) failures.push("integer spinner did not stop at max=10");

  const buffer = jsonForm.querySelector("[name='json-buffer_seconds']");
  buffer.value = "0.5";
  buffer.dispatchEvent(new Event("input", { bubbles: true }));
  const bufferDecrease = buffer.closest("[data-om-component='input-spinner']")?.querySelector("[data-om-input-spinner-decrease]");
  bufferDecrease?.click();
  if (buffer.value !== "0" || !bufferDecrease?.disabled) failures.push("decimal spinner did not step by 0.5 or stop at min=0");

  const htmlReplicas = htmlForm.querySelector("[name='html-replicas']");
  const htmlBuffer = htmlForm.querySelector("[name='html-buffer_seconds']");
  htmlReplicas.value = "1";
  htmlBuffer.value = "2.5";
  htmlForm.requestSubmit();
  for (let index = 0; index < 70 && !htmlForm.querySelector("[data-example-form-success]"); index += 1) await sleep(100);
  if (htmlForm.querySelector("[data-example-form-result='replicas'] dd")?.textContent?.trim() !== "1") failures.push("HTML response did not show the final integer value");
  if (htmlForm.querySelector("[data-example-form-result='buffer_seconds'] dd")?.textContent?.trim() !== "2.5") failures.push("HTML response did not show the final decimal value");

  buffer.value = "30";
  buffer.dispatchEvent(new Event("input", { bubbles: true }));
  jsonForm.requestSubmit();
  for (let index = 0; index < 70 && !jsonForm.querySelector("[data-example-form-success]"); index += 1) await sleep(100);
  if (jsonForm.querySelector("[data-example-form-result='replicas'] dd")?.textContent?.trim() !== "10") failures.push("JSON response did not show the final integer value");
  if (Number(jsonForm.querySelector("[data-example-form-result='buffer_seconds'] dd")?.textContent?.trim()) !== 30) failures.push("JSON response did not show the final decimal value");
  return { failures };
})()
""",
        timeout=20.0,
    )
    result.pageErrors.extend(
        f"Input Spinner Form: {failure}"
        for failure in support.assertion_failures(payload)
    )


def assert_tags_form(client, support: ModuleType, base_url: str, result) -> None:
    """Exercise text, local-choice and remote-choice Tags in both Form modes."""
    path = "/examples/forms/tags"
    support.navigate(client, urllib.parse.urljoin(base_url, path))
    assert_example_page(client, support, path, "forms", "tags", result)
    payload = client.evaluate(
        r"""
(async () => {
  const failures = [];
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const waitFor = async (predicate, attempts = 60) => {
    for (let index = 0; index < attempts; index += 1) {
      if (predicate()) return true;
      await sleep(100);
    }
    return false;
  };
  const htmlForm = document.querySelector("[data-example-form-card='html'] form");
  const jsonForm = document.querySelector("[data-example-form-card='json'] form");
  if (!htmlForm || !jsonForm) return { failures: ["HTML and JSON Tags Forms are required"] };

  const textTags = Array.from(document.querySelectorAll("[data-om-component='tags-input']"));
  const selectTags = Array.from(document.querySelectorAll("[data-om-component='select']"));
  await waitFor(() => [...textTags, ...selectTags].every((field) => field.dataset.omComponentState === "mounted"));
  if (textTags.length !== 4 || textTags.some((field) => field.dataset.omComponentState !== "mounted")) failures.push("four text Tags inputs did not mount");
  if (selectTags.length !== 4 || selectTags.some((field) => field.dataset.omComponentState !== "mounted")) failures.push("four Select Tags inputs did not mount");

  const control = (form, name) => form.querySelector(`[name='${form === htmlForm ? "html" : "json"}-${name}']`);
  const choiceRoot = (field) => field.closest(".choices") || field.parentElement;
  const editor = (field) => choiceRoot(field)?.querySelector(".choices__input--cloned");
  const values = (field, delimiter) => field.value.split(delimiter).filter(Boolean);
  const visibleValues = (field) => Array.from(choiceRoot(field)?.querySelectorAll(".choices__list--multiple [data-item][data-value]") || []).map((item) => item.dataset.value);
  const dispatchKey = (field, value, key) => {
    const input = editor(field);
    if (!input) return false;
    input.value = value;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true }));
    return true;
  };
  const paste = (field, text) => {
    const input = editor(field);
    if (!input) return false;
    const event = new Event("paste", { bubbles: true, cancelable: true });
    Object.defineProperty(event, "clipboardData", { value: { getData: () => text } });
    input.dispatchEvent(event);
    return true;
  };
  const removeValue = (field, value) => {
    const item = Array.from(choiceRoot(field)?.querySelectorAll(".choices__list--multiple [data-item][data-value]") || []).find((candidate) => candidate.dataset.value === value);
    item?.querySelector(".choices__button")?.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true, button: 0 }));
    return Boolean(item);
  };
  const chooseValue = async (field, value) => {
    choiceRoot(field)?.querySelector(".choices__inner")?.click();
    await sleep(50);
    const item = Array.from(choiceRoot(field)?.querySelectorAll(".choices__item--choice[data-value]") || []).find((candidate) => candidate.dataset.value === value && candidate.getAttribute("aria-disabled") !== "true");
    item?.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, cancelable: true, button: 0 }));
    await sleep(50);
    return Boolean(item);
  };
  const resultValue = (card, name) => card.querySelector(`[data-example-form-result='${name}'] dd`)?.textContent?.trim() || "";

  for (const form of [htmlForm, jsonForm]) {
    const keywords = control(form, "keywords");
    const aliases = control(form, "aliases");
    const categories = control(form, "categories");
    if (JSON.stringify(visibleValues(keywords)) !== JSON.stringify(["python", "sanic", "redis"])) failures.push("default keyword labels are incomplete");
    if (JSON.stringify(visibleValues(aliases)) !== JSON.stringify(["api", "web"])) failures.push("default alias labels are incomplete");
    if (JSON.stringify(Array.from(categories.selectedOptions).map((option) => option.value)) !== JSON.stringify(["backend", "dashboard"])) failures.push("default local Select tags are incomplete");
  }

  const jsonKeywords = control(jsonForm, "keywords");
  for (const value of [...visibleValues(jsonKeywords)]) removeValue(jsonKeywords, value);
  jsonForm.requestSubmit();
  const jsonKeywordError = jsonForm.querySelector("[data-om-error-for='json-keywords']");
  const jsonMessage = jsonForm.querySelector("[data-om-form-message]");
  if (!jsonKeywordError?.textContent?.trim() || jsonKeywordError.hidden) failures.push("required Tags field error is not visible");
  if (!jsonMessage?.textContent?.trim() || jsonMessage.hidden) failures.push("required Tags Form message is not visible");
  if (jsonKeywords.getAttribute("aria-invalid") !== "true") failures.push("required Tags field is not marked invalid");
  if (![jsonKeywords, editor(jsonKeywords)].includes(document.activeElement)) failures.push("required Tags field did not receive focus");
  paste(jsonKeywords, "python,sanic,redis");

  const htmlKeywords = control(htmlForm, "keywords");
  const htmlAliases = control(htmlForm, "aliases");
  if (!dispatchKey(htmlKeywords, "chrome", "Enter")) failures.push("keyword editor is missing");
  dispatchKey(htmlKeywords, "comma-added", ",");
  paste(htmlKeywords, "pasted-one,pasted-two");
  const beforeDuplicate = values(htmlKeywords, ",").length;
  dispatchKey(htmlKeywords, "chrome", "Enter");
  if (values(htmlKeywords, ",").length !== beforeDuplicate) failures.push("exact duplicate keyword was accepted");
  dispatchKey(htmlKeywords, "Chrome", "Enter");
  if (!values(htmlKeywords, ",").includes("Chrome")) failures.push("case-distinct keyword was rejected");
  if (!removeValue(htmlKeywords, "pasted-one") || values(htmlKeywords, ",").includes("pasted-one")) failures.push("remove button did not delete a keyword");
  const lastKeyword = values(htmlKeywords, ",").at(-1);
  const keywordEditor = editor(htmlKeywords);
  keywordEditor.value = "";
  keywordEditor.dispatchEvent(new KeyboardEvent("keydown", { key: "Backspace", bubbles: true, cancelable: true }));
  if (lastKeyword && values(htmlKeywords, ",").includes(lastKeyword)) failures.push("empty-input Backspace did not remove the last keyword");
  keywordEditor.value = "blurred";
  keywordEditor.dispatchEvent(new Event("blur", { bubbles: true }));
  if (!values(htmlKeywords, ",").includes("blurred")) failures.push("blur did not commit a pending keyword");
  dispatchKey(htmlAliases, "edge", "|");
  if (!values(htmlAliases, "|").includes("edge")) failures.push("custom alias delimiter did not commit a tag");

  const htmlRemote = control(htmlForm, "tag_ids");
  await waitFor(() => htmlRemote.options.length >= 6);
  await chooseValue(htmlRemote, "1");
  await chooseValue(htmlRemote, "2");
  if (htmlRemote.selectedOptions.length !== 2) failures.push("HTML remote Select did not select two tags");
  const expectedHtmlKeywords = htmlKeywords.value;
  const expectedHtmlAliases = htmlAliases.value;
  const expectedHtmlIds = Array.from(htmlRemote.selectedOptions).map((option) => option.value);
  htmlForm.requestSubmit();
  await waitFor(() => Boolean(document.querySelector("[data-example-form-card='html'] [data-example-form-success]")), 80);
  const htmlCard = document.querySelector("[data-example-form-card='html']");
  if (resultValue(htmlCard, "keywords") !== expectedHtmlKeywords) failures.push("HTML response did not show canonical keywords");
  if (resultValue(htmlCard, "aliases") !== expectedHtmlAliases) failures.push("HTML response did not show canonical aliases");
  if (!expectedHtmlIds.every((id) => resultValue(htmlCard, "tag_ids").includes(id))) failures.push("HTML response did not show multiple database tag IDs");

  const jsonRemote = control(jsonForm, "tag_ids");
  await waitFor(() => jsonRemote.options.length >= 6);
  const remoteRoot = choiceRoot(jsonRemote);
  const remoteSearch = editor(jsonRemote);
  remoteSearch.value = "stream";
  remoteSearch.dispatchEvent(new Event("input", { bubbles: true }));
  await waitFor(() => Array.from(jsonRemote.options).some((option) => option.textContent === "Streaming"));
  await chooseValue(jsonRemote, "15");
  if (!removeValue(jsonRemote, "15") || Array.from(jsonRemote.selectedOptions).some((option) => option.value === "15")) failures.push("remote Select tag could not be removed");
  remoteSearch.value = "a";
  remoteSearch.dispatchEvent(new Event("input", { bubbles: true }));
  await waitFor(() => jsonRemote.options.length >= 6);
  const beforeMore = jsonRemote.options.length;
  const more = remoteRoot.querySelector("[data-om-select-load-more]");
  if (more && !more.hidden) {
    more.click();
    await waitFor(() => jsonRemote.options.length > beforeMore);
  }
  if (jsonRemote.options.length <= 6) failures.push("remote Select did not load another result page");
  await chooseValue(jsonRemote, "1");
  await chooseValue(jsonRemote, "2");
  removeValue(jsonRemote, "1");
  await chooseValue(jsonRemote, "3");
  const expectedJsonIds = Array.from(jsonRemote.selectedOptions).map((option) => option.value);
  if (JSON.stringify(expectedJsonIds) !== JSON.stringify(["2", "3"])) failures.push(`remote Select final IDs are ${JSON.stringify(expectedJsonIds)}`);

  const jsonEditor = editor(jsonKeywords);
  jsonEditor.value = "submit-pending";
  jsonEditor.dispatchEvent(new Event("input", { bubbles: true }));
  jsonForm.requestSubmit();
  const expectedJsonKeywords = jsonKeywords.value;
  if (!values(jsonKeywords, ",").includes("submit-pending")) failures.push("submit capture did not commit the pending keyword");
  await waitFor(() => Boolean(jsonForm.querySelector("[data-example-form-success]")), 80);
  if (resultValue(jsonForm, "keywords") !== expectedJsonKeywords) failures.push("JSON response did not show canonical keywords");
  if (!expectedJsonIds.every((id) => resultValue(jsonForm, "tag_ids").includes(id))) failures.push("JSON response did not show multiple database tag IDs");
  return { failures };
})()
""",
        timeout=45.0,
    )
    result.pageErrors.extend(
        f"Tags Form: {failure}"
        for failure in support.assertion_failures(payload)
    )


def assert_color_picker_form(client, support: ModuleType, base_url: str, result) -> None:
    """Exercise Pickr value sync, reset, JSON submit and remote Modal mounting."""
    path = "/examples/forms/color-picker"
    support.navigate(client, urllib.parse.urljoin(base_url, path))
    assert_example_page(client, support, path, "forms", "color-picker", result)
    payload = client.evaluate(
        r"""
(async () => {
  const failures = [];
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const form = document.querySelector("[data-example-form-card='json'] form");
  if (!form) return { failures: ["missing JSON Color Picker Form"] };
  const pickers = Array.from(form.querySelectorAll("[data-om-component='color-picker']"));
  for (let index = 0; index < 50 && pickers.some((picker) => picker.dataset.omComponentState !== "mounted"); index += 1) await sleep(100);
  if (pickers.length !== 2 || pickers.some((picker) => picker.dataset.omComponentState !== "mounted")) failures.push("Color Pickers did not mount");

  const picker = pickers[1];
  const native = picker?.querySelector("[data-om-color-picker-native]");
  const value = picker?.querySelector("[data-om-color-picker-value]");
  const trigger = picker?.querySelector("[data-om-color-picker-trigger]");
  if (!native?.hidden || value?.disabled || value?.name !== "json-overlay_color" || trigger?.hidden) failures.push("native fallback was not replaced by the enhanced control");
  trigger?.click();
  for (let index = 0; index < 30 && !document.querySelector(".pcr-app.visible"); index += 1) await sleep(50);
  const resultInput = document.querySelector(".pcr-app.visible .pcr-result");
  const save = document.querySelector(".pcr-app.visible .pcr-save");
  if (!resultInput || !save) failures.push("Pickr editor did not open");
  else {
    resultInput.value = "#22C55E80";
    resultInput.dispatchEvent(new Event("input", { bubbles: true }));
    save.click();
    if (value.value !== "#22c55e80" || native.value !== "#22c55e") failures.push("Pickr did not sync the alpha HEX value");
  }

  form.reset();
  await sleep(0);
  if (value?.value !== "#0f172acc") failures.push("Form reset did not restore the initial color");
  form.requestSubmit();
  for (let index = 0; index < 70 && !["success", "error"].includes(form.dataset.omStatus); index += 1) await sleep(100);
  if (form.dataset.omStatus !== "success") failures.push("JSON Color Picker Form did not submit");

  document.querySelector("[data-om-modal-url='/examples/forms/color-picker/modal']")?.click();
  for (let index = 0; index < 70 && !document.querySelector("#example-color-picker-modal [data-om-component='color-picker'][data-om-component-state='mounted']"); index += 1) await sleep(100);
  if (!document.querySelector("#example-color-picker-modal [data-om-component='color-picker'][data-om-component-state='mounted']")) failures.push("remote Modal Color Picker did not mount");
  return { failures };
})()
""",
        timeout=25.0,
    )
    result.pageErrors.extend(
        f"Color Picker Form: {failure}"
        for failure in support.assertion_failures(payload)
    )

    support.click_and_assert_turbo(
        client,
        'a[href="/examples/forms/basics"]',
        "/examples/forms/basics",
        "Rich Text cleanup",
        result,
    )
    stale_apps = client.evaluate("document.querySelectorAll('.pcr-app').length", timeout=5.0)
    if stale_apps != 0:
        result.pageErrors.append(f"Color Picker Form: {stale_apps} Pickr overlays remained after Turbo navigation")


def assert_rich_text_form(client, support: ModuleType, base_url: str, result) -> None:
    """Exercise Quill sync, both response modes, reset and dynamic Modal mounting."""
    path = "/examples/forms/rich-text"
    initial_bad_response_count = len(result.badResponses)
    support.navigate(client, urllib.parse.urljoin(base_url, path))
    assert_example_page(client, support, path, "forms", "rich-text", result)
    payload = client.evaluate(
        r"""
(async () => {
  const failures = [];
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const forms = Array.from(document.querySelectorAll("[data-example-form-card] form"));
  for (let index = 0; index < 60 && forms.some((form) => form.querySelector("[data-om-component='rich-text-editor']")?.dataset.omComponentState !== "mounted"); index += 1) await sleep(100);
  if (forms.length !== 2) return { failures: ["HTML and JSON Rich Text Forms are required"] };
  const jsonForm = document.querySelector("[data-example-form-card='json'] form");
  let htmlForm = document.querySelector("[data-example-form-card='html'] form");
  if (!jsonForm || !htmlForm) return { failures: ["Rich Text response modes are missing"] };

  const edit = async (form, prefix, text) => {
    const title = form.querySelector(`[name='${prefix}-title']`);
    const component = form.querySelector("[data-om-component='rich-text-editor']");
    const textarea = component?.querySelector("[data-om-rich-text-value]");
    const editor = component?.querySelector(".ql-editor");
    if (!title || !component || !textarea || !editor) {
      failures.push(`missing ${prefix} Rich Text controls`);
      return null;
    }
    if (component.dataset.omComponentState !== "mounted" || !textarea.hidden || editor.closest("[data-om-rich-text-editor]")?.hidden) failures.push(`${prefix} Rich Text Editor did not enhance its textarea`);
    title.value = `${prefix} browser article`;
    title.dispatchEvent(new Event("input", { bubbles: true }));
    editor.focus();
    editor.innerHTML = `<p><strong>${text}</strong></p>`;
    editor.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: text }));
    await sleep(100);
    if (!textarea.value.includes(text)) failures.push(`${prefix} Quill HTML did not sync to the textarea`);
    return { component, textarea, initialValue: textarea.defaultValue };
  };

  const jsonControls = await edit(jsonForm, "json", "JSON rich text");
  jsonForm.requestSubmit();
  for (let index = 0; index < 70 && !["success", "error"].includes(jsonForm.dataset.omStatus); index += 1) await sleep(100);
  if (jsonForm.dataset.omStatus !== "success") failures.push("JSON Rich Text Form did not submit");

  const htmlControls = await edit(htmlForm, "html", "HTML rich text");
  htmlForm.reset();
  await sleep(20);
  if (htmlControls && htmlControls.textarea.value !== htmlControls.initialValue) failures.push("Rich Text reset did not restore the initial HTML");

  await edit(htmlForm, "html", "Server validation echo");
  const invalidTitle = htmlForm.querySelector("[name='html-title']");
  if (invalidTitle) invalidTitle.value = "x".repeat(101);
  htmlForm.requestSubmit();
  for (let index = 0; index < 70 && htmlForm.isConnected; index += 1) await sleep(100);
  htmlForm = document.querySelector("[data-example-form-card='html'] form");
  for (let index = 0; index < 50 && htmlForm?.querySelector("[data-om-component='rich-text-editor']")?.dataset.omComponentState !== "mounted"; index += 1) await sleep(100);
  if (!htmlForm?.querySelector("[data-om-error-for='html-title']:not([hidden])")) failures.push("HTML Rich Text Form did not render the server validation error");
  const echoedValue = htmlForm?.querySelector("[data-om-rich-text-value]")?.value || "";
  if (!echoedValue.includes("Server validation echo")) failures.push("HTML Rich Text Form did not preserve submitted editor content");
  if (htmlForm) {
    await edit(htmlForm, "html", "HTML rich text success");
    htmlForm.requestSubmit();
    for (let index = 0; index < 70 && !document.querySelector("[data-example-form-card='html'] [data-example-form-success]"); index += 1) await sleep(100);
    if (!document.querySelector("[data-example-form-card='html'] [data-example-form-success]")) failures.push("HTML Rich Text Form did not submit");
  }

  document.querySelector("[data-om-modal-url='/examples/forms/rich-text/modal']")?.click();
  for (let index = 0; index < 70 && !document.querySelector("#example-rich-text-modal [data-om-component='rich-text-editor'][data-om-component-state='mounted']"); index += 1) await sleep(100);
  if (!document.querySelector("#example-rich-text-modal .ql-editor")) failures.push("remote Modal Rich Text Editor did not mount");
  return { failures };
})()
""",
        timeout=30.0,
    )
    result.pageErrors.extend(
        f"Rich Text Form: {failure}"
        for failure in support.assertion_failures(payload)
    )
    result.badResponses[initial_bad_response_count:] = [
        response
        for response in result.badResponses[initial_bad_response_count:]
        if response.get("status") != 422
        or urllib.parse.urlparse(str(response.get("url") or "")).path
        != "/examples/forms/rich-text/submit/html"
    ]

    support.navigate(client, urllib.parse.urljoin(base_url, "/examples/forms/basics"))
    stale_editors = client.evaluate(
        "document.querySelectorAll('.ql-toolbar, .ql-container').length", timeout=5.0
    )
    if stale_editors != 0:
        result.pageErrors.append(
            f"Rich Text Form: {stale_editors} Quill nodes remained after Turbo navigation"
        )


def assert_multi_step_form(client, support: ModuleType, base_url: str, result) -> None:
    """Exercise both real save modes, step validation and server error routing on mobile."""
    path = "/examples/forms/multi-step"
    support.configure_viewport(client, 390, 844, mobile=True)
    try:
        support.navigate(client, urllib.parse.urljoin(base_url, path))
        assert_example_page(client, support, path, "forms", "multi-step", result)
        payload = client.evaluate(
            r"""
(async () => {
  const failures = [];
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const htmlForm = document.querySelector("[data-example-form-card='html'] form");
  const jsonForm = document.querySelector("[data-example-form-card='json'] form");
  if (!htmlForm || !jsonForm) return { failures: ["HTML and JSON Multi-step Forms are required"] };
  const components = Array.from(document.querySelectorAll("[data-om-component='multi-step-form']"));
  for (let index = 0; index < 50 && components.some((component) => component.dataset.omComponentState !== "mounted"); index += 1) await sleep(100);
  if (components.length !== 2 || components.some((component) => component.dataset.omComponentState !== "mounted")) failures.push("Multi-step components did not mount");
  if (document.documentElement.scrollWidth > window.innerWidth + 1) failures.push("Multi-step page overflows the mobile viewport");

  const fill = (form, name, value) => {
    const field = form.querySelector(`[name='${name}']`);
    if (!field) { failures.push(`missing field ${name}`); return; }
    field.value = value;
    field.dispatchEvent(new Event(field.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
  };
  const chooseTeam = (form, prefix) => {
    const team = form.querySelector(`[name='${prefix}-team_id']`);
    if (!team || team.options.length < 2) { failures.push(`${prefix} team choices did not load`); return; }
    team.value = team.options[1].value;
    team.dispatchEvent(new Event("change", { bubbles: true }));
  };
  const next = (form) => form.querySelector("[data-om-step-next]")?.click();
  const previous = (form) => form.querySelector("[data-om-step-previous]")?.click();
  const visibleStep = (form) => Array.from(form.querySelectorAll("[data-om-step-panel]")).findIndex((panel) => !panel.hidden);
  const fillPlanning = (form, prefix) => {
    fill(form, `${prefix}-status`, "active");
    fill(form, `${prefix}-priority`, "high");
    fill(form, `${prefix}-budget`, "12500.00");
    fill(form, `${prefix}-progress`, "45");
  };

  next(jsonForm);
  if (visibleStep(jsonForm) !== 0 || !jsonForm.querySelector("[data-om-step-indicator='0']")?.classList.contains("is-error")) failures.push("Next did not block an invalid current step");
  const stamp = Date.now();
  chooseTeam(jsonForm, "json");
  fill(jsonForm, "json-name", `Browser Multi-step JSON ${stamp}`);
  fill(jsonForm, "json-slug", "example-project-001");
  next(jsonForm);
  if (visibleStep(jsonForm) !== 1) failures.push("valid first step did not advance");
  previous(jsonForm);
  if (visibleStep(jsonForm) !== 0) failures.push("Previous did not return to the first step");
  next(jsonForm);
  fillPlanning(jsonForm, "json");
  next(jsonForm);
  if (visibleStep(jsonForm) !== 2 || jsonForm.querySelector("[data-om-form-actions]")?.hidden) failures.push("final step did not expose the ordinary submit action");
  jsonForm.requestSubmit();
  for (let index = 0; index < 80 && visibleStep(jsonForm) !== 0; index += 1) await sleep(100);
  if (visibleStep(jsonForm) !== 0 || !jsonForm.querySelector("[data-om-error-for='json-slug']:not([hidden])")) failures.push("JSON server field error did not return to its step");

  const jsonSlug = `browser-multi-step-json-${stamp}`;
  fill(jsonForm, "json-slug", jsonSlug);
  next(jsonForm);
  next(jsonForm);
  jsonForm.requestSubmit();
  for (let index = 0; index < 80 && !["success", "error"].includes(jsonForm.dataset.omStatus); index += 1) await sleep(100);
  if (jsonForm.dataset.omStatus !== "success") failures.push("JSON Multi-step Form did not save");
  const saved = await fetch(`/examples/tables/projects/table?q=${encodeURIComponent(jsonSlug)}`, { headers: { Accept: "application/json" } });
  const savedBody = await saved.text();
  if (!saved.ok || !savedBody.includes(jsonSlug)) failures.push("JSON Multi-step save did not reach the database-backed Table");

  chooseTeam(htmlForm, "html");
  const htmlSlug = `browser-multi-step-html-${stamp}`;
  fill(htmlForm, "html-name", `Browser Multi-step HTML ${stamp}`);
  fill(htmlForm, "html-slug", htmlSlug);
  next(htmlForm);
  fillPlanning(htmlForm, "html");
  next(htmlForm);
  htmlForm.requestSubmit();
  for (let index = 0; index < 80 && !document.querySelector("[data-example-form-card='html'] [data-example-form-success]"); index += 1) await sleep(100);
  if (!document.querySelector("[data-example-form-card='html'] [data-example-form-success]")) failures.push("HTML Multi-step Form did not save");
  return { failures };
})()
""",
            timeout=35.0,
        )
        result.pageErrors.extend(
            f"Multi-step Form: {failure}"
            for failure in support.assertion_failures(payload)
        )
    finally:
        support.configure_viewport(client, 1440, 1000, mobile=False)


def assert_remote_select(client, support: ModuleType, base_url: str, result) -> None:
    """Exercise rich paging, dependency reset, save and edit reload in Chrome."""
    path = "/examples/forms/selects"
    support.navigate(client, urllib.parse.urljoin(base_url, path))
    assert_example_page(client, support, path, "forms", "selects", result)
    payload = client.evaluate(
        r"""
(async () => {
  const failures = [];
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const container = document.querySelector("#example-logo-select-form");
  const form = container?.querySelector("form[data-om-form]");
  const control = form?.querySelector("select[data-om-select-control]");
  const country = form?.querySelector("[name='country_code']");
  if (!container || !form || !control || !country) return { failures: ["rich Select form is missing"] };
  for (let index = 0; index < 60 && control.dataset.omComponentState !== "mounted"; index += 1) await sleep(100);
  if (control.dataset.omComponentState !== "mounted") failures.push("rich Select did not mount");
  if (!control.value) failures.push("edit Select did not restore its initial Logo ID");
  const choices = control.closest(".choices");
  const search = choices?.querySelector(".choices__input--cloned");
  if (!choices || !search) return { failures: [...failures, "Choices search input is missing"] };

  country.value = "";
  country.dispatchEvent(new Event("change", { bubbles: true }));
  await sleep(50);
  if (control.value) failures.push("dependency change did not clear the selected Logo");

  search.value = "Atlas";
  search.dispatchEvent(new Event("input", { bubbles: true }));
  await sleep(170);
  search.value = "Mosaic";
  search.dispatchEvent(new Event("input", { bubbles: true }));
  for (let index = 0; index < 60 && !Array.from(control.options).some((option) => option.textContent?.includes("Mosaic")); index += 1) await sleep(100);
  const rapidOptions = Array.from(control.options).filter((option) => option.value);
  const rapidLabels = rapidOptions.filter((option) => !option.selected).map((option) => option.textContent || "");
  if (!rapidLabels.length || rapidLabels.some((label) => !label.includes("Mosaic")) || rapidOptions.filter((option) => option.selected).length > 1) failures.push(`latest rapid Select query did not win: ${JSON.stringify(rapidOptions.map((option) => option.textContent || ""))}`);

  search.value = "a";
  search.dispatchEvent(new Event("input", { bubbles: true }));
  for (let index = 0; index < 60 && control.options.length < 8; index += 1) await sleep(100);
  const beforeMore = control.options.length;
  const more = choices.querySelector("[data-om-select-load-more]");
  if (more && !more.hidden) {
    more.click();
    for (let index = 0; index < 60 && control.options.length <= beforeMore; index += 1) await sleep(100);
    if (control.options.length <= beforeMore) failures.push("Select load-more did not append a provider page");
  } else if (beforeMore <= 8) {
    failures.push("Select did not expose automatic or button pagination");
  }

  country.value = "CA";
  country.dispatchEvent(new Event("change", { bubbles: true }));
  for (let index = 0; index < 60 && !Array.from(control.options).some((option) => option.textContent?.includes("CA")); index += 1) await sleep(100);
  if (Array.from(control.options).some((option) => !option.textContent?.includes("CA"))) failures.push("Select dependency returned another country");
  const candidate = choices.querySelector(".choices__item--choice[data-value]");
  candidate?.click();
  await sleep(100);
  if (!control.value) {
    const fallback = Array.from(control.options).find((option) => !option.disabled);
    if (fallback) {
      fallback.selected = true;
      control.dispatchEvent(new Event("change", { bubbles: true }));
    }
  }
  const savedId = control.value;
  if (!savedId) return { failures: [...failures, "Select candidate could not be selected"] };
  form.requestSubmit();
  for (let index = 0; index < 80 && container.querySelector("form") === form; index += 1) await sleep(100);
  if (container.querySelector("form") === form) failures.push("Select save did not replace the Form");
  return { failures, savedId };
})()
""",
        timeout=35.0,
    )
    failures = support.assertion_failures(payload)
    result.pageErrors.extend(f"remote Select: {failure}" for failure in failures)
    if failures or not payload.get("savedId"):
        return
    support.navigate(client, urllib.parse.urljoin(base_url, path))
    persisted = client.evaluate(
        """
(() => ({
  failures: [],
  value: document.querySelector("#example-logo-select-form select[data-om-select-control]")?.value || ""
}))()
""",
        timeout=5.0,
    )
    if persisted.get("value") != payload["savedId"]:
        result.pageErrors.append(
            "remote Select: saved Logo did not survive a full reload"
        )


def assert_remote_autocomplete(
    client, support: ModuleType, base_url: str, result
) -> None:
    """Exercise latest-wins search, keyboard selection and hidden-ID persistence."""
    path = "/examples/forms/autocomplete"
    support.navigate(client, urllib.parse.urljoin(base_url, path))
    assert_example_page(client, support, path, "forms", "autocomplete", result)
    payload = client.evaluate(
        r"""
(async () => {
  const failures = [];
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const container = document.querySelector("#example-logo-autocomplete-form");
  const form = container?.querySelector("form[data-om-form]");
  const root = form?.querySelector("[data-om-component='autocomplete']");
  const input = root?.querySelector("[data-om-autocomplete-input]");
  const hidden = root?.querySelector("[data-om-autocomplete-value-control]");
  const country = form?.querySelector("[name='country_code']");
  if (!container || !form || !root || !input || !hidden || !country) return { failures: ["Autocomplete form is missing"] };
  for (let index = 0; index < 60 && root.dataset.omComponentState !== "mounted"; index += 1) await sleep(100);
  if (!hidden.value || !input.value) failures.push("Autocomplete initial ID and label were not restored");

  country.value = "";
  country.dispatchEvent(new Event("change", { bubbles: true }));
  await sleep(50);
  if (hidden.value || input.value) failures.push("Autocomplete dependency did not clear ID and text");

  input.value = "Atlas";
  input.dispatchEvent(new Event("input", { bubbles: true }));
  await sleep(170);
  input.value = "Mosaic";
  input.dispatchEvent(new Event("input", { bubbles: true }));
  for (let index = 0; index < 60 && !Array.from(root.querySelectorAll("[data-om-autocomplete-item]")).some((item) => item.getAttribute("aria-label")?.includes("Mosaic")); index += 1) await sleep(100);
  const rapidLabels = Array.from(root.querySelectorAll("[data-om-autocomplete-item]")).map((item) => item.getAttribute("aria-label") || "");
  if (!rapidLabels.length || rapidLabels.some((label) => !label.includes("Mosaic"))) failures.push("latest rapid Autocomplete query did not win");

  input.value = "a";
  input.dispatchEvent(new Event("input", { bubbles: true }));
  for (let index = 0; index < 60 && root.querySelectorAll("[data-om-autocomplete-item]").length < 8; index += 1) await sleep(100);
  const beforeMore = root.querySelectorAll("[data-om-autocomplete-item]").length;
  const more = root.querySelector("[data-om-autocomplete-load-more]");
  if (more && !more.hidden) {
    more.click();
    for (let index = 0; index < 60 && root.querySelectorAll("[data-om-autocomplete-item]").length <= beforeMore; index += 1) await sleep(100);
    if (root.querySelectorAll("[data-om-autocomplete-item]").length <= beforeMore) failures.push("Autocomplete load-more did not append a provider page");
  } else if (beforeMore <= 8) {
    failures.push("Autocomplete did not expose automatic or button pagination");
  }

  country.value = "GB";
  country.dispatchEvent(new Event("change", { bubbles: true }));
  input.value = "Atlas";
  input.dispatchEvent(new Event("input", { bubbles: true }));
  for (let index = 0; index < 60 && !root.querySelector("[data-om-autocomplete-item]"); index += 1) await sleep(100);
  const labels = Array.from(root.querySelectorAll("[data-om-autocomplete-item]")).map((item) => item.getAttribute("aria-label") || "");
  if (!labels.length || labels.some((label) => !label.includes("GB"))) failures.push("Autocomplete dependency returned another country");
  if (!root.querySelector("img[width='36'][height='36'][loading='lazy']")) failures.push("rich Autocomplete result did not render its fixed-size lazy image");
  input.focus();
  input.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true }));
  input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  await sleep(100);
  const savedId = hidden.value;
  if (!savedId) return { failures: [...failures, "keyboard selection did not write the hidden ID"] };
  form.requestSubmit();
  for (let index = 0; index < 80 && container.querySelector("form") === form; index += 1) await sleep(100);
  if (container.querySelector("form") === form) failures.push("Autocomplete save did not replace the Form");
  return { failures, savedId };
})()
""",
        timeout=35.0,
    )
    failures = support.assertion_failures(payload)
    result.pageErrors.extend(f"remote Autocomplete: {failure}" for failure in failures)
    if failures or not payload.get("savedId"):
        return
    support.navigate(client, urllib.parse.urljoin(base_url, path))
    persisted = client.evaluate(
        """
(() => ({
  failures: [],
  value: document.querySelector("#example-logo-autocomplete-form [data-om-autocomplete-value-control]")?.value || ""
}))()
""",
        timeout=5.0,
    )
    if persisted.get("value") != payload["savedId"]:
        result.pageErrors.append(
            "remote Autocomplete: saved Logo did not survive a full reload"
        )


def assert_database_list(client, support: ModuleType, base_url: str, result) -> None:
    """Prove the List component operates on server-rendered database rows."""
    path = "/examples/data-inputs/lists"
    support.navigate(client, urllib.parse.urljoin(base_url, path))
    assert_example_page(client, support, path, "data-inputs", "lists", result)
    payload = client.evaluate(
        r"""
(() => {
  const failures = [];
  const root = document.querySelector("[data-example-project-list]");
  const items = Array.from(root?.querySelectorAll("[data-om-list-item]") || []);
  if (items.length !== 24) failures.push(`expected 24 database projects, found ${items.length}`);
  if (items.some((item) => !item.dataset.projectId)) failures.push("a database list row has no primary key");
  if (!root?.querySelector("[data-om-list-pagination] [data-om-list-page='2']")) failures.push("local pagination did not render page 2");
  const search = root?.querySelector("[data-om-list-search]");
  if (search) {
    search.value = "does-not-exist";
    search.dispatchEvent(new Event("input", { bubbles: true }));
    if (root.querySelector("[data-om-list-empty]")?.hidden) failures.push("empty state did not appear after filtering");
  } else failures.push("database List search is missing");
  return { failures };
})()
""",
        timeout=5.0,
    )
    result.pageErrors.extend(
        f"database List: {failure}" for failure in support.assertion_failures(payload)
    )


def assert_storage_lifecycle(
    client, support: ModuleType, base_url: str, result
) -> None:
    """Exercise two real uploads and every documented model-file outcome."""
    upload_path = "/examples/storage/upload"
    support.navigate(client, urllib.parse.urljoin(base_url, upload_path))
    assert_example_page(client, support, upload_path, "storage", "upload", result)
    created = client.evaluate(
        r"""
(() => {
  const failures = [];
  const form = document.querySelector("[data-example-asset-create] form[data-om-form]");
  const roots = form ? Array.from(form.querySelectorAll("[data-om-component='upload']")) : [];
  const documentRoot = roots.find((root) => root.querySelector("[name='document_path']"));
  const previewRoot = roots.find((root) => root.querySelector("[name='preview_path']"));
  if (!form || !documentRoot || !previewRoot) return { failures: ["real upload Form is missing"] };
  if (documentRoot.dataset.omComponentState !== "mounted" || previewRoot.dataset.omComponentState !== "mounted") failures.push("Upload components did not mount");

  const drop = (root, file) => {
    const transfer = new DataTransfer();
    transfer.items.add(file);
    root.dispatchEvent(new DragEvent("drop", { bubbles: true, cancelable: true, dataTransfer: transfer }));
  };
  const pngBytes = Uint8Array.from(atob("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="), (value) => value.charCodeAt(0));
  drop(documentRoot, new File(["Oldman browser upload"], "browser-guide.txt", { type: "text/plain" }));
  drop(previewRoot, new File([pngBytes], "browser-preview.png", { type: "image/png" }));
  const documentInput = documentRoot.querySelector("input[type='file']");
  const previewInput = previewRoot.querySelector("input[type='file']");
  if (documentInput?.files?.length !== 1 || previewInput?.files?.length !== 1) failures.push("dropped files were not synchronized to native inputs");
  if (!previewRoot.querySelector("img[data-om-upload-thumbnail][src^='blob:']")) failures.push("image upload did not render a preview");
  previewRoot.querySelector("[data-om-upload-remove]")?.click();
  if (previewInput?.files?.length !== 0) failures.push("removing a preview did not clear the native input");
  drop(previewRoot, new File([pngBytes], "browser-preview.png", { type: "image/png" }));

  const name = form.querySelector("[name='display_name']");
  const description = form.querySelector("[name='description']");
  if (!name || !description) return { failures: [...failures, "upload metadata fields are missing"] };
  name.value = "Browser Storage Asset";
  description.value = "Created by the isolated Chrome gate";
  name.dispatchEvent(new Event("input", { bubbles: true }));
  description.dispatchEvent(new Event("input", { bubbles: true }));
  form.requestSubmit();
  return { failures };
})()
""",
        timeout=10.0,
    )
    failures = support.assertion_failures(created)
    result.pageErrors.extend(f"Storage upload: {failure}" for failure in failures)
    if failures:
        return
    support.wait_for_path(
        client, "/examples/storage/lifecycle", "Storage create redirect", result
    )

    initial = client.evaluate(
        r"""
(async () => {
  const failures = [];
  const root = document.querySelector("[data-example-asset-lifecycle]");
  const states = Array.from(document.querySelectorAll("[data-asset-file-state]"));
  if (!root || states.length !== 2) return { failures: ["created asset lifecycle state is missing"] };
  const files = Object.fromEntries(states.map((state) => [state.dataset.assetFileState, {
    path: state.querySelector("[data-asset-logical-name]")?.textContent?.trim() || "",
    exists: state.dataset.storageExists,
    url: state.querySelector("a[href^='/media/']")?.getAttribute("href") || ""
  }]));
  for (const [field, file] of Object.entries(files)) {
    if (!file.path.startsWith(field === "document_path" ? "examples/assets/" : "examples/previews/")) failures.push(`${field} did not store a logical name`);
    if (file.exists !== "true") failures.push(`${field} is missing from Storage`);
    if (!file.url || (await fetch(file.url)).status !== 200) failures.push(`${field} media URL is not readable`);
  }
  return { failures, assetId: root.dataset.assetId, files };
})()
""",
        timeout=10.0,
    )
    failures = support.assertion_failures(initial)
    result.pageErrors.extend(
        f"Storage initial state: {failure}" for failure in failures
    )
    if failures or not initial.get("assetId"):
        return
    asset_id = initial["assetId"]

    preserved = client.evaluate(
        r"""
(() => {
  const form = document.querySelector("[data-example-asset-edit] form[data-om-form]");
  const name = form?.querySelector("[name='display_name']");
  if (!form || !name) return { failures: ["asset edit Form is missing"] };
  name.value = "Browser Storage Asset Preserved";
  name.dispatchEvent(new Event("input", { bubbles: true }));
  form.requestSubmit();
  return { failures: [] };
})()
""",
        timeout=5.0,
    )
    result.pageErrors.extend(
        f"Storage preserve: {failure}"
        for failure in support.assertion_failures(preserved)
    )
    client.pump(1.5)
    support.navigate(
        client,
        urllib.parse.urljoin(base_url, f"/examples/storage/lifecycle?asset={asset_id}"),
    )
    after_preserve = client.evaluate(
        r"""
(() => ({
  failures: [],
  paths: Object.fromEntries(Array.from(document.querySelectorAll("[data-asset-file-state]")).map((state) => [
    state.dataset.assetFileState,
    state.querySelector("[data-asset-logical-name]")?.textContent?.trim() || ""
  ]))
}))()
""",
        timeout=5.0,
    )
    initial_paths = {name: value["path"] for name, value in initial["files"].items()}
    if after_preserve.get("paths") != initial_paths:
        result.pageErrors.append(
            "Storage preserve: empty file inputs changed stored logical names"
        )

    replaced = client.evaluate(
        r"""
(() => {
  const failures = [];
  const form = document.querySelector("[data-example-asset-edit] form[data-om-form]");
  const roots = form ? Array.from(form.querySelectorAll("[data-om-component='upload']")) : [];
  const documentRoot = roots.find((root) => root.querySelector("[name='document_path']"));
  const previewRoot = roots.find((root) => root.querySelector("[name='preview_path']"));
  if (!form || !documentRoot || !previewRoot) return { failures: ["asset replacement Uploads are missing"] };
  const drop = (root, file) => {
    const transfer = new DataTransfer();
    transfer.items.add(file);
    root.dispatchEvent(new DragEvent("drop", { bubbles: true, cancelable: true, dataTransfer: transfer }));
  };
  const pngBytes = Uint8Array.from(atob("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="), (value) => value.charCodeAt(0));
  drop(documentRoot, new File(["replacement"], "replacement.txt", { type: "text/plain" }));
  drop(previewRoot, new File([pngBytes], "replacement.png", { type: "image/png" }));
  form.requestSubmit();
  return { failures };
})()
""",
        timeout=5.0,
    )
    result.pageErrors.extend(
        f"Storage replace: {failure}"
        for failure in support.assertion_failures(replaced)
    )
    client.pump(1.5)
    support.navigate(
        client,
        urllib.parse.urljoin(base_url, f"/examples/storage/lifecycle?asset={asset_id}"),
    )
    after_replace = client.evaluate(
        r"""
(async () => {
  const failures = [];
  const states = Array.from(document.querySelectorAll("[data-asset-file-state]"));
  const files = Object.fromEntries(states.map((state) => [state.dataset.assetFileState, {
    path: state.querySelector("[data-asset-logical-name]")?.textContent?.trim() || "",
    url: state.querySelector("a[href^='/media/']")?.getAttribute("href") || ""
  }]));
  return { failures, files };
})()
""",
        timeout=5.0,
    )
    for field_name, old_file in initial["files"].items():
        new_file = after_replace.get("files", {}).get(field_name, {})
        if new_file.get("path") == old_file["path"]:
            result.pageErrors.append(
                f"Storage replace: {field_name} logical name did not change"
            )
        if new_file.get("url"):
            status = client.evaluate(
                f"fetch({json.dumps(new_file['url'])}).then((response) => response.status)",
                timeout=5.0,
            )
            if status != 200:
                result.pageErrors.append(
                    f"Storage replace: new {field_name} file is not readable"
                )

    rollback = client.evaluate(
        r"""
(async () => {
  const failures = [];
  const form = document.querySelector("[data-example-asset-rollback] form[data-om-form]");
  const root = form?.querySelector("[data-om-component='upload']");
  if (!form || !root) return { failures: ["rollback Upload Form is missing"] };
  const transfer = new DataTransfer();
  transfer.items.add(new File([Uint8Array.from([137, 80, 78, 71])], "rollback.png", { type: "image/png" }));
  root.dispatchEvent(new DragEvent("drop", { bubbles: true, cancelable: true, dataTransfer: transfer }));
  form.requestSubmit();
  const message = form.querySelector("[data-om-form-message]");
  for (let index = 0; index < 80 && (message?.hidden || !message?.textContent?.trim()); index += 1) await new Promise((resolve) => setTimeout(resolve, 100));
  if (message?.hidden || !message?.textContent?.trim() || message?.dataset.omTone !== "success") failures.push("rollback result was not shown by the Form");
  return { failures };
})()
""",
        timeout=15.0,
    )
    result.pageErrors.extend(
        f"Storage rollback: {failure}"
        for failure in support.assertion_failures(rollback)
    )
    support.navigate(
        client,
        urllib.parse.urljoin(base_url, f"/examples/storage/lifecycle?asset={asset_id}"),
    )
    after_rollback = client.evaluate(
        "document.querySelector('[data-asset-file-state=\"preview_path\"] [data-asset-logical-name]')?.textContent?.trim() || ''",
        timeout=5.0,
    )
    replacement_preview = (
        after_replace.get("files", {}).get("preview_path", {}).get("path")
    )
    if after_rollback != replacement_preview:
        result.pageErrors.append("Storage rollback: database preview path changed")

    cleared = client.evaluate(
        r"""
(() => {
  const form = Array.from(document.querySelectorAll("form[data-om-form]")).find((candidate) => candidate.action.includes("clear-preview"));
  if (!form) return { failures: ["clear preview Form is missing"] };
  form.requestSubmit();
  return { failures: [] };
})()
""",
        timeout=5.0,
    )
    result.pageErrors.extend(
        f"Storage clear: {failure}" for failure in support.assertion_failures(cleared)
    )
    client.pump(1.5)
    support.navigate(
        client,
        urllib.parse.urljoin(base_url, f"/examples/storage/lifecycle?asset={asset_id}"),
    )
    after_clear = client.evaluate(
        r"""
(() => ({
  failures: [],
  path: document.querySelector("[data-asset-file-state='preview_path'] [data-asset-logical-name]")?.textContent?.trim() || "",
  exists: document.querySelector("[data-asset-file-state='preview_path']")?.dataset.storageExists || ""
}))()
""",
        timeout=5.0,
    )
    if after_clear.get("exists") != "false":
        result.pageErrors.append("Storage clear: optional preview still exists")

    deleted = client.evaluate(
        r"""
(() => {
  const form = Array.from(document.querySelectorAll("form[data-om-form]")).find((candidate) => candidate.action.endsWith("/delete"));
  if (!form) return { failures: ["delete asset Form is missing"] };
  form.requestSubmit();
  return { failures: [] };
})()
""",
        timeout=5.0,
    )
    result.pageErrors.extend(
        f"Storage delete: {failure}" for failure in support.assertion_failures(deleted)
    )
    support.wait_for_path(client, upload_path, "Storage delete redirect", result)
    deleted_asset_present = client.evaluate(
        "Array.from(document.querySelectorAll('[data-example-assets] a')).some((link) => link.textContent?.includes('Browser Storage Asset'))",
        timeout=5.0,
    )
    if deleted_asset_present:
        result.pageErrors.append("Storage delete: deleted asset is still listed")

    api_path = "/examples/storage/api"
    support.navigate(client, urllib.parse.urljoin(base_url, api_path))
    api_result = client.evaluate(
        r"""
(async () => {
  const failures = [];
  const form = document.querySelector("form[action='/examples/storage/api/run']");
  if (!form) return { failures: ["Storage API Form is missing"] };
  form.requestSubmit();
  for (let index = 0; index < 80 && !document.querySelector("[data-storage-api-result]"); index += 1) await new Promise((resolve) => setTimeout(resolve, 100));
  const result = document.querySelector("[data-storage-api-result]");
  if (!result) return { failures: ["Storage API result did not replace its panel"] };
  const values = Array.from(result.querySelectorAll("dd")).map((node) => node.textContent?.trim() || "");
  if (values[0] === values[1]) failures.push("Storage API collision did not choose another name");
  if (values.at(-1) !== "False") failures.push("Storage API delete did not remove the fixed sample");
  return { failures };
})()
""",
        timeout=15.0,
    )
    result.pageErrors.extend(
        f"Storage API: {failure}" for failure in support.assertion_failures(api_result)
    )


def assert_project_table(
    client, support: ModuleType, base_url: str, result, *, data_format: str
) -> None:
    """Exercise filtering, paging, sorting and Modal CRUD in both Table render modes."""
    path = f"/examples/tables/{data_format}"
    support.navigate(client, urllib.parse.urljoin(base_url, path))
    assert_example_page(client, support, path, "tables", data_format, result)
    slug = f"browser-gate-{data_format}-project"
    payload = client.evaluate(
        f"""
(async () => {{
  const failures = [];
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const root = document.querySelector("#example-projects-table");
  const filterForm = document.querySelector("form[data-om-component='table-filter-form'][data-om-table-target='#example-projects-table']");
  if (!root || !filterForm) return {{ failures: ["missing {data_format} Table or filter Form"] }};
  for (let index = 0; index < 100 && root.querySelectorAll("[data-om-table-row]").length < 10; index += 1) await sleep(100);
  const rows = () => Array.from(root.querySelectorAll("[data-om-table-row]"));
  if (rows().length !== 10) failures.push(`initial page has ${{rows().length}} rows`);
  if (rows().some((row) => !row.dataset.omTableRowId)) failures.push("rows do not expose stable IDs");
  if ({json.dumps(data_format == "json")} && root.dataset.omTableFormat !== "json") failures.push("JSON Table did not use JSON mode");
  if ({json.dumps(data_format == "html")} && root.dataset.omTableFormat) failures.push("HTML Table unexpectedly used JSON mode");

  const firstId = rows()[0]?.dataset.omTableRowId;
  root.querySelector("[data-om-table-page='2']")?.click();
  for (let index = 0; index < 80 && rows()[0]?.dataset.omTableRowId === firstId; index += 1) await sleep(100);
  if (rows()[0]?.dataset.omTableRowId === firstId) failures.push("pagination did not replace the visible rows");

  const beforeSort = rows()[0]?.querySelector("[data-om-column='name']")?.textContent;
  root.querySelector("[data-om-table-sort='name']")?.click();
  for (let index = 0; index < 80 && rows()[0]?.querySelector("[data-om-column='name']")?.textContent === beforeSort; index += 1) await sleep(100);
  if (rows()[0]?.querySelector("[data-om-column='name']")?.textContent === beforeSort) failures.push("sorting did not change the visible order");

  const status = filterForm.querySelector("[name='status']");
  if (!status) return {{ failures: [...failures, "status filter is missing"] }};
  status.value = "active";
  status.dispatchEvent(new Event("change", {{ bubbles: true }}));
  filterForm.requestSubmit();
  for (let index = 0; index < 80 && rows().some((row) => !row.querySelector("[data-om-column='status']")?.textContent?.toLowerCase().includes("active")); index += 1) await sleep(100);
  if (rows().some((row) => !row.querySelector("[data-om-column='status']")?.textContent?.toLowerCase().includes("active"))) failures.push("status filter did not constrain rows");

  status.value = "";
  status.dispatchEvent(new Event("change", {{ bubbles: true }}));
  filterForm.requestSubmit();
  await sleep(500);
  document.querySelector("[data-om-modal-url='/examples/tables/projects/new-modal']")?.click();
  const modal = document.querySelector("#example-project-modal");
  for (let index = 0; index < 80 && !modal?.querySelector("form[action='/examples/tables/projects/create']"); index += 1) await sleep(100);
  const createForm = modal?.querySelector("form[action='/examples/tables/projects/create']");
  if (!modal || !createForm) return {{ failures: [...failures, "create Modal Form did not load"] }};
  const setValue = (name, value) => {{
    const field = createForm.querySelector(`[name='${{name}}']`);
    if (!field) {{ failures.push(`missing create field ${{name}}`); return; }}
    field.value = value;
    field.dispatchEvent(new Event(field.tagName === "SELECT" ? "change" : "input", {{ bubbles: true }}));
  }};
  const team = createForm.querySelector("[name='team_id']");
  if (!team || team.options.length < 2) failures.push("team choices did not load");
  else {{ team.value = team.options[1].value; team.dispatchEvent(new Event("change", {{ bubbles: true }})); }}
  setValue("name", "Browser Gate {data_format.upper()} Project");
  setValue("slug", "{slug}");
  setValue("status", "active");
  setValue("priority", "high");
  setValue("budget", "12500.00");
  setValue("progress", "45");
  const active = createForm.querySelector("[name='is_active']");
  if (active) active.checked = true;
  createForm.requestSubmit();
  for (let index = 0; index < 100 && !modal.hidden; index += 1) await sleep(100);
  if (!modal.hidden) return {{ failures: [...failures, "create action did not close the Modal"] }};

  const search = filterForm.querySelector("[name='q']");
  if (!search) return {{ failures: [...failures, "search filter is missing"] }};
  search.value = "{slug}";
  search.dispatchEvent(new Event("input", {{ bubbles: true }}));
  filterForm.requestSubmit();
  for (let index = 0; index < 100 && !root.textContent?.includes("Browser Gate {data_format.upper()} Project"); index += 1) await sleep(100);
  if (!root.textContent?.includes("Browser Gate {data_format.upper()} Project")) return {{ failures: [...failures, "created Project did not appear"] }};

  root.querySelector("[data-om-modal-url$='/edit-modal']")?.click();
  for (let index = 0; index < 80 && !modal.querySelector("form[action$='/update']"); index += 1) await sleep(100);
  const editForm = modal.querySelector("form[action$='/update']");
  const editName = editForm?.querySelector("[name='name']");
  if (!editForm || !editName) return {{ failures: [...failures, "edit Modal Form did not load"] }};
  editName.value = "Browser Gate {data_format.upper()} Project Updated";
  editName.dispatchEvent(new Event("input", {{ bubbles: true }}));
  editForm.requestSubmit();
  for (let index = 0; index < 100 && (!modal.hidden || !root.textContent?.includes("Project Updated")); index += 1) await sleep(100);
  if (!root.textContent?.includes("Project Updated")) failures.push("updated Project did not reload in the Table");

  root.querySelector("[data-om-modal-url$='/delete-modal']")?.click();
  for (let index = 0; index < 80 && !modal.querySelector("form[action$='/delete']"); index += 1) await sleep(100);
  const deleteForm = modal.querySelector("form[action$='/delete']");
  if (!deleteForm) return {{ failures: [...failures, "delete confirmation did not load"] }};
  deleteForm.requestSubmit();
  for (let index = 0; index < 100 && (!modal.hidden || rows().length !== 0); index += 1) await sleep(100);
  const emptyRow = root.querySelector("[data-om-table-body] tr:not([data-om-table-row])");
  if (rows().length !== 0 || !emptyRow || !emptyRow.textContent?.trim()) failures.push("delete did not produce the real empty state");
  return {{ failures }};
}})()
""",
        timeout=60.0,
    )
    result.pageErrors.extend(
        f"{data_format} Project Table: {failure}"
        for failure in support.assertion_failures(payload)
    )

    states_path = "/examples/tables/states"
    support.navigate(client, urllib.parse.urljoin(base_url, states_path))
    assert_example_page(client, support, states_path, "tables", "states", result)
    states = client.evaluate(
        r"""
(() => ({
  failures: ["loading", "empty", "error", "permission", "selection"]
    .filter((state) => !document.querySelector(`[data-example-table-state='${state}']`))
    .map((state) => `missing ${state} Table state`)
}))()
""",
        timeout=5.0,
    )
    result.pageErrors.extend(
        f"Table states: {failure}" for failure in support.assertion_failures(states)
    )


def assert_realtime_table(client, support: ModuleType, base_url: str, result) -> None:
    """Verify real database samples update only visible metric cells over page-owned SSE."""
    path = "/examples/tables/realtime"
    support.navigate(client, urllib.parse.urljoin(base_url, path))
    assert_example_page(client, support, path, "tables", "realtime", result)
    payload = client.evaluate(
        r"""
(async () => {
  const failures = [];
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const root = document.querySelector("[data-om-component='realtime-table']");
  if (!root) return { failures: ["missing realtime Table"] };
  for (let index = 0; index < 50 && root.dataset.omComponentState !== "mounted"; index += 1) await sleep(100);
  if (root.dataset.omComponentState !== "mounted") failures.push("realtime Table component did not mount");

  const rows = () => Array.from(root.querySelectorAll("[data-om-table-row]"));
  const rowIds = rows().map((row) => row.dataset.omTableRowId);
  if (JSON.stringify(rowIds) !== JSON.stringify(["1", "2", "3", "4"])) {
    failures.push(`expected all four fixture servers, found ${rowIds.join(", ")}`);
  }
  const initialSamples = Object.fromEntries(rows().map((row) => [
    row.dataset.omTableRowId,
    row.querySelector("[data-om-column='sampled_at']")?.textContent?.trim() || ""
  ]));
  const first = rows()[0];
  if (!first) return { failures: [...failures, "realtime Table has no database rows"] };
  const name = first.querySelector("[data-om-column='name']")?.textContent;
  const region = first.querySelector("[data-om-column='region']")?.textContent;
  const initialCpu = first.querySelector("[data-om-column='cpu_percent']")?.textContent;
  for (let index = 0; index < 50 && first.querySelector("[data-om-column='cpu_percent']")?.textContent === initialCpu; index += 1) await sleep(100);
  if (first.querySelector("[data-om-column='cpu_percent']")?.textContent === initialCpu) failures.push("SSE did not replay the database metric cell");
  if (first.querySelector("[data-om-column='name']")?.textContent !== name) failures.push("SSE changed a static server cell");
  if (first.querySelector("[data-om-column='region']")?.textContent !== region) failures.push("SSE changed a static region cell");
  for (const row of rows()) {
    const id = row.dataset.omTableRowId;
    const sampledAt = row.querySelector("[data-om-column='sampled_at']")?.textContent?.trim() || "";
    if (!id || !sampledAt || sampledAt === initialSamples[id]) {
      failures.push(`SSE did not replay a different sample for server ${id || "unknown"}`);
    }
  }

  const visibleCount = rows().length;
  const unseen = rows().at(-1);
  unseen?.remove();
  await sleep(1200);
  if (rows().length !== visibleCount - 1) failures.push("an SSE update recreated an unseen row");
  return { failures };
})()
""",
        timeout=12.0,
    )
    result.pageErrors.extend(
        f"Realtime Table: {failure}" for failure in support.assertion_failures(payload)
    )

    static_path = "/examples/tables/static"
    support.click_and_assert_turbo(
        client, f'a[href="{static_path}"]', static_path, "leave realtime Table", result
    )
    support.click_and_assert_turbo(
        client, f'a[href="{path}"]', path, "re-enter realtime Table", result
    )
    remounted = client.evaluate(
        r"""
(async () => {
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const root = document.querySelector("[data-om-component='realtime-table']");
  for (let index = 0; index < 50 && root?.dataset.omComponentState !== "mounted"; index += 1) await sleep(100);
  const failures = root?.dataset.omComponentState === "mounted" ? [] : ["realtime Table did not reconnect after Turbo navigation"];
  const rowIds = [...(root?.querySelectorAll("[data-om-table-row]") || [])].map((row) => row.dataset.omTableRowId);
  if (JSON.stringify(rowIds) !== JSON.stringify(["1", "2", "3", "4"])) failures.push("reloaded Table did not restore every server");
  // Re-entering starts another read-only replay, not a persistent live cursor.
  const cell = root?.querySelector("[data-om-column='cpu_percent']");
  const initial = cell?.textContent;
  for (let index = 0; index < 50 && cell?.textContent === initial; index += 1) await sleep(100);
  if (!cell || cell.textContent === initial) failures.push("reloaded Table did not resume SSE updates");
  return { failures };
})()
""",
        timeout=12.0,
    )
    result.pageErrors.extend(
        f"Realtime Table: {failure}"
        for failure in support.assertion_failures(remounted)
    )


def assert_modal_and_action_examples(
    client, support: ModuleType, base_url: str, result
) -> None:
    """Exercise remote Modal replacement, ordinary Forms and ordered Actions."""
    remote_path = "/examples/modals/remote"
    support.navigate(client, urllib.parse.urljoin(base_url, remote_path))
    assert_example_page(client, support, remote_path, "modals", "remote", result)
    remote = client.evaluate(
        r"""
(async () => {
  const failures = [];
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const opener = document.querySelector("#remote-modal-opener");
  const modal = document.querySelector("#remote-example-modal");
  if (!opener || !modal) return { failures: ["remote Modal markup is missing"] };
  const initialTitle = modal.querySelector("[data-om-modal-title]")?.textContent?.trim() || "";
  opener.focus();
  opener.click();
  for (let index = 0; index < 60 && !modal.querySelector("[data-example-remote-step='1']"); index += 1) await sleep(100);
  const first = modal.querySelector("[data-example-remote-step='1']");
  if (!first) return { failures: ["remote Modal first step did not load"] };
  const remoteTitle = modal.querySelector("[data-om-modal-title]")?.textContent?.trim() || "";
  if (!remoteTitle || remoteTitle === initialTitle) failures.push("remote title was not replaced");
  if (!modal.querySelector("[data-om-modal-footer] [data-example-remote-next]")) failures.push("remote footer was not replaced");
  const dropdown = modal.querySelector("[data-om-component='dropdown']");
  if (dropdown?.dataset.omComponentState !== "mounted") failures.push("remote declarative component did not mount");
  const firstHeight = modal.querySelector(".om-modal-surface")?.getBoundingClientRect().height || 0;
  modal.querySelector("[data-example-remote-next]")?.click();
  for (let index = 0; index < 60 && !modal.querySelector("[data-example-remote-step='2']"); index += 1) await sleep(100);
  if (!modal.querySelector("[data-example-remote-step='2']")) failures.push("remote Modal second step did not load");
  if (modal.querySelector("[data-example-remote-step='1']")) failures.push("first remote body was not replaced");
  const secondHeight = modal.querySelector(".om-modal-surface")?.getBoundingClientRect().height || 0;
  if (secondHeight <= firstHeight) failures.push("expanded remote body did not grow the Modal");
  modal.querySelector("[data-om-modal-close]")?.click();
  for (let index = 0; index < 30 && !modal.hidden; index += 1) await sleep(100);
  if (!modal.hidden) failures.push("remote Modal did not close");
  if (document.activeElement !== opener) failures.push("remote Modal did not restore the original opener focus");
  return { failures };
})()
""",
        timeout=20.0,
    )
    result.pageErrors.extend(
        f"Modal remote content: {failure}"
        for failure in support.assertion_failures(remote)
    )

    workflows_path = "/examples/modals/workflows"
    support.navigate(client, urllib.parse.urljoin(base_url, workflows_path))
    assert_example_page(client, support, workflows_path, "modals", "workflows", result)
    workflows = client.evaluate(
        r"""
(async () => {
  const failures = [];
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const htmlForm = document.querySelector("form[data-om-form]:not([data-om-form-mode])");
  if (!htmlForm) return { failures: ["HTML workflow Form is missing"] };
  const setFormValues = (form, prefix) => {
    const fieldName = (name) => prefix ? `${prefix}-${name}` : name;
    const name = form.querySelector(`[name='${fieldName("name")}']`);
    const email = form.querySelector(`[name='${fieldName("email")}']`);
    if (!name || !email) return false;
    name.value = `Browser ${prefix}`;
    email.value = `${(prefix || "workflow").replaceAll("-", ".")}@example.test`;
    name.dispatchEvent(new Event("input", { bubbles: true }));
    email.dispatchEvent(new Event("input", { bubbles: true }));
    return true;
  };
  if (!setFormValues(htmlForm, "containers")) failures.push("HTML workflow Form fields are missing");
  htmlForm.requestSubmit();
  for (let index = 0; index < 60 && !document.querySelector("[data-example-form-success]"); index += 1) await sleep(100);
  if (!document.querySelector("[data-example-form-success]")) failures.push("HTML workflow Form did not complete");

  document.querySelector("#workflow-modal-opener")?.click();
  const modal = document.querySelector("#workflow-example-modal");
  for (let index = 0; index < 60 && !modal?.querySelector("form[data-om-form]"); index += 1) await sleep(100);
  let form = modal?.querySelector("form[data-om-form]");
  if (!modal || !form) return { failures: [...failures, "JSON Modal workflow did not open"] };
  if (form.dataset.omComponentState !== "mounted") failures.push("Modal workflow Form did not mount");
  form.requestSubmit();
  for (let index = 0; index < 60 && !form.querySelector("[data-om-error-for]:not([hidden])"); index += 1) await sleep(100);
  if (!form.querySelector("[data-om-error-for]:not([hidden])")) failures.push("invalid Modal workflow step did not render field errors");
  if (!setFormValues(form, "")) return { failures: [...failures, "first workflow fields are missing"] };
  form.requestSubmit();
  for (let index = 0; index < 60 && !modal.querySelector("form[action='/examples/modals/workflow/2']"); index += 1) await sleep(100);
  form = modal.querySelector("form[action='/examples/modals/workflow/2']");
  if (!form) return { failures: [...failures, "first Action did not install step two"] };
  if (!setFormValues(form, "")) return { failures: [...failures, "second workflow fields are missing"] };
  form.requestSubmit();
  for (let index = 0; index < 70 && !modal.hidden; index += 1) await sleep(100);
  if (!modal.hidden) failures.push("completed Modal workflow did not close");
  return { failures };
})()
""",
        timeout=30.0,
    )
    result.pageErrors.extend(
        f"Modal Form workflow: {failure}"
        for failure in support.assertion_failures(workflows)
    )

    actions_path = "/examples/modals/actions"
    support.navigate(client, urllib.parse.urljoin(base_url, actions_path))
    assert_example_page(client, support, actions_path, "modals", "actions", result)
    actions = client.evaluate(
        r"""
(async () => {
  const failures = [];
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const click = (name) => document.querySelector(`[data-example-action='${name}']`)?.click();
  const waitFor = async (predicate, attempts = 60) => {
    for (let index = 0; index < attempts && !predicate(); index += 1) await sleep(100);
    return predicate();
  };
  const initialReplace = document.querySelector("#replace-action-result")?.textContent || "";
  const initialPrivate = document.querySelector("#private-action-result")?.textContent || "";
  const initialChain = document.querySelector("#action-chain-result")?.textContent || "";

  click("replace_html");
  if (!await waitFor(() => Boolean(document.querySelector("#replace-action-result")?.textContent?.trim()) && document.querySelector("#replace-action-result")?.textContent !== initialReplace)) failures.push("Replace HTML Action did not run");
  click("private");
  if (!await waitFor(() => Boolean(document.querySelector("#private-action-result")?.textContent?.trim()) && document.querySelector("#private-action-result")?.textContent !== initialPrivate)) failures.push("private Page Action did not run");
  click("feedback");
  if (!await waitFor(() => document.querySelector(".toastify.om-toast.on"))) failures.push("Feedback Action was not visible");

  const table = document.querySelector("#example-projects-table");
  if (!table) failures.push("Reload Table target is missing");
  else table.addEventListener("om:table:refresh", () => { table.dataset.exampleReloaded = "true"; }, { once: true });
  click("reload_table");
  if (!await waitFor(() => table?.dataset.exampleReloaded === "true", 100)) failures.push("Reload Table Action did not refresh its target");

  document.querySelector("[data-om-modal-target='#action-close-modal']")?.click();
  const modal = document.querySelector("#action-close-modal");
  if (!await waitFor(() => modal && !modal.hidden)) failures.push("close-Action Modal did not open");
  click("close_modal");
  if (!await waitFor(() => modal?.hidden === true)) failures.push("Close Modal Action did not close its source Modal");

  for (const name of ["missing_target", "unknown"]) {
    click(name);
    if (!await waitFor(() => document.querySelector(".swal2-confirm"))) failures.push(`${name} did not show its Action error`);
    document.querySelector(".swal2-confirm")?.click();
    await waitFor(() => !document.querySelector(".swal2-popup"));
  }
  click("chain_failure");
  if (!await waitFor(() => Boolean(document.querySelector("#action-chain-result")?.textContent?.trim()) && document.querySelector("#action-chain-result")?.textContent !== initialChain)) {
    const chainText = document.querySelector("#action-chain-result")?.textContent || "";
    failures.push(`Action before the failure did not run: ${chainText || "target missing"}`);
  }
  if (!await waitFor(() => document.querySelector(".swal2-confirm"))) failures.push("middle Action failure was not visible");
  const chainText = document.querySelector("#action-chain-result")?.textContent || "";
  if (chainText.includes("must not run")) failures.push("Action after the failure still ran");
  document.querySelector(".swal2-confirm")?.click();
  await waitFor(() => !document.querySelector(".swal2-popup"));
  return { failures };
})()
""",
        timeout=35.0,
    )
    result.pageErrors.extend(
        f"Response Actions: {failure}"
        for failure in support.assertion_failures(actions)
    )

    redirect = client.evaluate(
        r"""
(() => {
  document.querySelector("[data-example-action='redirect']")?.click();
  return { failures: [] };
})()
""",
        timeout=5.0,
    )
    result.pageErrors.extend(
        f"Response Actions: {failure}"
        for failure in support.assertion_failures(redirect)
    )
    client.pump(2.0)
    redirected = client.evaluate(
        "(() => ({ failures: document.querySelector('[data-action-redirected]') ? [] : ['Redirect Action did not finish navigation'] }))()",
        timeout=5.0,
    )
    result.pageErrors.extend(
        f"Response Actions: {failure}"
        for failure in support.assertion_failures(redirected)
    )


def assert_message_and_feedback_examples(
    client, support: ModuleType, base_url: str, result
) -> None:
    """Exercise signed-Cookie page Messages and API Feedback resolution."""
    page_path = "/examples/messages/page"
    page_url = urllib.parse.urljoin(base_url, page_path)
    support.navigate(client, page_url)
    assert_example_page(client, support, page_path, "messages", "page", result)
    notification_count = client.evaluate(
        "document.querySelectorAll('[data-om-activity-notification-item]').length",
        timeout=5.0,
    )

    submitted = client.evaluate(
        r"""
(() => {
  const form = document.querySelector("[data-example-message-form='single']");
  const input = form?.querySelector("[name='content']");
  if (!form || !input) return false;
  input.value = "<strong data-untrusted>Browser input stays text</strong><script>window.messageXss = true</script>";
  form.requestSubmit();
  return true;
})()
""",
        timeout=5.0,
    )
    if not submitted:
        result.pageErrors.append("Page Messages: single Message form was not found")
    client.pump(2.0)
    single = client.evaluate(
        r"""
(() => {
  const items = Array.from(document.querySelectorAll("[data-om-flash-message]"));
  const item = items[0];
  return { failures: [
    ...(location.pathname === "/examples/messages/page" ? [] : ["single Message did not redirect back to its page"]),
    ...(items.length === 1 ? [] : [`single Message rendered ${items.length} items`]),
    ...(item?.dataset.omMessageLevel === "warning" ? [] : ["single Message level was not preserved"]),
    ...(item?.textContent?.includes("<strong data-untrusted>") ? [] : ["single Message did not preserve browser input as text"]),
    ...(!item?.querySelector("script, [data-untrusted]") && !window.messageXss ? [] : ["browser input was interpreted as Message HTML"]),
    ...(items.length ? [] : [`single Message response title was ${document.title}`])
  ] };
})()
""",
        timeout=5.0,
    )
    result.pageErrors.extend(
        f"Page Messages: {failure}" for failure in support.assertion_failures(single)
    )
    support.navigate(client, page_url)
    consumed = client.evaluate(
        "(() => ({ failures: document.querySelector('[data-om-flash-message]') ? ['single Message survived a second page request'] : [] }))()",
        timeout=5.0,
    )
    result.pageErrors.extend(
        f"Page Messages: {failure}" for failure in support.assertion_failures(consumed)
    )

    client.evaluate(
        "document.querySelector(\"[data-example-message-form='multiple']\")?.requestSubmit()",
        timeout=5.0,
    )
    client.pump(2.0)
    multiple = client.evaluate(
        r"""
(() => {
  const levels = Array.from(document.querySelectorAll("[data-om-flash-message]"), (item) => item.dataset.omMessageLevel);
  return { failures: JSON.stringify(levels) === JSON.stringify(["success", "info", "warning", "error"])
    ? [] : [`multiple Message order was ${JSON.stringify(levels)}`] };
})()
""",
        timeout=5.0,
    )
    result.pageErrors.extend(
        f"Page Messages: {failure}" for failure in support.assertion_failures(multiple)
    )
    support.navigate(client, page_url)
    client.evaluate(
        "document.querySelector(\"[data-example-message-form='trusted-html']\")?.requestSubmit()",
        timeout=5.0,
    )
    client.pump(2.0)
    trusted = client.evaluate(
        r"""
(() => {
  const items = document.querySelectorAll("[data-om-flash-message]");
  const item = items[0];
  return { failures: [
    ...(items.length === 1 ? [] : [`trusted HTML rendered ${items.length} items`]),
    ...(item?.querySelector("strong")?.textContent?.trim() ? [] : ["fixed trusted Message HTML was not rendered"]),
    ...(document.querySelectorAll("[data-om-activity-notification-item]").length === %d ? [] : ["page Message changed the notification center"])
  ] };
})()
"""
        % notification_count,
        timeout=5.0,
    )
    result.pageErrors.extend(
        f"Page Messages: {failure}" for failure in support.assertion_failures(trusted)
    )
    support.navigate(client, page_url)
    consumed = client.evaluate(
        "(() => ({ failures: document.querySelector('[data-om-flash-message]') ? ['trusted Message survived a second page request'] : [] }))()",
        timeout=5.0,
    )
    result.pageErrors.extend(
        f"Page Messages: {failure}" for failure in support.assertion_failures(consumed)
    )

    feedback_path = "/examples/messages/feedback"
    support.navigate(client, urllib.parse.urljoin(base_url, feedback_path))
    assert_example_page(client, support, feedback_path, "messages", "feedback", result)
    feedback = client.evaluate(
        r"""
(async () => {
  const failures = [];
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const click = (name) => document.querySelector(`[data-example-feedback='${name}']`)?.click();
  const waitFor = async (predicate, attempts = 70) => {
    for (let index = 0; index < attempts && !predicate(); index += 1) await sleep(100);
    return predicate();
  };
  const closeAlert = async () => {
    document.querySelector(".swal2-confirm")?.click();
    await waitFor(() => !document.querySelector(".swal2-popup"));
  };
  const closeToast = async () => {
    document.querySelector(".toastify.om-toast.on .toast-close")?.click();
    await waitFor(() => !document.querySelector(".toastify.om-toast.on"));
  };
  const target = document.querySelector("#example-target-feedback");
  if (target?.dataset.omComponentState !== "mounted") failures.push("explicit Feedback target did not mount");

  click("default");
  if (!await waitFor(() => document.querySelector(".toastify.om-toast.on"))) {
    failures.push("default Page Feedback did not display its Action");
  }
  await closeToast();

  click("target");
  if (!await waitFor(() => document.querySelector(".swal2-popup"))) {
    failures.push("explicit Feedback target did not display its Action");
  }
  await closeAlert();

  click("message");
  if (!await waitFor(() => document.querySelector(".toastify.om-toast.on"))) {
    failures.push("response message did not use the Feedback fallback");
  }
  await closeToast();

  click("no_duplicate");
  if (!await waitFor(() => document.querySelector(".swal2-popup"))) {
    failures.push("explicit Feedback Action did not display");
  }
  if (document.querySelector(".toastify.om-toast.on")) {
    failures.push("response message duplicated an explicit Feedback Action");
  }
  await closeAlert();

  click("error");
  if (!await waitFor(() => document.querySelector(".swal2-popup"))) {
    failures.push("business error did not use the default Feedback alert");
  }
  await closeAlert();
  return { failures };
})()
""",
        timeout=35.0,
    )
    result.pageErrors.extend(
        f"Feedback: {failure}" for failure in support.assertion_failures(feedback)
    )


def assert_navigation_examples(
    client, support: ModuleType, base_url: str, result
) -> None:
    """Exercise Turbo history, frame/component lifecycle, cancellation and Loading."""
    lifecycle_path = "/examples/navigation/lifecycle"
    support.navigate(client, urllib.parse.urljoin(base_url, lifecycle_path))
    assert_example_page(
        client, support, lifecycle_path, "navigation", "lifecycle", result
    )
    lifecycle = client.evaluate(
        r"""
(async () => {
  const failures = [];
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const root = document.querySelector("[data-example-navigation-lifecycle]");
  const link = root?.querySelector("[data-example-slow-navigation]");
  if (!root || !link) return { failures: ["missing lifecycle example"] };
  if (root.dataset.omComponentState !== "mounted") failures.push("navigation probe did not mount");
  if (root.querySelector("[data-example-page-mounts]")?.textContent !== "1") failures.push("ExamplesPage did not mount exactly once");
  if (root.querySelector("[data-example-component-mounts]")?.textContent !== "1") failures.push("lifecycle component did not record its mount");
  const resourceCount = () => performance.getEntriesByType("resource")
    .filter((entry) => entry.name.includes("/examples/navigation/loading?slow=1")).length;
  const before = resourceCount();
  link.dispatchEvent(new MouseEvent("mouseenter", { bubbles: true }));
  link.dispatchEvent(new PointerEvent("pointerenter", { bubbles: true }));
  await sleep(500);
  if (resourceCount() !== before) failures.push("hover prefetched a navigation link");
  return { failures };
})()
""",
        timeout=5.0,
    )
    result.pageErrors.extend(
        f"navigation lifecycle: {failure}"
        for failure in support.assertion_failures(lifecycle)
    )

    before_slow = support.install_turbo_probe(client)
    loading_observed = client.evaluate(
        r"""
(async () => {
  const failures = [];
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  document.querySelector("[data-example-slow-navigation]")?.click();
  let observed = false;
  for (let index = 0; index < 20; index += 1) {
    const frame = document.querySelector("#oldman-main");
    if (frame?.dataset.omFrameState === "loading" && frame.querySelector(":scope > [data-om-scoped-preloader]")) {
      observed = true;
      break;
    }
    await sleep(25);
  }
  if (!observed) failures.push("slow Turbo navigation did not show the main-frame Loading overlay");
  return { failures };
})()
""",
        timeout=5.0,
    )
    result.pageErrors.extend(
        f"navigation Loading: {failure}"
        for failure in support.assertion_failures(loading_observed)
    )
    support.wait_for_turbo_path(
        client, "/examples/navigation/loading", before_slow, "slow navigation", result
    )
    assert_example_page(
        client, support, "/examples/navigation/loading", "navigation", "loading", result
    )

    scoped_loading = client.evaluate(
        r"""
(async () => {
  const failures = [];
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const root = document.querySelector("[data-example-navigation-loading]");
  const result = root?.querySelector("[data-example-loading-result]");
  if (!root || !result) return { failures: ["missing scoped Loading example"] };
  if (document.body.dataset.omExamplesPageMounts !== "2") failures.push("frame navigation did not mount exactly one new ExamplesPage");
  const run = async (selector, scope) => {
    const previousResult = result.textContent;
    root.querySelector(selector)?.click();
    for (let index = 0; index < 20 && scope.dataset.omPreloaderStatus !== "loading"; index += 1) await sleep(25);
    if (scope.dataset.omPreloaderStatus !== "loading") failures.push(`${selector} did not enter Loading`);
    if (!scope.querySelector(":scope > [data-om-scoped-preloader]")) failures.push(`${selector} did not render its overlay`);
    for (let index = 0; index < 80 && result.textContent === previousResult; index += 1) await sleep(50);
    if (!result.textContent?.trim() || result.textContent === previousResult) failures.push(`${selector} did not complete the real request`);
    if (scope.dataset.omPreloaderStatus !== "idle") failures.push(`${selector} did not clear Loading`);
  };
  await run("[data-example-loading-scope='local']", root);
  result.textContent = "waiting";
  await run("[data-example-loading-scope='page']", document.body);
  return { failures };
})()
""",
        timeout=15.0,
    )
    result.pageErrors.extend(
        f"scoped Loading: {failure}"
        for failure in support.assertion_failures(scoped_loading)
    )

    actions_path = "/examples/navigation/actions"
    support.click_and_assert_turbo(
        client, f'a[href="{actions_path}"]', actions_path, "navigation Actions", result
    )
    assert_example_page(client, support, actions_path, "navigation", "actions", result)
    before_leave = support.install_turbo_probe(client)
    cancellation = client.evaluate(
        r"""
(async () => {
  const failures = [];
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const action = document.querySelector("[data-example-slow-action]");
  const leave = document.querySelector("[data-example-leave-action]");
  if (!action || !leave) return { failures: ["missing Action cancellation controls"] };
  action.click();
  for (let index = 0; index < 20 && action.dataset.omLoading !== "true"; index += 1) await sleep(25);
  if (action.dataset.omLoading !== "true") failures.push("slow Action never entered its pending state");
  leave.click();
  return { failures };
})()
""",
        timeout=5.0,
    )
    result.pageErrors.extend(
        f"Action cancellation: {failure}"
        for failure in support.assertion_failures(cancellation)
    )
    support.wait_for_turbo_path(
        client,
        "/examples/navigation/loading",
        before_leave,
        "leave pending Action",
        result,
    )
    client.pump(1.25)
    late_action = client.evaluate(
        r"""
(() => ({
  failures: document.body.textContent?.includes("The slow Action completed on its original page.")
    ? ["a late Action changed the next page"]
    : []
}))()
""",
        timeout=5.0,
    )
    result.pageErrors.extend(
        f"Action cancellation: {failure}"
        for failure in support.assertion_failures(late_action)
    )

    support.click_and_assert_turbo(
        client, f'a[href="{lifecycle_path}"]', lifecycle_path, "history origin", result
    )
    support.click_and_assert_turbo(
        client, f'a[href="{actions_path}"]', actions_path, "history destination", result
    )
    history_back = client.evaluate(
        r"""
(() => {
  const trigger = document.querySelector("[data-om-history-back]");
  if (!trigger) return { failures: ["missing HistoryBack trigger"] };
  trigger.click();
  return { failures: [] };
})()
""",
        timeout=5.0,
    )
    result.pageErrors.extend(
        f"HistoryBack: {failure}"
        for failure in support.assertion_failures(history_back)
    )
    support.wait_for_path(client, lifecycle_path, "HistoryBack", result)

    support.click_and_assert_turbo(
        client,
        'a[href="/examples/navigation/loading"]',
        "/examples/navigation/loading",
        "network failure page",
        result,
    )
    expected_bad_response = len(result.badResponses)
    try:
        client.command(
            "Network.setBlockedURLs", {"urls": ["*examples/navigation/loading/wait*"]}
        )
        failed_request = client.evaluate(
            r"""
(async () => {
  const failures = [];
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  document.querySelector("[data-example-network-failure]")?.click();
  for (let index = 0; index < 60 && !document.querySelector(".swal2-popup"); index += 1) await sleep(50);
  const popup = document.querySelector(".swal2-popup");
  if (!popup) failures.push("network failure did not show the Dashboard Feedback");
  document.querySelector(".swal2-close")?.click();
  return { failures };
})()
""",
            timeout=8.0,
        )
        result.pageErrors.extend(
            f"network failure: {failure}"
            for failure in support.assertion_failures(failed_request)
        )
        client.pump(0.25)
    finally:
        client.command("Network.setBlockedURLs", {"urls": []})
        del result.badResponses[expected_bad_response:]


def drag_sortable_item(
    client, item_id: str, target_status: str, *, touch: bool = False
) -> None:
    """Move one real card with Chrome input events instead of mutating the DOM."""
    coordinates = client.evaluate(
        f"""
(async () => {{
  const item = document.querySelector('[data-om-item-id="{item_id}"]');
  const handle = item?.querySelector('[data-om-sortable-handle]');
  const target = document.querySelector('[data-om-list-id="{target_status}"]');
  if (!item || !handle || !target) return null;
  item.scrollIntoView({{ block: "center", inline: "center" }});
  await new Promise((resolve) => requestAnimationFrame(resolve));
  const start = handle.getBoundingClientRect();
  const end = target.getBoundingClientRect();
  const visibleEndTop = Math.max(end.top + 18, 18);
  const visibleEndBottom = Math.min(end.bottom - 18, window.innerHeight - 18);
  return {{
    startX: start.left + start.width / 2,
    startY: start.top + start.height / 2,
    endX: end.left + end.width / 2,
    endY: (visibleEndTop + visibleEndBottom) / 2
  }};
}})()
""",
        timeout=5.0,
    )
    if not isinstance(coordinates, dict):
        raise RuntimeError(
            f"Sortable drag coordinates are unavailable for task {item_id}"
        )

    start_x = coordinates["startX"]
    start_y = coordinates["startY"]
    end_x = coordinates["endX"]
    end_y = coordinates["endY"]
    if touch:
        client.command(
            "Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": 1}
        )
        client.command(
            "Input.dispatchTouchEvent",
            {
                "type": "touchStart",
                "touchPoints": [
                    {
                        "id": 1,
                        "x": start_x,
                        "y": start_y,
                        "radiusX": 2,
                        "radiusY": 2,
                        "force": 1,
                    }
                ],
            },
        )
        for step in range(1, 13):
            ratio = step / 12
            client.command(
                "Input.dispatchTouchEvent",
                {
                    "type": "touchMove",
                    "touchPoints": [
                        {
                            "id": 1,
                            "x": start_x + (end_x - start_x) * ratio,
                            "y": start_y + (end_y - start_y) * ratio,
                            "radiusX": 2,
                            "radiusY": 2,
                            "force": 1,
                        }
                    ],
                },
            )
            client.pump(0.03)
        client.command(
            "Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []}
        )
        return

    client.command(
        "Input.dispatchMouseEvent", {"type": "mouseMoved", "x": start_x, "y": start_y}
    )
    client.command(
        "Input.dispatchMouseEvent",
        {
            "type": "mousePressed",
            "x": start_x,
            "y": start_y,
            "button": "left",
            "buttons": 1,
            "clickCount": 1,
        },
    )
    for step in range(1, 13):
        ratio = step / 12
        client.command(
            "Input.dispatchMouseEvent",
            {
                "type": "mouseMoved",
                "x": start_x + (end_x - start_x) * ratio,
                "y": start_y + (end_y - start_y) * ratio,
                "button": "left",
                "buttons": 1,
            },
        )
        client.pump(0.03)
    client.command(
        "Input.dispatchMouseEvent",
        {
            "type": "mouseReleased",
            "x": end_x,
            "y": end_y,
            "button": "left",
            "buttons": 0,
            "clickCount": 1,
        },
    )


def assert_sortable_workflow(
    client, support: ModuleType, base_url: str, result
) -> None:
    """Exercise mouse, touch, keyboard, refusal, rollback and persistence."""
    path = "/examples/sortable/workflow"
    support.navigate(client, urllib.parse.urljoin(base_url, path))
    assert_example_page(client, support, path, "sortable", "workflow", result)
    inventory = client.evaluate(
        r"""
(() => {
  const board = document.querySelector("[data-example-sortable-board]");
  const lanes = [...document.querySelectorAll("[data-om-sortable-list]")];
  return {
    failures: [
      ...(!board ? ["missing Sortable board"] : []),
      ...(board?.dataset.omComponentState !== "mounted" ? ["SortableList did not mount"] : []),
      ...(lanes.length !== 4 ? [`expected 4 task lanes, found ${lanes.length}`] : []),
      ...(document.querySelectorAll("[data-om-sortable-item]").length !== 12 ? ["the board does not use 12 real tasks"] : [])
    ]
  };
})()
""",
        timeout=5.0,
    )
    result.pageErrors.extend(
        f"Sortable inventory: {failure}"
        for failure in support.assertion_failures(inventory)
    )

    move = client.evaluate(
        r"""
(() => {
  const item = [...document.querySelectorAll("[data-om-sortable-item]")]
    .find((candidate) => candidate.dataset.exampleTaskPriority !== "critical" && candidate.parentElement?.dataset.omListId !== "done");
  const source = item?.parentElement?.dataset.omListId;
  const target = source === "todo" ? "in_progress" : "todo";
  return item && source ? { failures: [], itemId: item.dataset.omItemId, source, target } : { failures: ["missing normal task for mouse drag"] };
})()
""",
        timeout=5.0,
    )
    move_failures = support.assertion_failures(move)
    result.pageErrors.extend(f"Sortable mouse: {failure}" for failure in move_failures)
    if not move_failures:
        item_id = str(move["itemId"])
        target = str(move["target"])
        drag_sortable_item(client, item_id, target)
        moved = client.evaluate(
            f"""
(async () => {{
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  for (let index = 0; index < 80; index += 1) {{
    const item = document.querySelector('[data-om-item-id="{item_id}"]');
    if (item?.parentElement?.dataset.omListId === "{target}" && !document.querySelector("[data-om-sortable-pending]")) break;
    await sleep(100);
  }}
  const item = document.querySelector('[data-om-item-id="{item_id}"]');
  return {{ failures: item?.parentElement?.dataset.omListId === "{target}" ? [] : ["mouse drag did not move the task"] }};
}})()
""",
            timeout=10.0,
        )
        result.pageErrors.extend(
            f"Sortable mouse: {failure}"
            for failure in support.assertion_failures(moved)
        )
        support.navigate(client, urllib.parse.urljoin(base_url, path))
        persisted = client.evaluate(
            f"""
(() => ({{ failures: document.querySelector('[data-om-item-id="{item_id}"]')?.parentElement?.dataset.omListId === "{target}" ? [] : ["mouse move did not persist after reload"] }}))()
""",
            timeout=5.0,
        )
        result.pageErrors.extend(
            f"Sortable persistence: {failure}"
            for failure in support.assertion_failures(persisted)
        )

    keyboard = client.evaluate(
        r"""
(async () => {
  const originalBoard = document.querySelector("[data-example-sortable-board]");
  const button = document.querySelector('[data-om-url*="interaction=keyboard"]');
  if (!button) return { failures: ["missing keyboard move button"] };
  const url = new URL(button.dataset.omUrl, location.href);
  const itemId = url.searchParams.get("item_id");
  const target = url.searchParams.get("target_list");
  button.click();
  for (let index = 0; index < 80; index += 1) {
    const item = document.querySelector(`[data-om-item-id="${itemId}"]`);
    const board = document.querySelector("[data-example-sortable-board]");
    if (board !== originalBoard && item?.parentElement?.dataset.omListId === target && board?.dataset.omComponentState === "mounted") break;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  const item = document.querySelector(`[data-om-item-id="${itemId}"]`);
  return { failures: item?.parentElement?.dataset.omListId === target ? [] : ["keyboard move did not refresh the board"] };
})()
""",
        timeout=10.0,
    )
    result.pageErrors.extend(
        f"Sortable keyboard: {failure}"
        for failure in support.assertion_failures(keyboard)
    )

    rejection = client.evaluate(
        r"""
(() => {
  const item = [...document.querySelectorAll('[data-example-task-priority="critical"]')]
    .find((candidate) => candidate.parentElement?.dataset.omListId !== "done");
  const source = item?.parentElement?.dataset.omListId;
  return item && source ? { failures: [], itemId: item.dataset.omItemId, source } : { failures: ["missing critical rejection task"] };
})()
""",
        timeout=5.0,
    )
    rejection_failures = support.assertion_failures(rejection)
    result.pageErrors.extend(
        f"Sortable rejection: {failure}" for failure in rejection_failures
    )
    if not rejection_failures:
        item_id = str(rejection["itemId"])
        source = str(rejection["source"])
        drag_sortable_item(client, item_id, "done")
        rejected = client.evaluate(
            f"""
(async () => {{
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  for (let index = 0; index < 80; index += 1) {{
    const item = document.querySelector('[data-om-item-id="{item_id}"]');
    const popup = document.querySelector(".toastify.om-toast.on");
    if (item?.parentElement?.dataset.omListId === "{source}"
      && !document.querySelector("[data-om-sortable-pending]")
      && popup) break;
    await sleep(100);
  }}
  const item = document.querySelector('[data-om-item-id="{item_id}"]');
  const popup = document.querySelector(".toastify.om-toast.on");
  return {{ failures: [
    ...(item?.parentElement?.dataset.omListId !== "{source}" ? ["rejected task did not roll back"] : []),
    ...(!popup ? ["business rejection was not visible"] : [])
  ] }};
}})()
""",
            timeout=10.0,
        )
        result.pageErrors.extend(
            f"Sortable rejection: {failure}"
            for failure in support.assertion_failures(rejected)
        )
        client.evaluate(
            "document.querySelector('.toastify.om-toast.on .toast-close')?.click()",
            timeout=5.0,
        )

    blocked_responses = len(result.badResponses)
    try:
        client.command(
            "Network.setBlockedURLs", {"urls": ["*examples/sortable/tasks/move*"]}
        )
        network = client.evaluate(
            r"""
(() => {
  const item = [...document.querySelectorAll("[data-om-sortable-item]")]
    .find((candidate) => candidate.dataset.exampleTaskPriority !== "critical");
  const source = item?.parentElement?.dataset.omListId;
  const target = source === "todo" ? "review" : "todo";
  return item && source ? { failures: [], itemId: item.dataset.omItemId, source, target } : { failures: ["missing network rollback task"] };
})()
""",
            timeout=5.0,
        )
        network_failures = support.assertion_failures(network)
        result.pageErrors.extend(
            f"Sortable network: {failure}" for failure in network_failures
        )
        if not network_failures:
            item_id = str(network["itemId"])
            source = str(network["source"])
            drag_sortable_item(client, item_id, str(network["target"]))
            popup = client.evaluate(
                r"""
(async () => {
  for (let index = 0; index < 80 && !document.querySelector(".swal2-popup"); index += 1) {
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  const popup = document.querySelector(".swal2-popup");
  popup?.querySelector(".swal2-close, .swal2-confirm")?.click();
  return { failures: popup ? [] : ["network failure Feedback was not visible"] };
})()
""",
                timeout=10.0,
            )
            result.pageErrors.extend(
                f"Sortable network: {failure}"
                for failure in support.assertion_failures(popup)
            )
            rolled_back = client.evaluate(
                f"""
(async () => {{
  for (let index = 0; index < 80; index += 1) {{
    const item = document.querySelector('[data-om-item-id="{item_id}"]');
    if (item?.parentElement?.dataset.omListId === "{source}" && !document.querySelector("[data-om-sortable-pending]")) break;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }}
  return {{ failures: document.querySelector('[data-om-item-id="{item_id}"]')?.parentElement?.dataset.omListId === "{source}" ? [] : ["network failure did not restore the task"] }};
}})()
""",
                timeout=10.0,
            )
            result.pageErrors.extend(
                f"Sortable network: {failure}"
                for failure in support.assertion_failures(rolled_back)
            )
    finally:
        client.command("Network.setBlockedURLs", {"urls": []})
        del result.badResponses[blocked_responses:]

    client.command(
        "Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": 1}
    )
    try:
        support.navigate(client, urllib.parse.urljoin(base_url, path))
        touch = client.evaluate(
            r"""
(() => {
  const item = [...document.querySelectorAll("[data-om-sortable-item]")]
    .find((candidate) => candidate.dataset.exampleTaskPriority !== "critical" && candidate.parentElement?.dataset.omListId !== "review");
  const source = item?.parentElement?.dataset.omListId;
  return item && source ? { failures: [], itemId: item.dataset.omItemId, target: "review" } : { failures: ["missing task for touch drag"] };
})()
""",
            timeout=5.0,
        )
        touch_failures = support.assertion_failures(touch)
        result.pageErrors.extend(
            f"Sortable touch: {failure}" for failure in touch_failures
        )
        if not touch_failures:
            item_id = str(touch["itemId"])
            drag_sortable_item(client, item_id, "review", touch=True)
            touched = client.evaluate(
                f"""
(async () => {{
  for (let index = 0; index < 80; index += 1) {{
    const item = document.querySelector('[data-om-item-id="{item_id}"]');
    if (item?.parentElement?.dataset.omListId === "review" && !document.querySelector("[data-om-sortable-pending]")) break;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }}
  return {{ failures: document.querySelector('[data-om-item-id="{item_id}"]')?.parentElement?.dataset.omListId === "review" ? [] : ["touch drag did not move the task"] }};
}})()
""",
                timeout=10.0,
            )
            result.pageErrors.extend(
                f"Sortable touch: {failure}"
                for failure in support.assertion_failures(touched)
            )
    finally:
        client.command("Emulation.setTouchEmulationEnabled", {"enabled": False})


def assert_chart_examples(client, support: ModuleType, base_url: str, result) -> None:
    """Exercise database aggregations, remote states and both realtime SSE paths."""
    for page, expected in (("trends", 4), ("composition", 3), ("distribution", 5)):
        path = f"/examples/charts/{page}"
        support.navigate(client, urllib.parse.urljoin(base_url, path))
        assert_example_page(client, support, path, "charts", page, result)
        payload = client.evaluate(
            f"""
(async () => {{
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  for (let index = 0; index < 100; index += 1) {{
    const charts = [...document.querySelectorAll("[data-om-component='apex-chart']")];
    if (charts.length === {expected} && charts.every((chart) => chart.dataset.omStatus === "success")) break;
    await sleep(100);
  }}
  const charts = [...document.querySelectorAll("[data-om-component='apex-chart']")];
  return {{ failures: [
    ...(charts.length === {expected} ? [] : [`expected {expected} charts, found ${{charts.length}}`]),
    ...(charts.every((chart) => chart.dataset.omStatus === "success") ? [] : ["a database Chart did not render"]),
    ...(charts.every((chart) => !chart.querySelector("script")) ? [] : ["Chart metadata rendered executable markup"]),
    ...(charts.every((chart) => !chart.querySelector("[data-om-chart-summary]")?.hidden) ? [] : ["Chart summary is missing"]),
    ...(charts.every((chart) => !chart.querySelector("[data-om-chart-meta]")?.hidden) ? [] : ["Chart metadata is missing"])
  ] }};
}})()
""",
            timeout=15.0,
        )
        result.pageErrors.extend(
            f"Chart {page}: {failure}"
            for failure in support.assertion_failures(payload)
        )

    states_path = "/examples/charts/states"
    support.navigate(client, urllib.parse.urljoin(base_url, states_path))
    assert_example_page(client, support, states_path, "charts", "states", result)
    states = client.evaluate(
        r"""
(async () => {
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const chart = document.querySelector("#example-chart-states");
  for (let index = 0; index < 100 && chart?.dataset.omStatus !== "success"; index += 1) await sleep(100);
  document.querySelector("[data-example-chart-slow]")?.click();
  await sleep(50);
  document.querySelector("[data-example-chart-latest]")?.click();
  for (let index = 0; index < 100 && !document.querySelector("[data-example-chart-request-state]")?.textContent?.includes("range=7d"); index += 1) await sleep(100);
  await sleep(900);
  const meta = chart?.querySelector("[data-om-chart-meta]")?.textContent ?? "";
  return { failures: [
    ...(chart?.dataset.omStatus === "success" ? [] : ["latest Chart request did not succeed"]),
    ...(meta.includes("7d") ? [] : ["slow request replaced the latest range"])
  ] };
})()
""",
        timeout=15.0,
    )
    result.pageErrors.extend(
        f"Chart latest-wins: {failure}"
        for failure in support.assertion_failures(states)
    )

    expected_bad_responses = len(result.badResponses)
    states = client.evaluate(
        r"""
(async () => {
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const chart = document.querySelector("#example-chart-states");
  const waitFor = async (status) => {
    for (let index = 0; index < 100 && chart?.dataset.omStatus !== status; index += 1) await sleep(100);
    return chart?.dataset.omStatus === status;
  };
  document.querySelector('[data-example-chart-source*="/empty"]')?.click();
  const empty = await waitFor("empty");
  document.querySelector('[data-example-chart-source*="/unknown"]')?.click();
  const error = await waitFor("error");
  document.querySelector('[data-example-chart-source*="/forbidden"]')?.click();
  const forbidden = await waitFor("error");
  return { failures: [
    ...(empty ? [] : ["empty state did not render"]),
    ...(error ? [] : ["404 state did not render"]),
    ...(forbidden ? [] : ["403 state did not render"])
  ] };
})()
""",
        timeout=30.0,
    )
    result.pageErrors.extend(
        f"Chart states: {failure}" for failure in support.assertion_failures(states)
    )
    del result.badResponses[expected_bad_responses:]
    result.consoleErrors[:] = [
        error
        for error in result.consoleErrors
        if "/examples/charts/data/unknown" not in error
        and "/examples/charts/data/forbidden" not in error
    ]

    realtime_path = "/examples/charts/realtime"
    support.navigate(client, urllib.parse.urljoin(base_url, realtime_path))
    assert_example_page(client, support, realtime_path, "charts", "realtime", result)
    realtime = client.evaluate(
        r"""
(async () => {
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const root = document.querySelector("[data-example-realtime-chart]");
  for (let index = 0; index < 120 && Number(root?.dataset.exampleRealtimeUpdates ?? "0") < 6; index += 1) await sleep(100);
  await sleep(250);
  const chart = root?.querySelector("[data-om-component='apex-chart']");
  let renderEvents = 0;
  chart?.addEventListener("om:chart:render", () => { renderEvents += 1; });
  const before = Number(root?.dataset.exampleRealtimeUpdates ?? "0");
  root?.querySelector("[data-example-publish-metric]")?.click();
  for (let index = 0; index < 120 && root?.querySelector("[data-example-realtime-source]")?.textContent !== "redis"; index += 1) await sleep(100);
  return { failures: [
    ...(before > 0 ? [] : ["stream.send replay did not reach the Chart"]),
    ...(root?.querySelector("[data-example-realtime-source]")?.textContent === "redis" ? [] : ["Redis publish_stream update did not arrive"]),
    ...(Number(root?.dataset.exampleRealtimeUpdates ?? "0") > before ? [] : ["Redis update was not appended"]),
    ...(renderEvents === 0 ? [] : ["realtime update rebuilt the Chart"])
  ] };
})()
""",
        timeout=30.0,
    )
    result.pageErrors.extend(
        f"Chart realtime: {failure}" for failure in support.assertion_failures(realtime)
    )

    client.evaluate(
        r"""
(() => {
  window.__exampleEventSourceCloseCount = 0;
  const close = EventSource.prototype.close;
  window.__exampleOriginalEventSourceClose = close;
  EventSource.prototype.close = function () {
    // The old Page also closes its notification connection. Count only the Chart.
    if (new URL(this.url, location.href).pathname === "/examples/charts/realtime/events") {
      window.__exampleEventSourceCloseCount += 1;
    }
    return close.call(this);
  };
})()
""",
        timeout=5.0,
    )
    trends_path = "/examples/charts/trends"
    support.click_and_assert_turbo(
        client,
        f'a[href="{trends_path}"]',
        trends_path,
        "realtime Chart leave",
        result,
    )
    closed = client.evaluate(
        "(() => { EventSource.prototype.close = window.__exampleOriginalEventSourceClose; return window.__exampleEventSourceCloseCount; })()",
        timeout=5.0,
    )
    if closed != 1:
        result.pageErrors.append(
            f"Chart realtime: expected one closed EventSource, found {closed}"
        )


def assert_notification_examples(
    client, support: ModuleType, base_url: str, result
) -> None:
    """Exercise current-user delivery and prove a second Session stays isolated."""
    ensure_secondary_user()
    other_port = support.find_free_port()
    other_profile = tempfile.mkdtemp(prefix="oldman-examples-notification-other-")
    other_chrome = support.launch_chrome(other_port, other_profile)
    other_client = None
    try:
        support.wait_for_chrome_devtools(other_chrome, other_port)
        other_client = support.CDPClient(
            support.create_page_websocket(other_port), result
        )
        for domain in ("Page", "Runtime", "Network", "Log"):
            other_client.command(f"{domain}.enable")
        support.configure_viewport(other_client, 1280, 900, mobile=False)
        support.login(other_client, base_url, SECONDARY_USERNAME, SECONDARY_PASSWORD)

        generator_path = "/examples/notifications/generator"
        generator_url = urllib.parse.urljoin(base_url, generator_path)
        support.navigate(client, generator_url)
        support.navigate(other_client, generator_url)
        assert_example_page(
            client, support, generator_path, "notifications", "generator", result
        )
        assert_example_page(
            other_client,
            support,
            generator_path,
            "notifications",
            "generator",
            result,
        )

        connection_contract = client.evaluate(
            r"""
(() => ({ failures: [
  ...(document.head.querySelectorAll('meta[name="oldman-user-events-url"]').length === 1 ? [] : ["expected one user-events meta"])
] }))()
""",
            timeout=5.0,
        )
        result.pageErrors.extend(
            f"Notifications: {failure}"
            for failure in support.assertion_failures(connection_contract)
        )
        other_before = other_client.evaluate(
            "Number(document.querySelector('[data-om-user-notification-count]')?.textContent || 0)",
            timeout=5.0,
        )

        persistent = client.evaluate(
            r"""
(async () => {
  const failures = [];
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const waitFor = async (predicate) => {
    for (let index = 0; index < 100 && !predicate(); index += 1) await sleep(100);
    return predicate();
  };
  const form = document.querySelector("[data-example-notification-form]");
  const count = () => Number(document.querySelector("[data-om-user-notification-count]")?.textContent || 0);
  if (!(form instanceof HTMLFormElement)) return { failures: ["generator Form was missing"] };
  form.elements.namedItem("mode").value = "persistent";
  form.elements.namedItem("presentation").value = "none";
  form.elements.namedItem("level").value = "warning";
  form.elements.namedItem("content_mode").value = "trusted_html";
  form.elements.namedItem("title").value = "Current user only";
  form.elements.namedItem("body").value = "<script data-example-ignored-body>ignored browser input</script>";
  form.elements.namedItem("href").value = "/user-notifications";
  form.elements.namedItem("icon").value = "ri-notification-3-line";
  const before = count();
  form.requestSubmit();
  const delivered = await waitFor(() => count() === before + 1 && document.querySelector("[data-om-user-notification-preview]")?.textContent?.includes("Current user only"));
  if (!delivered) failures.push("persistent notification did not update the topbar");
  if (!document.querySelector("[data-om-user-notification-preview] [data-example-notification-html]")) failures.push("trusted notification HTML did not render in the topbar");
  if (document.querySelector("[data-example-ignored-body]")) failures.push("browser input replaced trusted server HTML");
  if (document.querySelector(".swal2-popup.swal2-show")) failures.push("center-only notification opened Feedback");
  if (!await waitFor(() => form.dataset.omLoading !== "true")) failures.push("persistent request did not finish");
  return { failures, before, after: count() };
})()
""",
            timeout=20.0,
        )
        result.pageErrors.extend(
            f"Notifications: {failure}"
            for failure in support.assertion_failures(persistent)
        )

        other_client.pump(1.0)
        other_after = other_client.evaluate(
            r"""
(() => ({
  count: Number(document.querySelector("[data-om-user-notification-count]")?.textContent || 0),
  popup: document.querySelector(".swal2-popup.swal2-show") !== null
}))()
""",
            timeout=5.0,
        )
        if other_after != {"count": other_before, "popup": False}:
            result.pageErrors.append(
                f"Notifications: delivery crossed users: {other_before!r} -> {other_after!r}"
            )

        transient = client.evaluate(
            r"""
(async () => {
  const failures = [];
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const waitFor = async (predicate) => {
    for (let index = 0; index < 100 && !predicate(); index += 1) await sleep(100);
    return predicate();
  };
  const form = document.querySelector("[data-example-notification-form]");
  const count = () => Number(document.querySelector("[data-om-user-notification-count]")?.textContent || 0);
  const before = count();
  form.elements.namedItem("mode").value = "temporary";
  form.elements.namedItem("presentation").value = "modal";
  form.elements.namedItem("level").value = "info";
  form.elements.namedItem("content_mode").value = "text";
  form.elements.namedItem("title").value = "<strong data-example-untrusted-title>Temporary current-user modal</strong>";
  form.elements.namedItem("body").value = "<em data-example-untrusted-body>This notification is not stored.</em>";
  form.elements.namedItem("href").value = "";
  form.requestSubmit();
  if (!await waitFor(() => document.querySelector(".swal2-title")?.textContent?.includes("Temporary current-user modal"))) failures.push("temporary modal was not delivered");
  if (document.querySelector("[data-example-untrusted-title], [data-example-untrusted-body]")) failures.push("free notification input was interpreted as HTML");
  if (count() !== before) failures.push("temporary delivery changed the unread count");
  document.querySelector(".swal2-close, .swal2-cancel, .swal2-confirm")?.click();
  await waitFor(() => !document.querySelector(".swal2-popup.swal2-show"));
  if (!await waitFor(() => form.dataset.omLoading !== "true")) failures.push("temporary request did not finish");
  return { failures, count: count() };
})()
""",
            timeout=20.0,
        )
        result.pageErrors.extend(
            f"Notifications: {failure}"
            for failure in support.assertion_failures(transient)
        )
        other_client.pump(1.0)
        if other_client.evaluate(
            "document.querySelector('.swal2-popup.swal2-show') !== null",
            timeout=5.0,
        ):
            result.pageErrors.append(
                "Notifications: temporary modal crossed into the second Session"
            )

        center_example = "/examples/notifications/center"
        support.navigate(client, urllib.parse.urljoin(base_url, center_example))
        assert_example_page(
            client, support, center_example, "notifications", "center", result
        )
        support.click_and_assert_turbo(
            client,
            "[data-example-open-notification-center]",
            "/user-notifications",
            "notification center",
            result,
        )
        center = client.evaluate(
            """
(() => ({ failures: [
  ...(document.querySelector('[data-om-user-notification-center]') ? [] : ['shared notification center did not render']),
  ...(document.querySelector('[data-om-user-notification-center] [data-example-notification-html]') ? [] : ['trusted notification HTML did not render in the center'])
] }))()
""",
            timeout=5.0,
        )
        result.pageErrors.extend(
            f"Notifications: {failure}"
            for failure in support.assertion_failures(center)
        )
    finally:
        if other_client is not None:
            other_client.close()
        other_chrome.terminate()
        try:
            other_chrome.wait(timeout=3)
        except subprocess.TimeoutExpired:
            other_chrome.kill()
            other_chrome.wait(timeout=3)
        shutil.rmtree(other_profile, ignore_errors=True)


def assert_auth_session_examples(
    client, support: ModuleType, base_url: str, result
) -> None:
    """Exercise the real staff guard, Session snapshot, expiry and revocation UI."""
    identity_path = "/examples/auth/identity"
    support.navigate(client, urllib.parse.urljoin(base_url, identity_path))
    assert_example_page(client, support, identity_path, "auth", "identity", result)
    identity = client.evaluate(
        r"""
(() => ({ failures: [
  ...(document.querySelector("[data-example-auth-identity]") ? [] : ["current identity was not rendered"]),
  ...(document.querySelector("[data-example-auth-identity]")?.textContent?.includes("oldman_admin") ? [] : ["current username was not taken from SessionData"])
] }))()
""",
        timeout=5.0,
    )
    result.pageErrors.extend(
        f"Auth/Session: {failure}" for failure in support.assertion_failures(identity)
    )

    guards_path = "/examples/auth/guards"
    support.navigate(client, urllib.parse.urljoin(base_url, guards_path))
    assert_example_page(client, support, guards_path, "auth", "guards", result)
    guards = client.evaluate(
        r"""
(async () => {
  const headers = { Accept: "application/json" };
  const authenticated = await fetch("/examples/auth/probe", { headers });
  const authenticatedBody = await authenticated.json();
  return { failures: [
    ...(authenticated.status === 200 && authenticatedBody.data?.username === "oldman_admin" ? [] : [`staff probe returned ${authenticated.status}`])
  ] };
})()
""",
        timeout=10.0,
    )
    result.pageErrors.extend(
        f"Auth/Session: {failure}" for failure in support.assertion_failures(guards)
    )

    lifecycle_path = "/examples/session/lifecycle"
    support.navigate(client, urllib.parse.urljoin(base_url, lifecycle_path))
    assert_example_page(client, support, lifecycle_path, "session", "lifecycle", result)
    lifecycle = client.evaluate(
        r"""
(() => ({ failures: [
  ...(document.querySelector("[data-example-session-lifecycle]") ? [] : ["Session snapshot was not rendered"]),
  ...(document.querySelector("[data-example-active-session-count]")?.textContent?.trim() === "1" ? [] : ["active Session count was not one"])
] }))()
""",
        timeout=5.0,
    )
    result.pageErrors.extend(
        f"Auth/Session: {failure}" for failure in support.assertion_failures(lifecycle)
    )

    for page, selector in (
        ("expiry", "[data-example-session-expire]"),
        ("revoke", "[data-example-session-revoke]"),
    ):
        path = f"/examples/session/{page}"
        support.navigate(client, urllib.parse.urljoin(base_url, path))
        assert_example_page(client, support, path, "session", page, result)
        bad_response_count = len(result.badResponses)
        console_error_count = len(result.consoleErrors)
        invalidated = client.evaluate(
            f"""
(async () => {{
  const form = document.querySelector({json.dumps(selector)});
  if (!(form instanceof HTMLFormElement)) return {{ failures: ["{page} Form was missing"] }};
  form.removeAttribute("data-om-confirm");
  form.requestSubmit();
  for (let index = 0; index < 350 && !document.querySelector(".swal2-popup.swal2-show"); index += 1) {{
    await new Promise((resolve) => setTimeout(resolve, 100));
  }}
  const popup = document.querySelector(".swal2-popup.swal2-show");
  return {{ failures: popup ? [] : ["{page} did not show Session invalidation"] }};
}})()
""",
            timeout=40.0,
        )
        result.badResponses[bad_response_count:] = [
            response
            for response in result.badResponses[bad_response_count:]
            if not (
                response.get("status") == 401
                and urllib.parse.urlparse(response.get("url", "")).path
                == "/user-notifications/topbar"
            )
        ]
        result.consoleErrors[console_error_count:] = [
            error
            for error in result.consoleErrors[console_error_count:]
            if "/user-notifications/topbar" not in error
        ]
        result.pageErrors.extend(
            f"Auth/Session: {failure}"
            for failure in support.assertion_failures(invalidated)
        )
        client.evaluate(
            "document.querySelector('.swal2-popup.swal2-show .swal2-confirm')?.click()",
            timeout=5.0,
        )
        error_count = len(result.pageErrors)
        support.wait_for_path(client, "/login", f"{page} Session invalidation", result)
        if len(result.pageErrors) != error_count:
            return
        support.login(
            client,
            base_url,
            os.environ.get("OLDMAN_ADMIN_USERNAME", DEFAULT_USERNAME),
            os.environ.get("OLDMAN_ADMIN_PASSWORD", DEFAULT_PASSWORD),
        )

    login_path = "/examples/auth/login"
    support.navigate(client, urllib.parse.urljoin(base_url, login_path))
    assert_example_page(client, support, login_path, "auth", "login", result)
    login_link = client.evaluate(
        "(() => ({ failures: document.querySelector('[data-example-auth-login-link][href=\"/logout\"]') ? [] : ['real login/logout link was missing'] }))()",
        timeout=5.0,
    )
    result.pageErrors.extend(
        f"Auth/Session: {failure}" for failure in support.assertion_failures(login_link)
    )


def assert_i18n_examples(client, support: ModuleType, base_url: str, result) -> None:
    """Verify one language choice reaches both the browser catalog and Jinja."""
    expected = {
        "en": ("Loading...", "Request failed", "No results found"),
        "zh-Hans": ("加载中...", "请求失败", "未找到结果"),
        "zh-Hant": ("載入中...", "請求失敗", "找不到結果"),
    }
    original_language = client.evaluate(
        "document.documentElement.lang || 'en'", timeout=5.0
    )
    browser_path = "/examples/i18n/browser"
    server_path = "/examples/i18n/server"

    for language, translations in expected.items():
        support.navigate(client, urllib.parse.urljoin(base_url, browser_path))
        assert_example_page(client, support, browser_path, "i18n", "browser", result)
        switched = client.evaluate(
            f"""
(async () => {{
  const language = {json.dumps(language)};
  const expected = {json.dumps(translations[0], ensure_ascii=False)};
  const option = document.querySelector(`[data-om-component="language-switcher"] [data-lang="${{language}}"]`);
  if (!(option instanceof HTMLButtonElement)) return {{ failures: [`missing language option ${{language}}`] }};
  option.click();
  for (let index = 0; index < 100; index += 1) {{
    const loading = document.querySelector("[data-example-i18n-browser-loading]")?.textContent?.trim();
    if (document.documentElement.lang === language && option.getAttribute("aria-pressed") === "true" && loading === expected) {{
      return {{ failures: [] }};
    }}
    await new Promise((resolve) => setTimeout(resolve, 50));
  }}
  return {{ failures: [`browser catalog did not switch to ${{language}}`] }};
}})()
""",
            timeout=8.0,
        )
        result.pageErrors.extend(
            f"i18n {language}: {failure}"
            for failure in support.assertion_failures(switched)
        )

        support.navigate(client, urllib.parse.urljoin(base_url, server_path))
        assert_example_page(client, support, server_path, "i18n", "server", result)
        server_parity = client.evaluate(
            f"""
(() => {{
  const expected = {json.dumps(list(translations), ensure_ascii=False)};
  const actual = [
    document.querySelector("[data-example-i18n-server-loading]")?.textContent?.trim(),
    document.querySelector("[data-example-i18n-server-failed]")?.textContent?.trim(),
    document.querySelector("[data-example-i18n-server-empty]")?.textContent?.trim()
  ];
  return {{ failures: actual.every((value, index) => value === expected[index]) ? [] : [`server values ${{JSON.stringify(actual)}} did not match ${{JSON.stringify(expected)}}`] }};
}})()
""",
            timeout=5.0,
        )
        result.pageErrors.extend(
            f"i18n {language}: {failure}"
            for failure in support.assertion_failures(server_parity)
        )
        support.click_and_assert_turbo(
            client,
            f'a[href="{browser_path}"]',
            browser_path,
            f"i18n {language} Turbo",
            result,
        )
        parity = client.evaluate(
            f"""
(() => {{
  const expected = {json.dumps(list(translations), ensure_ascii=False)};
  const actual = [
    document.querySelector("[data-example-i18n-browser-loading]")?.textContent?.trim(),
    document.querySelector("[data-example-i18n-browser-failed]")?.textContent?.trim(),
    document.querySelector("[data-example-i18n-browser-empty]")?.textContent?.trim()
  ];
  return {{ failures: actual.every((value, index) => value === expected[index]) ? [] : [`browser values ${{JSON.stringify(actual)}} did not match ${{JSON.stringify(expected)}}`] }};
}})()
""",
            timeout=5.0,
        )
        result.pageErrors.extend(
            f"i18n {language}: {failure}"
            for failure in support.assertion_failures(parity)
        )

    coverage_path = "/examples/i18n/coverage"
    support.navigate(client, urllib.parse.urljoin(base_url, coverage_path))
    assert_example_page(client, support, coverage_path, "i18n", "coverage", result)
    coverage = client.evaluate(
        "(() => ({ failures: document.querySelector('[data-example-i18n-coverage]') ? [] : ['translation coverage table was missing'] }))()",
        timeout=5.0,
    )
    result.pageErrors.extend(
        f"i18n coverage: {failure}" for failure in support.assertion_failures(coverage)
    )

    restore = original_language if original_language in expected else "en"
    client.evaluate(
        f"""
(async () => {{
  const option = document.querySelector(`[data-om-component="language-switcher"] [data-lang="{restore}"]`);
  option?.click();
  for (let index = 0; index < 100 && (document.documentElement.lang !== "{restore}" || option?.getAttribute("aria-pressed") !== "true"); index += 1) {{
    await new Promise((resolve) => setTimeout(resolve, 50));
  }}
  return true;
}})()
""",
        timeout=8.0,
    )


def assert_ui_reference_examples(
    client, support: ModuleType, base_url: str, result
) -> None:
    """Verify concrete UI pages, responsive layouts, and their live controls."""
    screenshot_root = Path(
        os.environ.get("OLDMAN_EPG_CHILD_SCREENSHOT_DIR", "/tmp")
    ).expanduser().resolve()
    pages = (
        "foundations",
        "typography",
        "buttons",
        "badges-avatars",
        "cards",
        "lists",
        "alerts",
        "progress-loading",
        "timeline",
        "navigation-shell",
        "tabs-disclosure",
        "dropdowns-overlays",
        "countdown",
        "gallery",
        "video",
        "system-states",
        "images",
        "icons",
    )
    inventory = client.evaluate(
        f"""
(async () => {{
  const failures = [];
  for (const page of {json.dumps(pages)}) {{
    const response = await fetch(`/examples/ui/${{page}}`, {{ headers: {{ Accept: "text/html" }} }});
    const body = await response.text();
    if (response.status !== 200) failures.push(`${{page}} returned ${{response.status}}`);
    if (!body.includes(`data-example-ui-reference="${{page}}"`)) failures.push(`${{page}} still uses the placeholder page`);
  }}
  return {{ failures }};
}})()
""",
        timeout=30.0,
    )
    result.pageErrors.extend(
        f"UI reference: {failure}"
        for failure in support.assertion_failures(inventory)
    )

    buttons_path = "/examples/ui/buttons"
    support.navigate(client, urllib.parse.urljoin(base_url, buttons_path))
    assert_example_page(client, support, buttons_path, "ui", "buttons", result)
    buttons = client.evaluate(
        """
(() => ({ failures: [
  document.querySelectorAll("[data-example-ui-reference='buttons'] .om-button").length < 30 ? "button variants are incomplete" : "",
  !document.querySelector(".om-button[aria-busy='true'] .om-button-spinner") ? "loading button is missing" : "",
  !document.querySelector(".om-button:disabled") ? "disabled button is missing" : ""
].filter(Boolean) }))()
""",
        timeout=5.0,
    )
    result.pageErrors.extend(
        f"UI buttons: {failure}"
        for failure in support.assertion_failures(buttons)
    )

    avatars_path = "/examples/ui/badges-avatars"
    support.navigate(client, urllib.parse.urljoin(base_url, avatars_path))
    assert_example_page(
        client, support, avatars_path, "ui", "badges-avatars", result
    )
    avatars = client.evaluate(
        """
(async () => {
  const root = document.querySelector("[data-om-component='avatar']");
  for (let index = 0; index < 40 && root?.dataset.omComponentState !== "mounted"; index += 1) {
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  return { failures: [
    root?.dataset.omAvatarState !== "fallback" ? "broken avatar did not show its text fallback" : "",
    root?.querySelector("[data-om-avatar-fallback]")?.hidden ? "avatar fallback remains hidden" : ""
  ].filter(Boolean) };
})()
""",
        timeout=5.0,
    )
    result.pageErrors.extend(
        f"UI avatar: {failure}"
        for failure in support.assertion_failures(avatars)
    )

    alerts_path = "/examples/ui/alerts"
    support.navigate(client, urllib.parse.urljoin(base_url, alerts_path))
    assert_example_page(client, support, alerts_path, "ui", "alerts", result)
    alerts = client.evaluate(
        """
(async () => {
  const root = document.querySelector("[data-om-component='alert']");
  for (let index = 0; index < 40 && root?.dataset.omComponentState !== "mounted"; index += 1) {
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  root?.querySelector("[data-om-alert-dismiss]")?.click();
  return { failures: root?.hidden ? [] : ["dismissible Alert did not close"] };
})()
""",
        timeout=5.0,
    )
    result.pageErrors.extend(
        f"UI alerts: {failure}"
        for failure in support.assertion_failures(alerts)
    )

    reference_checks = {
        "cards": "document.querySelectorAll('.om-card').length >= 9 && document.querySelector('.om-card-footer') && document.querySelector('.om-card-compact') && document.querySelector('.om-card-accent-danger')",
        "lists": "document.querySelectorAll('.om-list').length >= 4 && document.querySelector('.om-list-dense') && document.querySelector('[aria-current=\"page\"]') && document.querySelector('[aria-disabled=\"true\"]')",
        "progress-loading": "document.querySelectorAll('.om-progress').length >= 9 && document.querySelector('.om-progress-indeterminate') && document.querySelectorAll('.om-spinner').length >= 9",
        "timeline": "document.querySelectorAll('.om-timeline-item').length >= 8 && document.querySelector('.om-timeline-item[aria-current=\"step\"]')",
    }
    for page, expression in reference_checks.items():
        path = f"/examples/ui/{page}"
        support.navigate(client, urllib.parse.urljoin(base_url, path))
        assert_example_page(client, support, path, "ui", page, result)
        payload = client.evaluate(
            f"(() => ({{ failures: ({expression}) ? [] : ['required variants are missing'] }}))()",
            timeout=5.0,
        )
        result.pageErrors.extend(
            f"UI {page}: {failure}"
            for failure in support.assertion_failures(payload)
        )

    support.configure_viewport(client, 900, 900, mobile=False)
    typography_path = "/examples/ui/typography"
    support.navigate(client, urllib.parse.urljoin(base_url, typography_path))
    assert_example_page(
        client, support, typography_path, "ui", "typography", result
    )
    typography = client.evaluate(
        """
(() => ({ failures: [
  !document.querySelector("[data-example-ui-long-chinese]") ? "long Chinese sample is missing" : "",
  !document.querySelector("[data-example-ui-long-english]") ? "long English sample is missing" : "",
  document.documentElement.scrollWidth > window.innerWidth ? "medium layout overflows horizontally" : ""
].filter(Boolean) }))()
""",
        timeout=5.0,
    )
    result.pageErrors.extend(
        f"UI typography: {failure}"
        for failure in support.assertion_failures(typography)
    )
    support.capture_named_screenshot(
        client,
        result,
        "examples-medium-ui",
        str(screenshot_root / "oldman-examples-ui-medium.png"),
    )

    tabs_path = "/examples/ui/tabs-disclosure"
    support.navigate(client, urllib.parse.urljoin(base_url, tabs_path))
    assert_example_page(client, support, tabs_path, "ui", "tabs-disclosure", result)
    tabs = client.evaluate(
        """
(async () => {
  const failures = [];
  const root = document.querySelector("[data-example-ui-tabs]");
  for (let index = 0; index < 40 && root?.dataset.omComponentState !== "mounted"; index += 1) {
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  const tabItems = [...(root?.querySelectorAll("[data-om-tab]") ?? [])];
  tabItems[0]?.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
  if (tabItems[1]?.getAttribute("aria-selected") !== "true") failures.push("ArrowRight did not activate the next tab");
  tabItems[1]?.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
  if (tabItems[3]?.getAttribute("aria-selected") !== "true") failures.push("disabled tab was not skipped");
  tabItems[3]?.dispatchEvent(new KeyboardEvent("keydown", { key: "Home", bubbles: true }));
  if (tabItems[0]?.getAttribute("aria-selected") !== "true" || tabItems[0]?.tabIndex !== 0) failures.push("Home did not restore the first tab stop");
  if (!document.querySelector("#workspace-activity-panel")?.hidden) failures.push("inactive tab panel remains visible");

  const accordionItems = [...document.querySelectorAll("[data-example-ui-accordion] details")];
  accordionItems[1]?.querySelector("summary")?.click();
  if (!accordionItems[1]?.open || accordionItems[0]?.open) failures.push("native accordion did not keep one item open");
  const collapse = document.querySelector("[data-example-ui-collapse]");
  collapse?.querySelector("summary")?.click();
  if (!collapse?.open) failures.push("standalone collapse did not open");
  if (!document.querySelector(".om-step[aria-current='step']") || !document.querySelector(".om-step.is-error")) failures.push("stepper states are incomplete");
  return { failures };
})()
""",
        timeout=5.0,
    )
    result.pageErrors.extend(
        f"UI tabs and disclosure: {failure}"
        for failure in support.assertion_failures(tabs)
    )

    overlays_path = "/examples/ui/dropdowns-overlays"
    support.navigate(client, urllib.parse.urljoin(base_url, overlays_path))
    assert_example_page(client, support, overlays_path, "ui", "dropdowns-overlays", result)
    overlays = client.evaluate(
        """
(async () => {
  const failures = [];
  const waitForMount = async (selector) => {
    const root = document.querySelector(selector);
    for (let index = 0; index < 40 && root?.dataset.omComponentState !== "mounted"; index += 1) {
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
    return root;
  };

  const dropdown = await waitForMount("[data-example-dropdown]");
  dropdown?.querySelector("[data-om-dropdown-toggle]")?.click();
  const menu = dropdown?.querySelector("[data-om-dropdown-menu]");
  if (menu?.hidden || menu?.getAttribute("aria-hidden") !== "false") failures.push("dropdown did not open");
  if (menu) {
    const rect = menu.getBoundingClientRect();
    if (rect.left < 0 || rect.right > innerWidth || rect.top < 0 || rect.bottom > innerHeight) failures.push("dropdown left the viewport");
  }
  dropdown?.querySelector("[data-om-dropdown-toggle]")?.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  if (!menu?.hidden) failures.push("Escape did not close dropdown");

  const tooltip = await waitForMount("[data-om-component='tooltip']");
  const tooltipTrigger = tooltip?.querySelector("[data-om-tooltip-trigger]");
  tooltipTrigger?.focus();
  tooltipTrigger?.dispatchEvent(new FocusEvent("focus"));
  const tooltipContent = tooltip?.querySelector("[data-om-tooltip-content]");
  if (tooltipContent?.hidden || !tooltipTrigger?.getAttribute("aria-describedby")) failures.push("keyboard focus did not show tooltip");
  tooltipTrigger?.blur();
  tooltipTrigger?.dispatchEvent(new FocusEvent("blur"));
  if (!tooltipContent?.hidden) failures.push("tooltip remained open after blur");

  const popover = await waitForMount("[data-example-popover]");
  const popoverTrigger = popover?.querySelector("[data-om-popover-trigger]");
  popoverTrigger?.click();
  const popoverContent = popover?.querySelector("[data-om-popover-content]");
  if (popoverContent?.hidden || popoverTrigger?.getAttribute("aria-expanded") !== "true") failures.push("popover did not open");
  document.body.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  if (!popoverContent?.hidden) failures.push("outside click did not close popover");

  document.querySelector("[data-om-modal-target='#example-drawer-end']")?.click();
  await new Promise((resolve) => setTimeout(resolve, 220));
  const drawer = document.querySelector("#example-drawer-end");
  if (drawer?.hidden || drawer?.dataset.omState !== "open") failures.push("end Drawer did not open");
  drawer?.querySelector("[data-om-modal-target='#nested-overlay-modal']")?.click();
  await new Promise((resolve) => setTimeout(resolve, 220));
  const nested = document.querySelector("#nested-overlay-modal");
  document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  await new Promise((resolve) => setTimeout(resolve, 220));
  if (!nested?.hidden) failures.push("Escape did not close nested Modal");
  if (drawer?.hidden) failures.push("nested Escape also closed Drawer");
  document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  await new Promise((resolve) => setTimeout(resolve, 220));
  if (!drawer?.hidden || document.body.classList.contains("om-modal-open")) failures.push("Drawer did not release its overlay state");
  return { failures };
})()
""",
        timeout=8.0,
    )
    result.pageErrors.extend(
        f"UI dropdowns and overlays: {failure}"
        for failure in support.assertion_failures(overlays)
    )
    support.capture_named_screenshot(
        client,
        result,
        "examples-dropdowns-overlays",
        str(screenshot_root / "oldman-examples-dropdowns-overlays.png"),
    )

    countdown_path = "/examples/ui/countdown"
    support.navigate(client, urllib.parse.urljoin(base_url, countdown_path))
    assert_example_page(client, support, countdown_path, "ui", "countdown", result)
    countdown = client.evaluate(
        """
(async () => {
  const root = document.querySelector("[data-example-countdown]");
  for (let index = 0; index < 40 && root?.dataset.omComponentState !== "mounted"; index += 1) {
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  const before = root?.querySelector("[data-om-countdown-seconds]")?.textContent;
  await new Promise((resolve) => setTimeout(resolve, 1100));
  const after = root?.querySelector("[data-om-countdown-seconds]")?.textContent;
  return { failures: [
    root?.dataset.omComponentState !== "mounted" ? "countdown did not mount" : "",
    root?.dataset.omCountdownState !== "running" ? "countdown is not running" : "",
    before === after ? "countdown did not advance" : ""
  ].filter(Boolean) };
})()
""",
        timeout=5.0,
    )
    result.pageErrors.extend(
        f"UI countdown: {failure}"
        for failure in support.assertion_failures(countdown)
    )

    support.configure_viewport(client, 1200, 900, mobile=False)
    gallery_path = "/examples/ui/gallery"
    support.navigate(client, urllib.parse.urljoin(base_url, gallery_path))
    assert_example_page(client, support, gallery_path, "ui", "gallery", result)
    gallery = client.evaluate(
        """
(async () => {
  const failures = [];
  const root = document.querySelector("[data-example-ui-reference='gallery']");
  for (let index = 0; index < 40 && root?.dataset.omComponentState !== "mounted"; index += 1) {
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  root?.querySelector("[data-om-gallery-index='2']")?.click();
  const modal = document.querySelector("#example-gallery-modal");
  const carousel = modal?.querySelector("[data-om-gallery-carousel]");
  for (let index = 0; index < 40 && (modal?.dataset.omState !== "open" || carousel?.dataset.omCarouselState !== "ready"); index += 1) {
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  if (modal?.dataset.omState !== "open") failures.push("gallery Modal did not open");
  if (!modal?.querySelector("[data-example-gallery-slide='2'].swiper-slide-active")) failures.push("gallery did not open at the selected item");
  modal?.querySelector("[data-om-carousel-next]")?.click();
  await new Promise((resolve) => setTimeout(resolve, 350));
  if (!modal?.querySelector("[data-example-gallery-slide='3'].swiper-slide-active")) failures.push("gallery next control did not advance");
  const brokenFallback = root?.querySelector("[data-om-gallery-index='7'] [data-om-gallery-fallback]");
  for (let index = 0; index < 20 && brokenFallback?.hidden; index += 1) {
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  if (brokenFallback?.hidden) failures.push("broken gallery image did not show its fallback");
  return { failures };
})()
""",
        timeout=8.0,
    )
    result.pageErrors.extend(
        f"UI gallery: {failure}"
        for failure in support.assertion_failures(gallery)
    )
    support.capture_named_screenshot(
        client,
        result,
        "examples-gallery",
        str(screenshot_root / "oldman-examples-gallery.png"),
    )
    client.evaluate(
        "document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))"
    )

    video_path = "/examples/ui/video"
    support.navigate(client, urllib.parse.urljoin(base_url, video_path))
    assert_example_page(client, support, video_path, "ui", "video", result)
    video = client.evaluate(
        """
(async () => {
  const element = document.querySelector("[data-example-video]");
  for (let index = 0; index < 40 && element?.readyState < 1; index += 1) {
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  const frame = document.querySelector("[data-example-video-frame]")?.getBoundingClientRect();
  const ratio = frame && frame.height ? frame.width / frame.height : 0;
  return { failures: [
    !element?.controls ? "native video controls are missing" : "",
    !element?.currentSrc.endsWith("dashboard-demo.webm") ? "video source did not load" : "",
    !element?.canPlayType("video/webm") ? "browser cannot play the WebM example" : "",
    Math.abs(ratio - (16 / 9)) > 0.03 ? "video frame is not 16:9" : ""
  ].filter(Boolean) };
})()
""",
        timeout=5.0,
    )
    result.pageErrors.extend(
        f"UI video: {failure}"
        for failure in support.assertion_failures(video)
    )

    support.configure_viewport(client, 390, 844, mobile=True)
    icons_path = "/examples/ui/icons"
    support.navigate(client, urllib.parse.urljoin(base_url, icons_path))
    assert_example_page(client, support, icons_path, "ui", "icons", result)
    client.command(
        "Browser.grantPermissions",
        {
            "origin": base_url.rstrip("/"),
            "permissions": ["clipboardReadWrite", "clipboardSanitizedWrite"],
        },
    )
    icons = client.evaluate(
        """
(async () => {
  const failures = [];
  const root = document.querySelector("[data-om-component='icon-catalog']");
  const search = root?.querySelector("[data-example-icon-search]");
  for (let index = 0; index < 50 && root?.dataset.omComponentState !== "mounted"; index += 1) {
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  if (root?.dataset.omComponentState !== "mounted") return { failures: ["icon catalog did not mount"] };
  const libraryLinks = [...document.querySelectorAll("[data-example-icon-library-link]")].map((link) => link.href);
  const expectedLibraries = ["https://remixicon.com/", "https://pictogrammers.com/library/mdi/", "https://v2.boxicons.com/"];
  if (JSON.stringify(libraryLinks) !== JSON.stringify(expectedLibraries)) failures.push("icon library links are incomplete");
  if (!document.querySelector("[data-example-ui-reference='icon-usage']")) failures.push("icon usage instructions are missing");
  search.value = "mdi-alert";
  search.dispatchEvent(new Event("input", { bubbles: true }));
  const visible = [...root.querySelectorAll("[data-example-icon-item]")].filter((item) => !item.hidden);
  if (visible.length !== 1 || visible[0].dataset.iconClass !== "mdi-alert-outline") failures.push("icon search returned the wrong item");
  if (document.documentElement.scrollWidth > window.innerWidth) failures.push("mobile icon grid overflows horizontally");
  return { failures };
})()
""",
        timeout=8.0,
    )
    result.pageErrors.extend(
        f"UI icons: {failure}" for failure in support.assertion_failures(icons)
    )
    client.command("Page.bringToFront")
    client.command(
        "Runtime.evaluate",
        {
            "expression": "document.querySelector('[data-example-icon-item]:not([hidden])')?.click()",
            "userGesture": True,
        },
    )
    copied = client.evaluate(
        """
(async () => {
  const status = document.querySelector("[data-example-icon-status]");
  for (let index = 0; index < 50 && !status?.textContent?.includes("mdi-alert-outline"); index += 1) {
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  return { failures: status?.textContent?.includes("mdi-alert-outline") ? [] : ["icon class was not copied"] };
})()
""",
        timeout=5.0,
    )
    result.pageErrors.extend(
        f"UI icons: {failure}" for failure in support.assertion_failures(copied)
    )
    support.capture_named_screenshot(
        client,
        result,
        "examples-mobile-ui",
        str(screenshot_root / "oldman-examples-ui-mobile.png"),
    )

    support.configure_viewport(client, 1440, 1000, mobile=False)
    support.navigate(client, urllib.parse.urljoin(base_url, icons_path))
    assert_example_page(client, support, icons_path, "ui", "icons", result)


def assert_plugin_index(client, support: ModuleType, base_url: str, result) -> None:
    """Verify the plugin inventory, real example links, and isolated live effects."""
    plugins_path = "/examples/plugins"
    support.navigate(client, urllib.parse.urljoin(base_url, plugins_path))
    assert_example_page(client, support, plugins_path, "plugins", "index", result)
    payload = client.evaluate(
        r"""
(async () => {
  const failures = [];
  const expectedSupported = [
    "Turbo", "Stimulus", "Axios", "TanStack Table", "ApexCharts", "Choices", "Cleave",
    "Flatpickr", "noUiSlider + wNumb", "Pickr", "Quill", "SimpleBar", "Swiper", "Toastify",
    "SweetAlert2", "Waves", "SortableJS", "ri / mdi / bx"
  ];
  const root = document.querySelector("[data-example-plugin-index]");
  if (!root) return { failures: ["plugin index still uses the placeholder page"] };

  const supported = [...root.querySelectorAll('[data-plugin-status="supported"]')];
  const planned = [...root.querySelectorAll('[data-plugin-status="planned"]')];
  const supportedNames = supported.map((item) => item.getAttribute("data-plugin-name"));
  if (JSON.stringify(supportedNames) !== JSON.stringify(expectedSupported)) failures.push("supported plugin inventory drifted");
  if (planned.length !== 3) failures.push("planned plugin inventory drifted");
  if (planned.some((item) => item.querySelector("[data-plugin-example]"))) failures.push("an unimplemented plugin links to a false example");

  for (const item of supported) {
    const link = item.querySelector("[data-plugin-example]");
    if (!(link instanceof HTMLAnchorElement)) {
      failures.push(`${item.getAttribute("data-plugin-name")} has no working example link`);
      continue;
    }
    const response = await fetch(link.pathname, { headers: { Accept: "text/html" } });
    const body = await response.text();
    if (response.status !== 200 || !body.includes("data-examples-shell")) {
      failures.push(`${item.getAttribute("data-plugin-name")} example returned ${response.status}`);
    }
  }

  const simplebar = root.querySelector("[data-example-plugin-simplebar]");
  for (let index = 0; index < 50 && simplebar?.dataset.omComponentState !== "mounted"; index += 1) {
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  if (simplebar?.dataset.omComponentState !== "mounted" || !simplebar.querySelector(".simplebar-wrapper")) {
    failures.push("SimpleBar example did not mount");
  }
  if (root.querySelectorAll("[data-om-component]").length !== 1) failures.push("plugin index initialized more than its isolated SimpleBar example");

  const waves = root.querySelector("[data-example-plugin-waves]");
  if (!(waves instanceof HTMLButtonElement)) {
    failures.push("Waves example button is missing");
  } else {
    const rect = waves.getBoundingClientRect();
    waves.dispatchEvent(new MouseEvent("mousedown", {
      bubbles: true,
      button: 0,
      clientX: rect.left + rect.width / 2,
      clientY: rect.top + rect.height / 2
    }));
    await new Promise((resolve) => setTimeout(resolve, 50));
    if (!waves.querySelector(".waves-ripple")) failures.push("Waves press effect did not run");
    waves.dispatchEvent(new MouseEvent("mouseup", { bubbles: true, button: 0 }));
  }
  return { failures };
})()
""",
        timeout=30.0,
    )
    result.pageErrors.extend(
        f"Plugins: {failure}" for failure in support.assertion_failures(payload)
    )


def verify_examples(base_url: str, result, support: ModuleType) -> None:
    """Open representative shells and prove repeated Turbo navigation."""
    screenshot_root = (
        Path(os.environ.get("OLDMAN_EPG_CHILD_SCREENSHOT_DIR", "/tmp"))
        .expanduser()
        .resolve()
    )
    result.desktopScreenshot = str(screenshot_root / "oldman-examples-desktop.png")
    result.mobileScreenshot = str(screenshot_root / "oldman-examples-mobile.png")

    port = support.find_free_port()
    user_data_dir = tempfile.mkdtemp(prefix="oldman-examples-chrome-")
    chrome = support.launch_chrome(port, user_data_dir)
    client = None
    try:
        support.wait_for_chrome_devtools(chrome, port)
        client = support.CDPClient(support.create_page_websocket(port), result)
        for domain in ("Page", "Runtime", "Network", "Log"):
            client.command(f"{domain}.enable")

        support.configure_viewport(client, 1440, 1000, mobile=False)
        support.login(
            client,
            base_url,
            os.environ.get("OLDMAN_ADMIN_USERNAME", DEFAULT_USERNAME),
            os.environ.get("OLDMAN_ADMIN_PASSWORD", DEFAULT_PASSWORD),
        )

        tables_path = "/examples/tables/static"
        support.navigate(client, urllib.parse.urljoin(base_url, tables_path))
        assert_example_page(client, support, tables_path, "tables", "static", result)
        assert_all_example_routes(client, support, result)
        support.capture_named_screenshot(
            client, result, "examples-desktop-tables", result.desktopScreenshot
        )
        assert_project_table(client, support, base_url, result, data_format="html")
        assert_project_table(client, support, base_url, result, data_format="json")
        assert_realtime_table(client, support, base_url, result)

        forms_path = "/examples/forms/basics"
        support.click_and_assert_turbo(
            client, f'a[href="{forms_path}"]', forms_path, "examples forms", result
        )
        assert_example_page(client, support, forms_path, "forms", "basics", result)
        support.capture_named_screenshot(
            client, result, "examples-desktop-forms", "/tmp/oldman-examples-forms.png"
        )
        assert_slug_form(client, support, base_url, result)
        assert_input_spinner_form(client, support, base_url, result)
        assert_tags_form(client, support, base_url, result)
        assert_color_picker_form(client, support, base_url, result)
        assert_rich_text_form(client, support, base_url, result)
        assert_multi_step_form(client, support, base_url, result)

        json_list_path = "/examples/forms/json-list"
        support.click_and_assert_turbo(
            client,
            f'a[href="{json_list_path}"]',
            json_list_path,
            "examples JSON list",
            result,
        )
        assert_example_page(
            client, support, json_list_path, "forms", "json-list", result
        )
        assert_json_list_form(client, support, base_url, result)

        assert_remote_select(client, support, base_url, result)
        assert_remote_autocomplete(client, support, base_url, result)
        assert_database_list(client, support, base_url, result)
        assert_storage_lifecycle(client, support, base_url, result)
        providers_path = "/examples/data-inputs/providers"
        support.navigate(client, urllib.parse.urljoin(base_url, providers_path))
        assert_example_page(
            client, support, providers_path, "data-inputs", "providers", result
        )

        containers_path = "/examples/forms/containers"
        support.click_and_assert_turbo(
            client,
            f'a[href="{containers_path}"]',
            containers_path,
            "examples Form containers",
            result,
        )
        assert_example_page(
            client, support, containers_path, "forms", "containers", result
        )
        assert_remote_modal_form(client, support, result)
        assert_modal_and_action_examples(client, support, base_url, result)
        assert_message_and_feedback_examples(client, support, base_url, result)
        assert_navigation_examples(client, support, base_url, result)
        assert_sortable_workflow(client, support, base_url, result)
        assert_chart_examples(client, support, base_url, result)
        assert_notification_examples(client, support, base_url, result)
        assert_auth_session_examples(client, support, base_url, result)
        assert_i18n_examples(client, support, base_url, result)

        assert_ui_reference_examples(client, support, base_url, result)
        support.capture_named_screenshot(
            client, result, "examples-desktop-ui", "/tmp/oldman-examples-ui.png"
        )

        assert_plugin_index(client, support, base_url, result)
        plugins_path = "/examples/plugins"
        support.configure_viewport(client, 390, 844, mobile=True)
        support.navigate(client, urllib.parse.urljoin(base_url, plugins_path))
        assert_example_page(client, support, plugins_path, "plugins", "index", result)
        support.capture_named_screenshot(
            client, result, "examples-mobile-plugins", result.mobileScreenshot
        )
        client.pump(0.5)
    finally:
        if client is not None:
            client.close()
        chrome.terminate()
        try:
            chrome.wait(timeout=3)
        except subprocess.TimeoutExpired:
            chrome.kill()
            chrome.wait(timeout=3)
        shutil.rmtree(user_data_dir, ignore_errors=True)


def main() -> int:
    """Print the browser child contract consumed by the owner wrapper."""
    support = load_browser_support()
    result = support.VerificationResult()
    try:
        verify_examples(
            os.environ.get("OLDMAN_DASHBOARD_URL", DEFAULT_URL), result, support
        )
    except Exception as exc:  # noqa: BLE001 - browser failures must remain structured.
        result.pageErrors.append(str(exc))
    result.ok = (
        not result.consoleErrors and not result.pageErrors and not result.badResponses
    )
    print(json.dumps(result.as_json(), ensure_ascii=False, indent=2), flush=True)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
