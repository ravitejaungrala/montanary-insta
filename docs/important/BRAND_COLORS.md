# Pipelyt Brand Color Palette

**Approved hex codes — these are the ONLY colors that may appear in the product UI.**

| Token | Hex | Tailwind class | Use |
|---|---|---|---|
| Brand Orange | `#ff4500` | `bg-[#ff4500]`, `text-[#ff4500]`, `border-[#ff4500]` | Primary CTAs, brand accents, active states, highlights |
| Success Green | `#10B981` | `bg-[#10B981]`, `text-[#10B981]`, `border-[#10B981]` | Success states, "verified" badges, positive metrics |
| Black | `#000000` | `bg-black`, `text-black`, `border-black` | Body text, primary content |
| White | `#FFFFFF` | `bg-white`, `text-white`, `border-white` | Page background, card surfaces |

## Greys = black/white with opacity

Do NOT use `slate-*`, `gray-*`, `zinc-*`, `neutral-*`, or any other Tailwind palette greys. Express greys as **black or white with opacity**:

| Need | Use |
|---|---|
| Body label | `text-black` |
| Secondary text | `text-black/60` (60% black) |
| Muted text / placeholders | `text-black/40` |
| Disabled text | `text-black/30` |
| Card border | `border-black/10` or `border-black/15` |
| Hover overlay | `hover:bg-[#ff4500]/5` (5% orange) |
| Subtle background | `bg-black/5` |

## Brand-tinted surfaces

For backgrounds, borders, and rings tied to brand orange, use opacity variants:

- `border-[#ff4500]/20` — light orange border
- `border-[#ff4500]/30` — medium orange border
- `bg-[#ff4500]/5` — very subtle orange tint (for hover)
- `bg-[#ff4500]/10` — subtle orange tint
- `ring-[#ff4500]/20` — focus ring
- `shadow-[#ff4500]/20` — orange-tinted shadow

Same applies to `#10B981` for success states.

## ❌ Forbidden

Do NOT use any of these in product code:

- `slate-*`, `gray-*`, `zinc-*`, `neutral-*`, `stone-*`
- `orange-50`, `orange-100`, `orange-200`, `orange-300` ... (any Tailwind orange shade other than the literal `[#ff4500]`)
- `red-*`, `rose-*`, `pink-*`, `amber-*`, `yellow-*` (unless the design explicitly calls for an alert state — and even then, prefer `#ff4500` or `#10B981`)
- `green-*`, `emerald-*`, `teal-*`, `cyan-*` (use `#10B981`)
- `blue-*`, `sky-*`, `indigo-*`, `violet-*`, `purple-*`, `fuchsia-*`
- Hardcoded hex codes other than the four approved ones

## Exceptions

Platform brand colors are allowed when displaying that platform's icon/badge ONLY:

- LinkedIn: `#0077b5`
- Twitter / X: `#000000` (black is fine, already approved)
- Facebook: `#1877F2`
- Instagram: `#E4405F`
- Reddit: `#ff4500` (already our brand orange)
- YouTube: `#c40000`
- Pinterest: `#a8001a`

Use these only inside platform-specific components (PlatformIcon, OAuth cards). Never as general-purpose UI colors.

## Quick reference for new components

```jsx
// ✅ Good
<button className="bg-[#ff4500] text-white border border-[#ff4500]">CTA</button>
<div className="text-black/60 border border-black/10 hover:bg-[#ff4500]/5">Item</div>

// ❌ Bad
<button className="bg-orange-500 text-slate-700 border-slate-200">CTA</button>
<div className="text-gray-500 border-slate-100 hover:bg-orange-50">Item</div>
```
