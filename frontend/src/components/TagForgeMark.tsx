/* Tag Forge logomark: a forge — hammer striking a sparking anvil.
 *
 * Adapted from "forge" by Monjin Friends (Noun Project), CC BY 3.0.
 * Attribution is a licence condition — see THIRD_PARTY_LICENSES.md; the
 * Settings page surfaces it in-app.
 *
 * Inherits color from the parent (currentColor) so it works on the brand
 * square in the sidebar and follows accent presets anywhere else. */

export function TagForgeMark({
  size = 18,
  className,
}: {
  size?: number;
  className?: string;
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="currentColor"
      className={className}
      aria-hidden="true"
    >
      {/* sparks */}
      <path d="M1.9 0.9 5.0 2.7 3.2 4.3z" />
      <path d="M5.2 0.4 9.3 5.9 6.8 5.1 8.2 7.5 4.0 3.0 6.5 3.8z" />
      <path d="M1.2 5.4 4.6 7.2 1.4 7.7z" />
      {/* hammer */}
      <path d="M12.3 3.3 20.8 0.4 22.2 4.2 13.7 7.1z" />
      <path d="M11.4 2.6 14.0 1.7 12.8 8.1 10.6 7.4z" />
      {/* anvil */}
      <path d="M5 9.2H18V10.8H23.2L20.6 13H17.8L16.2 17.6V18.7H19V22.4H5V18.7H7.8V17.6L6.2 13H1.5V10.8H5z" />
    </svg>
  );
}
