// Test fixture for `semgrep --test .semgrep`. Never compiled or bundled.
declare const accessToken: string;
declare const res: { id_token: string; refreshToken: string };
declare const locale: string;

// ruleid: sos-web-no-token-in-web-storage
localStorage.setItem("access_token", accessToken);

// ruleid: sos-web-no-token-in-web-storage
window.sessionStorage.setItem("sos.session", "abc");

// ruleid: sos-web-no-token-in-web-storage
sessionStorage.setItem("x", res.id_token);

// ruleid: sos-web-no-token-in-web-storage
localStorage["jwt"] = accessToken;

// ruleid: sos-web-no-token-in-web-storage
localStorage.authToken = res.refreshToken;

// ruleid: sos-web-no-token-in-web-storage
globalThis.localStorage.setItem("refresh", "r");

// ok: sos-web-no-token-in-web-storage
localStorage.setItem("sos.locale", locale);

// ok: sos-web-no-token-in-web-storage
sessionStorage.setItem("sos.sidebar.collapsed", "true");

// ok: sos-web-no-token-in-web-storage
const cache = new Map<string, string>();
cache.set("access_token", accessToken);
