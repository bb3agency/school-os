// Test fixture for `semgrep --test .semgrep`. Never compiled or bundled.
import { createElement } from "react";

export function SelfClosing({ html }: { html: string }) {
  // ruleid: sos-web-no-dangerously-set-inner-html
  return <div dangerouslySetInnerHTML={{ __html: html }} />;
}

export function WithClosingTag({ text }: { text: string }) {
  // ruleid: sos-web-no-dangerously-set-inner-html
  return <p className="answer" dangerouslySetInnerHTML={{ __html: text }}></p>;
}

export function Safe({ text }: { text: string }) {
  // ok: sos-web-no-dangerously-set-inner-html
  return <p className="answer">{text}</p>;
}

export function viaCreateElement(html: string) {
  // ruleid: sos-web-no-dangerously-set-inner-html
  return createElement("div", { className: "x", dangerouslySetInnerHTML: { __html: html } });
}

export function spreadProps(html: string) {
  // ruleid: sos-web-no-dangerously-set-inner-html
  const props = { dangerouslySetInnerHTML: { __html: html } };
  return <div {...props} />;
}

export function safeCreateElement(text: string) {
  // ok: sos-web-no-dangerously-set-inner-html
  return createElement("div", { className: "x" }, text);
}
