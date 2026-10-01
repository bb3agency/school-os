// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";
import en from "../../messages/en.json";
import enTeluguOff from "../../messages/en.telugu-off.json";
import te from "../../messages/te.json";
import { GET as teluguFont } from "@/app/fonts/telugu/[file]/route";
import { flattenMessages, icuSignature } from "@/test/icu";
import {
  enabledLocales,
  isEnabledLocale,
  legacyLocalePath,
  teluguEnabled,
  uiLocale,
  withoutLocalePrefix,
} from "./languages";
import { loadMessages, mergeMessages } from "./messages";
import { TELUGU_FONT_FILES, TELUGU_STYLESHEET, teluguStylesheet } from "./telugu-font";

const ON = { SOS_TELUGU_ENABLED: "true" };
const TELUGU_SCRIPT = /[ఀ-౿]/;

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("SOS_TELUGU_ENABLED: one switch, off by default (ADR-0036)", () => {
  it("is off unless set to a true value", () => {
    for (const value of [undefined, "", "false", "0", "no", "off", "nope"]) {
      expect(teluguEnabled({ SOS_TELUGU_ENABLED: value }), String(value)).toBe(false);
    }
    for (const value of ["true", "TRUE", "1", "yes", "on", " True "]) {
      expect(teluguEnabled({ SOS_TELUGU_ENABLED: value }), value).toBe(true);
    }
  });

  it("reads the environment at call time (no rebuild needed)", () => {
    expect(teluguEnabled()).toBe(false);
    vi.stubEnv("SOS_TELUGU_ENABLED", "true");
    expect(teluguEnabled()).toBe(true);
  });

  it("offers English only while off, English first when on", () => {
    expect(enabledLocales({})).toEqual(["en"]);
    expect(enabledLocales(ON)).toEqual(["en", "te"]);
    expect(isEnabledLocale("te", {})).toBe(false);
    expect(isEnabledLocale("te", ON)).toBe(true);
    expect(isEnabledLocale("fr", ON)).toBe(false);
    expect(uiLocale("te", {})).toBe("en");
    expect(uiLocale("te", ON)).toBe("te");
    expect(uiLocale(undefined, ON)).toBe("en");
  });
});

describe("no locale in any URL (product owner 2026-09-30, ADR-0036 note)", () => {
  it("maps old /en and /te paths to the same path without the prefix", () => {
    expect(legacyLocalePath("/te")).toEqual({ locale: "te", pathname: "/" });
    expect(legacyLocalePath("/en/")).toEqual({ locale: "en", pathname: "/" });
    expect(legacyLocalePath("/te/students/1")).toEqual({ locale: "te", pathname: "/students/1" });
    expect(legacyLocalePath("/EN/platform")).toEqual({ locale: "en", pathname: "/platform" });
  });

  it("leaves prefix-less paths alone", () => {
    for (const path of [
      "/",
      "/teachers",
      "/entries",
      "/en-in/x",
      "/students/en",
      "/students/te/x",
    ]) {
      expect(legacyLocalePath(path), path).toBeNull();
    }
  });

  it("never produces a protocol-relative path", () => {
    expect(legacyLocalePath("/en//evil.example")).toEqual({
      locale: "en",
      pathname: "/evil.example",
    });
    expect(legacyLocalePath("/te///evil.example/x")?.pathname).toBe("/evil.example/x");
    expect(legacyLocalePath("/en/\\evil.example")?.pathname).toBe("/evil.example");
  });

  it("strips the prefix from a return address and keeps its query and hash", () => {
    expect(withoutLocalePrefix("/en/settings/users?page=2#top")).toBe("/settings/users?page=2#top");
    expect(withoutLocalePrefix("/te?x=1")).toBe("/?x=1");
    expect(withoutLocalePrefix("/students?next=/en/x")).toBe("/students?next=/en/x");
    expect(withoutLocalePrefix("/")).toBe("/");
  });
});

describe("message loader (ADR-0036)", () => {
  it("never returns Telugu while off", async () => {
    for (const requested of ["te", "en", undefined, "fr"]) {
      const { locale, messages } = await loadMessages(requested, false);
      expect(locale, String(requested)).toBe("en");
      expect(messages.language.label).toBe(en.language.label);
      expect(messages.notices.description).toBe(enTeluguOff.notices.description);
    }
  });

  it("returns the shipped catalogs unchanged when on", async () => {
    expect(await loadMessages("te", true)).toEqual({ locale: "te", messages: te });
    expect(await loadMessages("en", true)).toEqual({ locale: "en", messages: en });
  });

  it("the off catalog mentions Telugu only in keys shown with the switch on", async () => {
    const { messages } = await loadMessages("en", false);
    // Labels of Telugu fields, options and the switcher: screens render them only when on.
    const onlyWhenOn = [
      /^language\.te$/,
      /\.telugu$/,
      /\.te$/,
      /\.mixed$/,
      /_te$/,
      /\.teField$/,
      /\.summaryTe$/,
      /^validation\.bothLanguages$/,
      /^platform\.announcements\.bothLanguagesHint$/,
    ];
    const offenders = [...flattenMessages(messages).entries()]
      .filter(([, value]) => /telugu/i.test(value) || TELUGU_SCRIPT.test(value))
      .map(([key]) => key)
      .filter((key) => !onlyWhenOn.some((pattern) => pattern.test(key)));
    expect(offenders).toEqual([]);
  });

  it("every English-only override replaces an existing key with the same ICU arguments", () => {
    const base = flattenMessages(en);
    for (const [key, value] of flattenMessages(enTeluguOff)) {
      expect(base.has(key), key).toBe(true);
      expect(icuSignature(value), key).toEqual(icuSignature(base.get(key) ?? ""));
      expect(value, key).not.toMatch(/telugu/i);
    }
  });

  it("mergeMessages replaces leaves only and leaves the base untouched", () => {
    const base = { a: { b: "1", c: "2" }, d: "3" };
    expect(mergeMessages(base, { a: { c: "x" } })).toEqual({ a: { b: "1", c: "x" }, d: "3" });
    expect(base.a.c).toBe("2");
  });
});

describe("Telugu font route (ADR-0036)", () => {
  const get = (file: string) =>
    teluguFont(new Request(`https://office.school.example/fonts/telugu/${file}`), {
      params: Promise.resolve({ file }),
    });

  it("answers 404 for the stylesheet and every font file while off", async () => {
    for (const file of [TELUGU_STYLESHEET, ...TELUGU_FONT_FILES]) {
      expect((await get(file)).status, file).toBe(404);
    }
  });

  it("serves the stylesheet and the Telugu font files when on, nothing else", async () => {
    vi.stubEnv("SOS_TELUGU_ENABLED", "true");
    const css = await get(TELUGU_STYLESHEET);
    expect(css.status).toBe(200);
    expect(css.headers.get("content-type")).toContain("text/css");
    expect(await css.text()).toBe(teluguStylesheet());
    const font = await get(TELUGU_FONT_FILES[0] ?? "");
    expect(font.status).toBe(200);
    expect(font.headers.get("content-type")).toBe("font/woff2");
    expect((await font.arrayBuffer()).byteLength).toBeGreaterThan(10_000);
    for (const file of ["../../package.json", "noto-sans-telugu-latin-400-normal.woff2", "x.css"]) {
      expect((await get(file)).status, file).toBe(404);
    }
  });

  it("the stylesheet names every font file it serves", () => {
    const css = teluguStylesheet();
    for (const file of TELUGU_FONT_FILES) expect(css).toContain(`/fonts/telugu/${file}`);
    expect(css).toContain("--font-sans");
  });
});
