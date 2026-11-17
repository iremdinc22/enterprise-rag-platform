# Application screenshot provenance

[Project overview](../../README.md) · [Final validation](../final-validation.md)

## Manual presentation captures — 2026-11-17

The main README uses the two screenshots supplied by the repository owner from
manual use of the running localhost demo. They are copied byte-for-byte, without
cropping, resizing or retouching. Responsive Markdown rendering displays both at
the same content width while preserving their original aspect ratios.

These captures are presentation assets, not additional automated validation or
proof of a tested commit. The Ask AI image shows a deterministic Orion answer
(731 USD), citing `SYNTHETIC-acme-orion.pdf`, page 2. It replaces the earlier
empty-form capture. Both preserve the deterministic simulation label.
Visual review found no passwords, JWTs, keys or private paths; only demo account
and document names, the synthetic question and its source excerpt are visible.

| Repository image | Original supplied filename | Dimensions |
| --- | --- | --- |
| [Document workspace](atlas-workspace-manual.png) | `Ekran Resmi 2026-11-17 17.30.10.png` | 2876 × 1420 |
| [Ask AI](atlas-ask-ai-manual.png) | `Ekran Resmi 2026-11-17 17.36.07.png` | 2880 × 1418 |

SHA-256 checksums of the unchanged supplied PNGs:

- `atlas-workspace-manual.png`: `01b8b156e4d0ca472e7118d8d34fe48fce9d90fb17687ad6adccb9594a0b247e`
- `atlas-ask-ai-manual.png`: `921dc40203fd27d226e5170ba12c0ba0c043b75d4f126da828227493b1faf5d7`

## Automated CI validation captures

The following are unmodified captures of the actual running deterministic demo from
[Atlas Demo E2E run 44090728527](https://github.com/iremdinc22/enterprise-rag-platform/actions/runs/44090728527),
at commit `626575e1fde6e6a0017995cfd5c80e798566a1bf`.
They use synthetic documents, preserve simulation labels and were visually checked
for credentials, tokens and sensitive information before publication.

| Repository image | Original artifact filename | Dimensions |
| --- | --- | --- |
| [Login](atlas-login.png) | `login.png` | 1440 × 900 |
| [Workspace](atlas-workspace.png) | `workspace-overview.png` | 1440 × 1179 |
| [Document library](atlas-document-library.png) | `document-library.png` | 1440 × 1179 |
| [Answer and citations](atlas-answer-citations.png) | `answer-citations.png` | 1440 × 1026 |
| [Mobile documents](atlas-mobile-documents.png) | `mobile-dashboard.png` | 390 × 1875 |
| [Mobile answer](atlas-mobile-answer.png) | `mobile-answer.png` | 390 × 1271 |

Images are full-page captures; their height may exceed the configured viewport.
They are evidence of the tested state, not fabricated mockups or real LLM output.
