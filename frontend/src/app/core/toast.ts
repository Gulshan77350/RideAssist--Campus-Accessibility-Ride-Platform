import { Component, Injectable, inject, signal } from '@angular/core';

export interface Toast {
  id: number;
  text: string;
  kind: 'info' | 'success' | 'error';
}

@Injectable({ providedIn: 'root' })
export class Toasts {
  readonly items = signal<Toast[]>([]);
  private next = 1;

  show(text: string, kind: Toast['kind'] = 'info', ms = 5000): void {
    const toast = { id: this.next++, text, kind };
    this.items.update((t) => [...t, toast]);
    setTimeout(() => this.dismiss(toast.id), ms);
  }
  success(text: string) {
    this.show(text, 'success');
  }
  error(text: string) {
    this.show(text, 'error', 7000);
  }
  dismiss(id: number): void {
    this.items.update((t) => t.filter((x) => x.id !== id));
  }
}

/** Toasts are announced politely to screen readers; errors assertively. */
@Component({
  selector: 'app-toasts',
  template: `
    <div class="toasts" aria-live="polite" role="status">
      @for (t of toasts.items(); track t.id) {
        <div class="toast" [class]="'toast ' + t.kind" [attr.role]="t.kind === 'error' ? 'alert' : null">
          <span>{{ t.text }}</span>
          <button class="btn btn-ghost" (click)="toasts.dismiss(t.id)" aria-label="Dismiss notification">✕</button>
        </div>
      }
    </div>
  `,
  styles: `
    .toasts {
      position: fixed;
      bottom: 1rem;
      right: 1rem;
      left: 1rem;
      display: flex;
      flex-direction: column;
      align-items: flex-end;
      gap: 0.5rem;
      z-index: 3000;
      pointer-events: none;
    }
    .toast {
      pointer-events: auto;
      display: flex;
      align-items: center;
      gap: 0.5rem;
      max-width: 420px;
      background: var(--surface);
      border: 1px solid var(--border);
      border-left: 5px solid var(--primary);
      border-radius: var(--radius-sm);
      box-shadow: var(--shadow);
      padding: 0.4rem 0.4rem 0.4rem 1rem;
    }
    .toast.success { border-left-color: var(--success); }
    .toast.error { border-left-color: var(--danger); }
    .btn { min-height: 36px; padding: 0.3rem 0.6rem; }
  `,
})
export class ToastHost {
  protected toasts = inject(Toasts);
}
