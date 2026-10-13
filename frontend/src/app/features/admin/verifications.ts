import { Component, OnDestroy, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { DomSanitizer, SafeResourceUrl } from '@angular/platform-browser';

import { Api, errorMessage } from '../../core/api';
import { VERIFICATION_STATUS, dateTime } from '../../core/format';
import { DocumentOut, PendingVerification, VerificationStatus } from '../../core/models';
import { Toasts } from '../../core/toast';

const TABS: VerificationStatus[] = ['pending_review', 'awaiting_document', 'rejected', 'verified'];

@Component({
  selector: 'app-admin-verifications',
  imports: [FormsModule],
  template: `
    <h1>Verifications</h1>
    <div class="tabs" role="tablist" aria-label="Filter by status">
      @for (t of tabs; track t) {
        <button role="tab" type="button" class="tab" [class.on]="tab() === t" [attr.aria-selected]="tab() === t" (click)="select(t)">
          {{ label(t) }}
        </button>
      }
    </div>

    @if (loading()) {
      <p class="muted">Loading…</p>
    } @else if (!items().length) {
      <div class="card empty">Nothing here.{{ tab() === 'pending_review' ? ' The review queue is clear 🎉' : '' }}</div>
    } @else {
      <ul class="list">
        @for (s of items(); track s.student_id) {
          <li class="card stack">
            <div class="spread">
              <div>
                <h2 class="name">{{ s.full_name }}</h2>
                <span class="small muted">{{ s.email }} · {{ s.enrollment_no }} · registered {{ when(s.registered_at) }}</span>
              </div>
              <div class="row">
                @if (s.roster_matched) { <span class="badge badge-success">In PwD roster</span> }
                @else { <span class="badge">Not in roster</span> }
              </div>
            </div>

            @if (s.documents.length) {
              <div class="row small">
                @for (d of s.documents; track d.id) {
                  <button class="btn btn-outline" type="button" (click)="view(d)">
                    📄 {{ d.doc_type === 'udid' ? 'UDID card' : 'Certificate' }} · {{ when(d.uploaded_at) }} · {{ d.status }}
                  </button>
                }
              </div>
            }

            @if (s.verification_status === 'pending_review') {
              <div class="field">
                <label [for]="'note-' + s.student_id">Review note <span class="muted">(required to reject; shown to the student)</span></label>
                <input [id]="'note-' + s.student_id" [(ngModel)]="notes[s.student_id]" maxlength="500" />
              </div>
              <div class="row">
                <button class="btn btn-primary" type="button" (click)="approve(s)" [disabled]="busy()">Approve</button>
                <button class="btn btn-danger" type="button" (click)="reject(s)" [disabled]="busy()">Reject</button>
              </div>
            }
          </li>
        }
      </ul>
    }

    @if (preview(); as p) {
      <div class="modal-backdrop" (click)="closePreview()">
        <div class="modal card" role="dialog" aria-modal="true" aria-label="Document preview" (click)="$event.stopPropagation()">
          <div class="spread">
            <h2>Document preview</h2>
            <button class="btn btn-outline" type="button" (click)="closePreview()" autofocus>Close</button>
          </div>
          <p class="small muted">Decrypted for you only. This view has been recorded in the audit log.</p>
          @if (p.pdf) {
            <iframe [src]="p.url" title="Disability document (PDF)"></iframe>
          } @else {
            <img [src]="p.url" alt="Uploaded disability document" />
          }
        </div>
      </div>
    }
  `,
  styles: `
    .tabs { display: flex; gap: 0.25rem; flex-wrap: wrap; margin: 0.5rem 0 1rem; }
    .tab { font: inherit; font-weight: 600; padding: 0.55rem 1rem; min-height: 44px; border-radius: 999px;
      border: 1.5px solid var(--border); background: var(--surface); color: var(--text); cursor: pointer; }
    .tab.on { background: var(--primary); color: var(--on-primary); border-color: var(--primary); }
    .list { list-style: none; padding: 0; margin: 0; display: grid; gap: 0.75rem; }
    .name { margin: 0; }
    .modal-backdrop { position: fixed; inset: 0; background: rgb(0 0 0 / 55%); display: grid; place-items: center; z-index: 2500; padding: 1rem; }
    .modal { width: min(900px, 100%); max-height: 92vh; overflow: auto; }
    .modal img { max-width: 100%; border-radius: var(--radius-sm); }
    .modal iframe { width: 100%; height: 70vh; border: 0; }
  `,
})
export class AdminVerifications implements OnInit, OnDestroy {
  private api = inject(Api);
  private toasts = inject(Toasts);
  private sanitizer = inject(DomSanitizer);
  protected readonly tabs = TABS;
  protected tab = signal<VerificationStatus>('pending_review');
  protected items = signal<PendingVerification[]>([]);
  protected loading = signal(true);
  protected busy = signal(false);
  protected preview = signal<{ url: SafeResourceUrl; raw: string; pdf: boolean } | null>(null);
  protected notes: Record<string, string> = {};

  async ngOnInit(): Promise<void> {
    await this.load();
  }

  protected async select(t: VerificationStatus): Promise<void> {
    this.tab.set(t);
    await this.load();
  }

  private async load(): Promise<void> {
    this.loading.set(true);
    try {
      this.items.set(await this.api.verifications(this.tab()));
    } catch (e) {
      this.toasts.error(errorMessage(e));
    } finally {
      this.loading.set(false);
    }
  }

  protected async view(doc: DocumentOut): Promise<void> {
    try {
      const blob = await this.api.documentBlob(doc.id);
      const raw = URL.createObjectURL(blob);
      this.preview.set({ raw, url: this.sanitizer.bypassSecurityTrustResourceUrl(raw), pdf: doc.content_type === 'application/pdf' });
    } catch (e) {
      this.toasts.error(errorMessage(e));
    }
  }

  protected closePreview(): void {
    const p = this.preview();
    if (p) URL.revokeObjectURL(p.raw); // never keep decrypted documents around
    this.preview.set(null);
  }

  protected async approve(s: PendingVerification): Promise<void> {
    await this.decide(s, () => this.api.approve(s.student_id, this.notes[s.student_id]), `${s.full_name} verified`);
  }

  protected async reject(s: PendingVerification): Promise<void> {
    const note = (this.notes[s.student_id] ?? '').trim();
    if (note.length < 3) {
      this.toasts.error('Add a short note explaining the rejection. The student will see it.');
      return;
    }
    await this.decide(s, () => this.api.reject(s.student_id, note), `${s.full_name} rejected`);
  }

  private async decide(s: PendingVerification, call: () => Promise<unknown>, done: string): Promise<void> {
    this.busy.set(true);
    try {
      await call();
      this.toasts.success(done);
      this.items.update((list) => list.filter((x) => x.student_id !== s.student_id));
    } catch (e) {
      this.toasts.error(errorMessage(e));
    } finally {
      this.busy.set(false);
    }
  }

  protected label = (t: VerificationStatus) => VERIFICATION_STATUS[t].label;
  protected when = dateTime;

  ngOnDestroy(): void {
    this.closePreview();
  }
}
