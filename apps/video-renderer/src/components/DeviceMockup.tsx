import { ReactNode } from 'react';

/**
 * Phone or browser frame that wraps a "screen" — used now as a
 * decorative placeholder for future product screen-recordings, and
 * already as a brand-coloured device-shape primitive on its own.
 *
 * Two variants:
 *   - 'phone'   — 9:19.5 vertical with rounded corners + notch + bezel
 *   - 'browser' — desktop window with the three macOS dots + URL bar
 *
 * Pass children to fill the screen area; defaults to a brand-coloured
 * gradient placeholder so the mockup looks complete even with no asset.
 */
export const DeviceMockup: React.FC<{
  variant?: 'phone' | 'browser';
  width: number;
  color: string;
  children?: ReactNode;
}> = ({ variant = 'phone', width, color, children }) => {
  if (variant === 'browser') {
    const height = width * 0.62;
    const chromeHeight = 36;
    return (
      <div
        style={{
          width,
          height,
          borderRadius: 16,
          overflow: 'hidden',
          background: '#ffffff',
          boxShadow: '0 24px 60px rgba(15,23,42,0.25)',
          border: '1px solid #e2e8f0',
        }}
      >
        {/* Chrome bar */}
        <div
          style={{
            height: chromeHeight,
            background: '#f1f5f9',
            display: 'flex',
            alignItems: 'center',
            paddingLeft: 16,
            gap: 6,
            borderBottom: '1px solid #e2e8f0',
          }}
        >
          <span style={{ width: 10, height: 10, borderRadius: '50%', background: '#ef4444' }} />
          <span style={{ width: 10, height: 10, borderRadius: '50%', background: '#f59e0b' }} />
          <span style={{ width: 10, height: 10, borderRadius: '50%', background: '#10b981' }} />
        </div>
        {/* Screen */}
        <div
          style={{
            width: '100%',
            height: height - chromeHeight,
            background: children
              ? '#ffffff'
              : `linear-gradient(135deg, ${color}11, ${color}22)`,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          {children}
        </div>
      </div>
    );
  }

  // phone variant
  const phoneHeight = width * 2.05;
  const bezel = width * 0.04;
  const notchWidth = width * 0.3;
  const notchHeight = width * 0.06;

  return (
    <div
      style={{
        width,
        height: phoneHeight,
        borderRadius: width * 0.13,
        background: '#0f172a',
        padding: bezel,
        boxShadow: '0 30px 60px rgba(15,23,42,0.35)',
        position: 'relative',
      }}
    >
      <div
        style={{
          width: '100%',
          height: '100%',
          borderRadius: width * 0.09,
          background: children
            ? '#ffffff'
            : `linear-gradient(135deg, ${color}11, ${color}33)`,
          overflow: 'hidden',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        {children}
      </div>
      {/* Notch */}
      <div
        style={{
          position: 'absolute',
          top: bezel * 1.4,
          left: '50%',
          transform: 'translateX(-50%)',
          width: notchWidth,
          height: notchHeight,
          background: '#0f172a',
          borderRadius: notchHeight,
        }}
      />
    </div>
  );
};
