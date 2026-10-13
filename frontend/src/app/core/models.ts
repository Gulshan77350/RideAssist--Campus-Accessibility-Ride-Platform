// Mirrors the backend's pydantic schemas (see backend/*/schemas.py).

export type Role = 'rider' | 'driver' | 'college_admin' | 'super_admin';
export type VerificationStatus =
  | 'pending_otp'
  | 'awaiting_document'
  | 'pending_review'
  | 'verified'
  | 'rejected';
export type RideStatus =
  | 'requested'
  | 'accepted'
  | 'ongoing'
  | 'completed'
  | 'cancelled'
  | 'expired';
export type VehicleKind = 'wheelchair_accessible' | 'standard';
export type DocumentType = 'udid' | 'college_certificate';

export interface LatLng {
  lat: number;
  lng: number;
}
export interface Place extends LatLng {
  address?: string | null;
}

export interface Tokens {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

export interface StudentInfo {
  enrollment_no: string;
  verification_status: VerificationStatus;
  verified_at: string | null;
  verification_expires_at: string | null;
  needs_wheelchair: boolean;
  pickup_notes: string | null;
  emergency_contact_phone: string | null;
}

export interface Me {
  id: string;
  email: string;
  full_name: string;
  phone: string | null;
  role: Role;
  college_id: string | null;
  college_name: string | null;
  campus: { lat: number; lng: number; radius_m: number } | null;
  student: StudentInfo | null;
}

export interface RegisterOut {
  user_id: string;
  verification_status: VerificationStatus;
  message: string;
}
export interface OtpConfirmOut extends Tokens {
  verification_status: VerificationStatus;
  message: string;
}

export interface DocumentOut {
  id: string;
  doc_type: DocumentType;
  content_type: string;
  size_bytes: number;
  status: 'pending' | 'approved' | 'rejected';
  uploaded_at: string;
  reviewed_at: string | null;
}

export interface VerificationStatusOut {
  verification_status: VerificationStatus;
  can_book_rides: boolean;
  verified_at: string | null;
  verification_expires_at: string | null;
  review_note: string | null;
  latest_document: DocumentOut | null;
  next_step: string;
}

export interface Vehicle {
  id: string;
  plate_no: string;
  kind: VehicleKind;
  capacity: number;
  is_active: boolean;
}

export interface RouteInfo {
  eta_seconds: number;
  distance_m: number;
  polyline: [number, number][];
  provider: string;
}

export interface Ride {
  id: string;
  status: RideStatus;
  rider_id: string;
  rider_name: string;
  pickup: Place;
  dropoff: Place;
  needs_wheelchair: boolean;
  rider_notes: string | null;
  driver: {
    id: string;
    full_name: string;
    phone: string | null;
    vehicle: Vehicle | null;
    position: LatLng | null;
  } | null;
  route: RouteInfo | null;
  requested_at: string;
  accepted_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  cancelled_at: string | null;
  cancel_reason: string | null;
  distance_m: number | null;
}

export interface RideRequest {
  pickup: Place;
  dropoff: Place;
  needs_wheelchair?: boolean | null;
  notes?: string | null;
}

export interface Offer {
  ride_id: string;
  rider_name: string;
  pickup: Place;
  dropoff: Place;
  needs_wheelchair: boolean;
  rider_notes: string | null;
  distance_to_pickup_m: number | null;
  expires_at: string;
}

export interface DriverStatus {
  is_online: boolean;
  vehicle: Vehicle | null;
  active_ride_id: string | null;
  position: LatLng | null;
}

export interface NearbyVehicle extends LatLng {
  vehicle_kind: VehicleKind;
  distance_m: number;
}

export interface PendingVerification {
  student_id: string;
  full_name: string;
  email: string;
  enrollment_no: string;
  verification_status: VerificationStatus;
  roster_matched: boolean;
  registered_at: string;
  documents: DocumentOut[];
}

export interface RosterUploadOut {
  rows_processed: number;
  rows_rejected: string[];
  auto_verified_students: number;
}

export interface FleetDriver {
  id: string;
  full_name: string;
  email: string;
  phone: string | null;
  is_online: boolean;
  vehicle: Vehicle | null;
}

export interface Analytics {
  rides_today: number;
  completed_today: number;
  cancelled_today: number;
  unmatched_today: number;
  avg_wait_seconds_7d: number | null;
  avg_pickup_seconds_7d: number | null;
  active_riders_30d: number;
  online_drivers: number;
  verification_funnel: Record<VerificationStatus, number>;
  rides_per_day: { day: string; rides: number; completed: number }[];
}

export interface Anomaly {
  id: number;
  action: string;
  target_type: string | null;
  target_id: string | null;
  details: Record<string, unknown>;
  ip: string | null;
  created_at: string;
}

export type TrackEvent =
  | { type: 'snapshot'; ride_id: string; status: RideStatus; role: string; driver?: LatLng | null }
  | { type: 'location'; from: 'driver' | 'rider'; lat: number; lng: number; ts: number }
  | { type: 'status'; ride_id: string; status: RideStatus }
  | { type: 'error'; detail: string };
