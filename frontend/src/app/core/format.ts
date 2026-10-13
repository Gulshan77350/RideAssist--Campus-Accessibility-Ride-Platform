import { RideStatus, VehicleKind, VerificationStatus } from './models';

export function duration(seconds: number | null | undefined): string {
  if (seconds == null) return '—';
  if (seconds < 60) return `${Math.max(1, Math.round(seconds))} s`;
  const m = Math.round(seconds / 60);
  return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`;
}

export function distance(m: number | null | undefined): string {
  if (m == null) return '—';
  return m < 1000 ? `${Math.round(m)} m` : `${(m / 1000).toFixed(1)} km`;
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}

export const RIDE_STATUS: Record<RideStatus, { label: string; tone: string }> = {
  requested: { label: 'Finding driver', tone: 'badge-info' },
  accepted: { label: 'Driver on the way', tone: 'badge-info' },
  ongoing: { label: 'On board', tone: 'badge-warning' },
  completed: { label: 'Completed', tone: 'badge-success' },
  cancelled: { label: 'Cancelled', tone: '' },
  expired: { label: 'No driver found', tone: 'badge-danger' },
};

export const VERIFICATION_STATUS: Record<VerificationStatus, { label: string; tone: string }> = {
  pending_otp: { label: 'Email not verified', tone: 'badge-warning' },
  awaiting_document: { label: 'Document needed', tone: 'badge-warning' },
  pending_review: { label: 'Under review', tone: 'badge-info' },
  verified: { label: 'Verified', tone: 'badge-success' },
  rejected: { label: 'Rejected', tone: 'badge-danger' },
};

export const VEHICLE_KIND: Record<VehicleKind, string> = {
  wheelchair_accessible: '♿ Wheelchair accessible',
  standard: 'Standard',
};

/** Moves `from` toward `to` by at most `step` metres (used by the driver's drive simulator). */
export function stepToward(
  from: { lat: number; lng: number },
  to: { lat: number; lng: number },
  step: number,
): { lat: number; lng: number; arrived: boolean } {
  const mPerDegLat = 111_320;
  const mPerDegLng = 111_320 * Math.cos((from.lat * Math.PI) / 180);
  const dy = (to.lat - from.lat) * mPerDegLat;
  const dx = (to.lng - from.lng) * mPerDegLng;
  const dist = Math.hypot(dx, dy);
  if (dist <= step) return { ...to, arrived: true };
  const f = step / dist;
  return { lat: from.lat + (dy * f) / mPerDegLat, lng: from.lng + (dx * f) / mPerDegLng, arrived: false };
}
