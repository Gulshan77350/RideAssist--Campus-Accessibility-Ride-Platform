import { HttpClient, HttpErrorResponse, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { firstValueFrom, Observable } from 'rxjs';

import {
  Analytics,
  Anomaly,
  DocumentOut,
  DocumentType,
  DriverStatus,
  FleetDriver,
  LatLng,
  Me,
  NearbyVehicle,
  Offer,
  OtpConfirmOut,
  PendingVerification,
  RegisterOut,
  Ride,
  RideRequest,
  RosterUploadOut,
  Tokens,
  Vehicle,
  VehicleKind,
  VerificationStatus,
  VerificationStatusOut,
} from './models';

const API = '/api/v1';

/** Turns any HTTP error into the human-readable `detail` FastAPI returns. */
export function errorMessage(err: unknown): string {
  if (err instanceof HttpErrorResponse) {
    const detail = err.error?.detail;
    if (typeof detail === 'string') return detail;
    if (Array.isArray(detail) && detail.length) {
      return detail
        .map((d: { loc?: string[]; msg: string }) => `${d.loc?.at(-1) ?? 'field'}: ${d.msg}`)
        .join('; ');
    }
    if (err.status === 0) return 'Cannot reach the server. Check your connection.';
    if (err.status === 429) return 'Too many attempts. Please wait a moment and try again.';
    return `Request failed (${err.status})`;
  }
  return 'Something went wrong';
}

/** Typed client for every RideAssist endpoint, grouped by service. */
@Injectable({ providedIn: 'root' })
export class Api {
  private http = inject(HttpClient);

  private get<T>(url: string, params?: Record<string, string | number | boolean>) {
    return firstValueFrom(this.http.get<T>(API + url, { params: new HttpParams({ fromObject: params ?? {} }) }));
  }
  private post<T>(url: string, body: unknown = {}) {
    return firstValueFrom(this.http.post<T>(API + url, body));
  }

  // ---- auth & verification -------------------------------------------------
  login(email: string, password: string): Promise<Tokens> {
    const body = new HttpParams().set('username', email).set('password', password);
    return firstValueFrom(this.http.post<Tokens>(`${API}/auth/token`, body));
  }
  refresh(refreshToken: string): Observable<Tokens> {
    return this.http.post<Tokens>(`${API}/auth/refresh`, { refresh_token: refreshToken });
  }
  me() {
    return this.get<Me>('/auth/me');
  }
  updateAccessibility(body: Partial<Pick<Me['student'] & object, 'needs_wheelchair' | 'pickup_notes' | 'emergency_contact_phone'>>) {
    return firstValueFrom(this.http.patch<Me>(`${API}/auth/me/accessibility`, body));
  }
  collegeLookup(domain: string) {
    return this.get<{ name: string; subdomain: string }>('/colleges/lookup', { domain });
  }
  register(body: { email: string; enrollment_no: string; full_name: string; password: string; phone?: string | null }) {
    return this.post<RegisterOut>('/auth/register/student', body);
  }
  sendOtp(email: string) {
    return this.post<{ message: string }>('/verification/otp/send', { email });
  }
  confirmOtp(email: string, code: string) {
    return this.post<OtpConfirmOut>('/verification/otp/confirm', { email, code });
  }
  verificationStatus() {
    return this.get<VerificationStatusOut>('/verification/status');
  }
  uploadDocument(docType: DocumentType, file: File) {
    const form = new FormData();
    form.append('doc_type', docType);
    form.append('file', file);
    return this.post<DocumentOut>('/verification/documents', form);
  }

  // ---- rides -----------------------------------------------------------------
  requestRide(body: RideRequest) {
    return this.post<Ride>('/rides', body);
  }
  myRides(limit = 30) {
    return this.get<Ride[]>('/rides', { limit });
  }
  async activeRide(): Promise<Ride | null> {
    const resp = await firstValueFrom(this.http.get<Ride>(`${API}/rides/active`, { observe: 'response' }));
    return resp.status === 204 ? null : resp.body;
  }
  ride(id: string) {
    return this.get<Ride>(`/rides/${id}`);
  }
  rideAction(id: string, action: 'accept' | 'start' | 'complete') {
    return this.post<Ride>(`/rides/${id}/${action}`);
  }
  rejectOffer(id: string) {
    return this.post<void>(`/rides/${id}/reject`);
  }
  cancelRide(id: string, reason?: string) {
    return this.post<Ride>(`/rides/${id}/cancel`, { reason: reason || null });
  }
  rate(id: string, rating: number, comment?: string) {
    return this.post<void>(`/rides/${id}/rating`, { rating, comment: comment || null });
  }
  sos(id: string) {
    return this.post<{ alerted: string[]; message: string }>(`/rides/${id}/sos`);
  }
  nearby(at: LatLng, radius = 2500) {
    return this.get<NearbyVehicle[]>('/drivers/nearby', { lat: at.lat, lng: at.lng, radius });
  }

  // ---- driver ----------------------------------------------------------------
  driverStatus() {
    return this.get<DriverStatus>('/drivers/me');
  }
  goOnline(at: LatLng) {
    return this.post<DriverStatus>('/drivers/me/online', at);
  }
  goOffline() {
    return this.post<DriverStatus>('/drivers/me/offline');
  }
  reportLocation(at: LatLng) {
    return this.post<void>('/drivers/me/location', at);
  }
  async currentOffer(): Promise<Offer | null> {
    const resp = await firstValueFrom(this.http.get<Offer>(`${API}/drivers/me/offer`, { observe: 'response' }));
    return resp.status === 204 ? null : resp.body;
  }

  // ---- college admin ---------------------------------------------------------
  verifications(status: VerificationStatus) {
    return this.get<PendingVerification[]>('/admin/verifications', { status });
  }
  documentBlob(id: string) {
    return firstValueFrom(this.http.get(`${API}/admin/verifications/documents/${id}/file`, { responseType: 'blob' }));
  }
  approve(studentId: string, note?: string) {
    return this.post<PendingVerification>(`/admin/verifications/${studentId}/approve`, { note: note || null });
  }
  reject(studentId: string, note: string) {
    return this.post<PendingVerification>(`/admin/verifications/${studentId}/reject`, { note });
  }
  uploadRoster(file: File) {
    const form = new FormData();
    form.append('file', file);
    return this.post<RosterUploadOut>('/admin/roster', form);
  }
  anomalies() {
    return this.get<Anomaly[]>('/admin/anomalies');
  }
  createDriver(body: { email: string; full_name: string; phone: string; password: string }) {
    return this.post<{ id: string }>('/admin/staff/drivers', body);
  }
  vehicles() {
    return this.get<Vehicle[]>('/admin/fleet/vehicles');
  }
  addVehicle(body: { plate_no: string; kind: VehicleKind; capacity: number }) {
    return this.post<Vehicle>('/admin/fleet/vehicles', body);
  }
  updateVehicle(id: string, body: Partial<Pick<Vehicle, 'kind' | 'capacity' | 'is_active'>>) {
    return firstValueFrom(this.http.patch<Vehicle>(`${API}/admin/fleet/vehicles/${id}`, body));
  }
  fleetDrivers() {
    return this.get<FleetDriver[]>('/admin/fleet/drivers');
  }
  assignVehicle(driverId: string, vehicleId: string | null) {
    return firstValueFrom(
      this.http.put<FleetDriver>(`${API}/admin/fleet/drivers/${driverId}/vehicle`, { vehicle_id: vehicleId }),
    );
  }
  adminRides(activeOnly = true) {
    return this.get<Ride[]>('/admin/rides', { active_only: activeOnly });
  }
  analytics() {
    return this.get<Analytics>('/admin/analytics/overview');
  }
}
