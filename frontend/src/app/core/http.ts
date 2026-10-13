import { HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { catchError, switchMap, throwError } from 'rxjs';

import { Auth, homeFor } from './auth';
import { Role } from './models';

const NO_AUTH = ['/auth/token', '/auth/refresh', '/auth/register', '/verification/otp', '/colleges/lookup'];

/** Adds the bearer token and transparently refreshes once on a 401. */
export const authInterceptor: HttpInterceptorFn = (req, next) => {
  const auth = inject(Auth);
  if (!req.url.startsWith('/api') || NO_AUTH.some((p) => req.url.includes(p))) {
    return next(req);
  }
  const withToken = (token: string | null) =>
    token ? req.clone({ setHeaders: { Authorization: `Bearer ${token}` } }) : req;

  return next(withToken(auth.accessToken)).pipe(
    catchError((err: unknown) => {
      if (!(err instanceof HttpErrorResponse) || err.status !== 401 || !auth.canRefresh) {
        return throwError(() => err);
      }
      return auth.refresh().pipe(
        switchMap((t) => next(withToken(t.access_token))),
        catchError((refreshErr) => {
          auth.logout();
          return throwError(() => refreshErr);
        }),
      );
    }),
  );
};

/** Route guard: logged in AND one of the allowed roles, otherwise bounce to the right home. */
export function roleGuard(...roles: Role[]): CanActivateFn {
  return async () => {
    const auth = inject(Auth);
    const router = inject(Router);
    const me = auth.me() ?? (await auth.loadMe());
    if (!me) return router.parseUrl('/login');
    return roles.includes(me.role) ? true : router.parseUrl(homeFor(me.role));
  };
}

export const guestGuard: CanActivateFn = async () => {
  const auth = inject(Auth);
  const me = auth.me() ?? (await auth.loadMe());
  return me ? inject(Router).parseUrl(homeFor(me.role)) : true;
};
