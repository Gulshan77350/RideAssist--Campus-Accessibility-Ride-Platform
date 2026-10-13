import { Routes } from '@angular/router';

import { guestGuard, roleGuard } from './core/http';
import { Shell } from './shared/shell';

// Each role's pages are lazy-loaded, so a rider never downloads the admin console.
export const routes: Routes = [
  { path: '', pathMatch: 'full', redirectTo: 'login' },
  {
    path: 'login',
    canActivate: [guestGuard],
    title: 'Log in · RideAssist',
    loadComponent: () => import('./features/auth/login').then((m) => m.Login),
  },
  {
    path: 'register',
    canActivate: [guestGuard],
    title: 'Sign up · RideAssist',
    loadComponent: () => import('./features/auth/register').then((m) => m.Register),
  },
  {
    path: 'verify',
    title: 'Verify email · RideAssist',
    loadComponent: () => import('./features/auth/verify-otp').then((m) => m.VerifyOtp),
  },
  {
    path: 'rider',
    component: Shell,
    canActivate: [roleGuard('rider')],
    children: [
      { path: '', title: 'Book a ride · RideAssist', loadComponent: () => import('./features/rider/rider-home').then((m) => m.RiderHome) },
      { path: 'history', title: 'My rides · RideAssist', loadComponent: () => import('./features/shared-pages/ride-history').then((m) => m.RideHistory) },
      { path: 'verification', title: 'Verification · RideAssist', loadComponent: () => import('./features/rider/verification').then((m) => m.Verification) },
      { path: 'settings', title: 'Accessibility · RideAssist', loadComponent: () => import('./features/rider/settings').then((m) => m.Settings) },
    ],
  },
  {
    path: 'driver',
    component: Shell,
    canActivate: [roleGuard('driver')],
    children: [
      { path: '', title: 'Drive · RideAssist', loadComponent: () => import('./features/driver/driver-home').then((m) => m.DriverHome) },
      { path: 'history', title: 'Ride log · RideAssist', loadComponent: () => import('./features/shared-pages/ride-history').then((m) => m.RideHistory) },
    ],
  },
  {
    path: 'admin',
    component: Shell,
    canActivate: [roleGuard('college_admin', 'super_admin')],
    children: [
      { path: '', title: 'Overview · RideAssist', loadComponent: () => import('./features/admin/overview').then((m) => m.AdminOverview) },
      { path: 'verifications', title: 'Verifications · RideAssist', loadComponent: () => import('./features/admin/verifications').then((m) => m.AdminVerifications) },
      { path: 'rides', title: 'Live rides · RideAssist', loadComponent: () => import('./features/admin/live-rides').then((m) => m.AdminLiveRides) },
      { path: 'fleet', title: 'Fleet · RideAssist', loadComponent: () => import('./features/admin/fleet').then((m) => m.AdminFleet) },
      { path: 'roster', title: 'Roster · RideAssist', loadComponent: () => import('./features/admin/roster').then((m) => m.AdminRoster) },
      { path: 'anomalies', title: 'Anomalies · RideAssist', loadComponent: () => import('./features/admin/anomalies').then((m) => m.AdminAnomalies) },
    ],
  },
  { path: '**', redirectTo: 'login' },
];
