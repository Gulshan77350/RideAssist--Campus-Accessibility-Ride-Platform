import { Injectable, computed, inject, signal } from '@angular/core';
import { Router } from '@angular/router';
import { Observable, finalize, shareReplay, tap } from 'rxjs';

import { Api } from './api';
import { Me, Role, Tokens } from './models';

const ACCESS_KEY = 'ra.access';
const REFRESH_KEY = 'ra.refresh';

function read(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}
function write(key: string, value: string | null): void {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    /* storage unavailable (private mode): session lasts until reload */
  }
}

export function homeFor(role: Role | null | undefined): string {
  switch (role) {
    case 'rider':
      return '/rider';
    case 'driver':
      return '/driver';
    case 'college_admin':
    case 'super_admin':
      return '/admin';
    default:
      return '/login';
  }
}

@Injectable({ providedIn: 'root' })
export class Auth {
  private api = inject(Api);
  private router = inject(Router);

  private access = signal<string | null>(read(ACCESS_KEY));
  private refreshToken = read(REFRESH_KEY);
  private refreshing$: Observable<Tokens> | null = null;

  readonly me = signal<Me | null>(null);
  readonly role = computed(() => this.me()?.role ?? null);
  readonly isLoggedIn = computed(() => this.access() !== null);

  get accessToken(): string | null {
    return this.access();
  }

  setTokens(tokens: Tokens): void {
    this.access.set(tokens.access_token);
    this.refreshToken = tokens.refresh_token;
    write(ACCESS_KEY, tokens.access_token);
    write(REFRESH_KEY, tokens.refresh_token);
  }

  async login(email: string, password: string): Promise<Me> {
    this.setTokens(await this.api.login(email, password));
    return (await this.loadMe())!;
  }

  /** Restores the session on app start; silently clears it if tokens are dead. */
  async loadMe(): Promise<Me | null> {
    if (!this.access()) return null;
    try {
      const me = await this.api.me();
      this.me.set(me);
      return me;
    } catch {
      this.clear();
      return null;
    }
  }

  /** Single-flight refresh: concurrent 401s share one refresh call (tokens are single-use). */
  refresh(): Observable<Tokens> {
    if (!this.refreshToken) throw new Error('no refresh token');
    this.refreshing$ ??= this.api.refresh(this.refreshToken).pipe(
      tap((t) => this.setTokens(t)),
      finalize(() => (this.refreshing$ = null)),
      shareReplay(1),
    );
    return this.refreshing$;
  }

  get canRefresh(): boolean {
    return !!this.refreshToken;
  }

  clear(): void {
    this.access.set(null);
    this.refreshToken = null;
    this.me.set(null);
    write(ACCESS_KEY, null);
    write(REFRESH_KEY, null);
  }

  logout(): void {
    this.clear();
    this.router.navigateByUrl('/login');
  }
}
