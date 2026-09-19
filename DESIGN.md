# bigfeels website design

This identity applies to the public project site and its downloadable brand assets. The existing local memory workspace remains unchanged.

## Direction

Evidence receipts make memory provenance visible. The first viewport uses an oversized, left-aligned headline with a paired memory/source illustration. All example records are explicitly illustrative. Lower sections move from the lifecycle to interface choices, privacy boundaries, and installation. No invented metrics, testimonials, pricing, or universal capture claims.

## Palette

- Lavender `#b5a0ee`: header and hero field
- Plum `#241633`: primary text, mark, installation field
- Paper `#f7f5fa`: reading surfaces and inverse text
- Citron `#e7ff87`: primary action and source receipt
- Muted plum `#584969`: supporting text on light surfaces

## Type and geometry

Self-hosted Manrope regular and extra-bold. The wordmark contains outlined glyphs. Display type uses tight, balanced line breaks; body text retains generous leading. Desktop display peaks at 83px. Mobile headline is 49px, reducing to 43px at the smallest width. Buttons are at least 46px high for primary interactions. Open sections, thin separators, and purposeful receipts replace generic feature-card grids.

## Brand assets

The generated linked-loop mark was normalized to a single-color silhouette and vector-traced. SVG icon, outlined SVG wordmarks, transparent PNGs, backed app icon, favicon, social preview, and downloadable kit live under `site/assets/`. Manrope retains its SIL Open Font License.

## Responsive behavior

A two-column hero becomes a stacked composition at 800px. Lifecycle, interface, and privacy layouts collapse to one column with preserved reading order. Code blocks scroll internally on narrow screens. The nonessential decorative install mark disappears at 480px. No page-wide horizontal scrolling is allowed at 320, 390, 768, or 1440px.

## Interaction and motion

The memory demonstration uses ordinary labelled buttons with `aria-pressed` and a polite live result. It never writes a real store. Clipboard failure selects the commands and explains manual copying. The only entrance motion settles the two receipts, and reduced-motion users receive a static layout. Every control has an explicit focus style. The whole site works without a backend; content and installation commands remain readable without JavaScript.

## Voice

Direct human English. No em dashes, rhetorical parallelisms, AI filler, inflated privacy claims, or fake proof. The lowercase product is bigfeels; the executable is bigfeels-mem.
