# download skill

`fetch` (BLUE `download`) saves a URL into `D:\Personal-Agent\downloads\`.

- Same URL policy as the browser: http(s) only, never this PC's loopback
  services — also checked again after redirects.
- Size cap (`max_mb`, default 500); the partial `.part` file is removed if exceeded.
- Safe file names; an existing file is never overwritten (`name (1).ext`).
- Proof (plan §54): the file exists, its size and SHA-256 are returned.
- Downloaded files are untrusted data (plan §19) — opening or running them is a
  separate, permission-gated action.
