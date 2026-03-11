import { h } from 'preact';

/**
 * What it shows: SUPERHOT-style CRT terminal page banner — section title with phosphor glow,
 *   a horizontal scan beam sweep, and CRT scanline texture overlay.
 * Decision it drives: Immediate visual orientation — which section of the dashboard the user is on.
 *   Matches the design language of the ARIA hub (same .page-banner-sh pattern).
 *
 * @param {{ title: string, subtitle?: string }} props
 */
export default function PageBanner({ title, subtitle }) {
  return (
    <div class="page-banner-sh">
      <div class="banner-title">{title}</div>
      {subtitle && <div class="banner-subtitle">{subtitle}</div>}
    </div>
  );
}
