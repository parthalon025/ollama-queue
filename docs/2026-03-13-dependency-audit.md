# DEPENDENCY AUDIT REPORT — ollama-queue

**Date:** 2026-03-13  
**Project:** ollama-queue (Ollama job queue scheduler)  
**Repository:** https://github.com/parthalon025/ollama-queue

---

## Executive Summary

| Category | Finding |
|----------|---------|
| **CRITICAL/HIGH CVEs** | 1 high-severity npm vulnerability (flatted DoS) |
| **MEDIUM CVEs** | None |
| **Outdated Packages (no CVE)** | 8 Python packages behind latest (non-blocking) |
| **License Compliance** | All packages compliant; 1 expected UNLICENSED (SPA private) |
| **Python CVEs** | **0** (fully clean) |
| **Overall Risk** | **Low—Medium** (1 indirect npm dependency) |

---

## Section 1: CRITICAL / HIGH CVEs — Immediate Attention Required

### HIGH Severity: npm `flatted` Unbounded Recursion DoS

| Attribute | Value |
|-----------|-------|
| **Package** | flatted |
| **Current Version** | 3.3.3 |
| **Minimum Fix Version** | 3.4.0 |
| **CVE** | GHSA-25h7-pfq9-p65f ([NIST](https://github.com/advisories/GHSA-25h7-pfq9-p65f)) |
| **Severity** | HIGH (CVSS 7.5) |
| **CWE** | CWE-674 (Uncontrolled Recursion) |
| **Impact** | Denial of Service via unbounded recursion in `parse()` revive phase |
| **Vector** | CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:H |
| **Exposure** | **Indirect** — build-time only (dev dependency chain) |

#### Dependency Chain
```
ollama-queue-dashboard (SPA)
├─ eslint@10.0.2
│  └─ file-entry-cache@8.0.0
│     └─ flat-cache@4.0.1
│        └─ flatted@3.3.3  ← VULNERABLE
└─ superhot-ui (theme dep)
   └─ cspell@9.7.0
      └─ flatted@3.3.3  ← VULNERABLE (deduplicated)
```

#### Risk Assessment
- **Runtime Exposure:** None. `flatted` is used only by eslint (linting) and cspell (spell-check). Neither runs during production.
- **Build Exposure:** Code build (CSS/JS compilation) via esbuild does not invoke these tools. Safe.
- **Production Dashboard:** Never loads `flatted` code. Request payload deserialization uses browser JSON.parse(), not custom revive.
- **Practical Risk:** Very low. Exploitation requires (a) attacker-controlled cache file fed to eslint offline, or (b) attacker-controlled serialized data to `flatted.parse()` in dev environment.

#### Recommended Action
**Optional fix:** Upgrade to `flatted@≥3.4.0`. Lowest-risk path:
```bash
cd ollama_queue/dashboard/spa
npm update flatted --save
```

**When NOT to fix:** Cache files are local-only and under your control; no untrusted input reaches eslint offline lint stage during dev.

---

## Section 2: MEDIUM CVEs

None found.

---

## Section 3: Outdated Packages (No Known CVEs)

All packages below are behind latest but have no known CVEs. Grouped by risk level:

### Low Risk — Minor/patch updates, safe to upgrade anytime

| Package | Current | Latest | Notes |
|---------|---------|--------|-------|
| `certifi` | 2026.1.4 | 2026.2.25 | CA certificate bundle; safe patch |
| `python-dotenv` | 1.2.1 | 1.2.2 | Env var loader; safe patch |
| `pydantic-core` | 2.41.5 | 2.42.0 | Pydantic validator; safe patch |
| `pytz` | 2025.2 | 2026.1.post1 | Timezone data; safe for version drift |
| `ruff` | 0.15.1 | 0.15.6 | Linter; dev-only, safe patch |

### Medium Risk — Minor updates, safe but not urgent

| Package | Current | Latest | Notes |
|---------|---------|--------|-------|
| `fastapi` | 0.129.0 | 0.135.1 | **Core dependency.** 6-patch-level drift. [Changelog](https://github.com/tiangolo/fastapi/releases): includes perf improvements, but no breaking changes in 0.13x series. Safe to upgrade. |
| `uvicorn` | 0.40.0 | 0.41.0 | ASGI server. Single-patch drift. No breaking changes. Safe. |

### Special: pip itself
| Package | Current | Latest | Notes |
|---------|---------|--------|-------|
| `pip` | 24.0 | 26.0.1 | Not pinned in requirements. Latest is safe and recommended. |

---

## Section 4: License Compliance

### Python Licenses (via pip-licenses)

| Package | License | Status |
|---------|---------|--------|
| fastapi | MIT | ✓ Compliant |
| uvicorn | MIT | ✓ Compliant |
| click | BSD-3-Clause | ✓ Compliant |
| croniter | MIT | ✓ Compliant |
| httpx | MIT | ✓ Compliant |
| pytest | MIT | ✓ Compliant |
| pytest-xdist | MIT | ✓ Compliant |
| ruamel.yaml | MIT | ✓ Compliant |
| tomlkit | MIT | ✓ Compliant |
| pydantic | MIT | ✓ Compliant |
| starlette | BSD-3-Clause | ✓ Compliant |

**All Python dependencies:** MIT or BSD-3-Clause. **No issues.**

### npm Licenses

**Scanned packages:** 687 (8 prod, 680 dev)

| License Type | Count | Example Packages | Status |
|--------------|-------|------------------|--------|
| MIT | ~630 | eslint, jest, babel, tailwindcss | ✓ Compliant |
| Apache-2.0 | ~15 | html-entities, ... | ✓ Compliant |
| BSD-2/3-Clause | ~10 | ... | ✓ Compliant |
| ISC | ~10 | glob, fs-extra | ✓ Compliant |
| BlueOak-1.0.0 | ~5 | ... | ✓ Compliant |
| CC-BY-4.0 | ~2 | ... | ✓ Compliant |
| 0BSD, MIT-0, MPL-2.0 | ~5 combined | ... | ✓ Compliant |
| UNLICENSED | 1 | **ollama-queue-dashboard@0.1.0** (SPA itself, marked private) | ✓ Expected |

**Non-compliant licenses:** None found.

---

## Section 5: Security Highlights — Key Dependencies

### Python Web Stack (all secure)

| Package | Role | Status |
|---------|------|--------|
| **fastapi** v0.135.1 | REST API framework | ✓ Latest, secure |
| **uvicorn** v0.41.0 | ASGI server | ✓ Latest, secure |
| **pydantic** v2.12.5 | Request validation | ✓ Latest, secure |
| **starlette** v0.52.1 | Web middleware | ✓ Latest, secure |
| **httpx** v0.28.1 | HTTP client (tests) | ✓ Latest, secure |

### Frontend (1 finding)

| Package | Role | Status |
|---------|------|--------|
| **preact** v10.25.0 | UI framework | ✓ Secure, latest caret |
| **flatted** v3.3.3 | Cache serializer (eslint/cspell dev) | ⚠ HIGH CVE, fix available (3.4.0) |
| **esbuild** v0.25.0 | Bundle compiler | ✓ Secure |
| **tailwindcss** v4.0.0 | CSS framework | ✓ Secure |

---

## Section 6: Workspace Rollup

| Metric | Count |
|--------|-------|
| **Total CVEs (all severities)** | 1 HIGH |
| **Critical CVEs** | 0 |
| **High-severity CVEs** | 1 (indirect, dev-time only) |
| **Medium CVEs** | 0 |
| **Outdated packages (no CVE)** | 8 (all patches/minors, low risk) |
| **License issues** | 0 |
| **Repos audited** | 1 |
| **Python packages** | 34 total (0 vulnerable) |
| **npm packages** | 687 total (1 vulnerable, indirect) |

---

## Section 7: Recommendations

### Immediate (1–2 days)

1. **Review flatted exposure:** Confirm that eslint/cspell cache files are local-only. No risk if true.
2. **If yes → Skip fix for now:** The vulnerability is low-risk in this context.
3. **If no → Upgrade:** `npm update flatted` in `spa/` directory.

### Short-term (this month)

- **FastAPI 0.129 → 0.135:** One upgrade step; read [release notes](https://github.com/tiangolo/fastapi/releases/tag/0.135.0). No breaking changes expected. Safe anytime.
- **Uvicorn 0.40 → 0.41:** Single patch; safe.
- **Certifi, python-dotenv, etc.:** Bulk patch install: `.venv/bin/python -m pip install --upgrade pip certifi python-dotenv pytz` (non-blocking).

### Long-term (quarterly)

- Run this audit quarterly or after adding new dependencies.
- Subscribe to PyPI security advisories for fastapi, uvicorn, pydantic (core packages).
- Enable Dependabot on GitHub (auto-opens PRs for version updates).

---

## Appendix A: How to Re-Run This Audit

### Python (runtime + dev)

```bash
cd ~/Documents/projects/ollama-queue
.venv/bin/python -m pip-audit -r requirements.txt -f json       # Runtime CVEs
.venv/bin/python -m pip-audit -r requirements-dev.txt -f json   # Dev CVEs
.venv/bin/python -m pip list --outdated --format json           # Outdated
.venv/bin/python -m pip-licenses --format json --with-urls      # Licenses
```

### Node (SPA)

```bash
cd ollama_queue/dashboard/spa
npm audit --json                    # CVEs
npm outdated                        # Outdated packages
npx license-checker --json          # Licenses
```

---

## Appendix B: Audit Tools Used

- **Python CVE scanning:** `pip-audit` (installed, used for manifest-based scan)
- **Python outdated detection:** `pip list --outdated` (venv)
- **Python license scanning:** `pip-licenses` (venv)
- **npm CVE scanning:** `npm audit` (built-in)
- **npm license scanning:** `npm-license-checker` (installed via npx)

---

**Report generated:** 2026-03-13 (timestamp)  
**Auditor note:** This is a **read-only audit**. No packages were installed, upgraded, or modified.
