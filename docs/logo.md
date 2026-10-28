# Logo and icons

The SVG files have transparent backgrounds and use vector paths without external
fonts, scripts or images. Each file includes an accessible title and description.

## Assets

| File | Variant |
| --- | --- |
| [Horizontal logo](assets/slbt-logo.svg) | Light backgrounds; the default README image. |
| [Horizontal logo, dark variant](assets/slbt-logo-dark.svg) | Dark backgrounds; selected automatically by the README's picture element. |
| [Square icon](assets/slbt-mark.svg) | Light backgrounds. |
| [Square icon, dark variant](assets/slbt-mark-dark.svg) | Dark backgrounds. |
| [Single-color icon](assets/slbt-mark-mono.svg) | Light backgrounds. |

Horizontal logos use a `384 × 128` viewBox; square icons use `128 × 128`.
Preserve the aspect ratio when resizing.

When embedding an image, provide alt text such as
`slbt — Semantic Layer Build Tool`. The README selects the dark logo for dark mode
and uses the light logo as its fallback.

These files are included under the project's [MIT license](../LICENSE).
