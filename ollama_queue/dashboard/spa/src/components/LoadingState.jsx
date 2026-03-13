// What it shows: Skeleton placeholder while data loads — prevents blank flashes.
// Decision it drives: User knows data is on the way, not broken.
// B6: All loading states unified to ShSkeletonNative (one pattern everywhere).

import ShSkeletonNative from './ShSkeletonNative.jsx';

export default function LoadingState({ type = 'full' }) {
  if (type === 'stats') {
    return (
      <div class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-4">
        {[...Array(5)].map((_, i) => (
          <div key={i} class="t-card" style="padding: 16px;">
            <ShSkeletonNative rows={2} />
          </div>
        ))}
      </div>
    );
  }

  if (type === 'table') {
    return (
      <div class="t-card" style="overflow: hidden; padding: 1rem;">
        <ShSkeletonNative rows={6} />
      </div>
    );
  }

  if (type === 'cards') {
    return (
      <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {[...Array(3)].map((_, i) => (
          <div key={i} class="t-card" style="padding: 16px;">
            <ShSkeletonNative rows={3} />
          </div>
        ))}
      </div>
    );
  }

  // type === 'full'
  return (
    <div style="display: flex; flex-direction: column; gap: 1.5rem;">
      <div class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-4">
        {[...Array(5)].map((_, i) => (
          <div key={i} class="t-card" style="padding: 16px;">
            <ShSkeletonNative rows={2} />
          </div>
        ))}
      </div>
      <div class="t-card" style="overflow: hidden; padding: 1rem;">
        <ShSkeletonNative rows={4} />
      </div>
    </div>
  );
}
