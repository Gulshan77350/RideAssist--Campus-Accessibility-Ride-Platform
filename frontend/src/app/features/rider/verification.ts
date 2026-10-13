import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { Api, errorMessage } from '../../core/api';
import { Auth } from '../../core/auth';
import { VERIFICATION_STATUS, dateTime } from '../../core/format';
import { DocumentType, VerificationStatusOut } from '../../core/models';
import { Toasts } from '../../core/toast';

const CAN_UPLOAD = new Set(['awaiting_document', 'rejected', 'pending_review']);

@Component({
  selector: 'app-verification',
  imports: [FormsModule],
  template: `
    <h1>PwD verification</h1>
    @if (status(); as s) {
      <div class="grid-2">
        <section class="card stack" aria-labelledby="st">
          <div class="spread">
            <h2 id="st">Status</h2>
            <span [class]="'badge ' + label().tone">{{ label().label }}</span>
          </div>
          <p>{{ s.next_step }}</p>
          @if (s.review_note) {
            <div class="alert" [class.alert-danger]="s.verification_status === 'rejected'">
              <strong>Note from the disability cell:</strong> {{ s.review_note }}
            </div>
          }
          @if (s.verification_status === 'verified') {
            <p class="small muted">Verified on {{ when(s.verified_at) }}. Valid until {{ when(s.verification_expires_at) }}.</p>
          }
          <ol class="journey small">
            <li class="done">College email confirmed</li>
            <li [class.done]="s.verification_status === 'verified'">
              Matched against the college PwD roster, or certificate approved
            </li>
            <li [class.done]="s.can_book_rides">Ride booking unlocked</li>
          </ol>
        </section>

        @if (canUpload()) {
          <section class="card stack" aria-labelledby="up">
            <h2 id="up">Upload a document</h2>
            <p class="small muted">
              Upload your UDID card or a disability certificate issued by your college. Files are encrypted
              before storage and seen only by your college's disability-cell officer.
            </p>
            @if (s.latest_document; as d) {
              <div class="alert alert-info small">
                Last upload: {{ d.doc_type === 'udid' ? 'UDID card' : 'College certificate' }} on {{ when(d.uploaded_at) }}
                ({{ d.status }}). Uploading again replaces it in the review queue.
              </div>
            }
            <div class="field">
              <label for="type">Document type</label>
              <select id="type" [(ngModel)]="docType">
                <option value="udid">UDID card (Unique Disability ID)</option>
                <option value="college_certificate">College-issued certificate</option>
              </select>
            </div>
            <div class="field">
              <label for="file">File</label>
              <input id="file" type="file" accept="image/jpeg,image/png,application/pdf" (change)="pick($event)" aria-describedby="file-hint" />
              <span id="file-hint" class="hint">JPEG, PNG or PDF, up to 5 MB.</span>
            </div>
            <button class="btn btn-primary" type="button" (click)="upload()" [disabled]="!file() || busy()">
              {{ busy() ? 'Uploading…' : 'Submit for review' }}
            </button>
          </section>
        }
      </div>
    } @else {
      <p class="muted" aria-live="polite">Loading…</p>
    }
  `,
  styles: `
    .journey { margin: 0; padding-left: 1.25rem; display: grid; gap: 0.35rem; }
    .journey li.done { color: var(--success); font-weight: 600; }
    .journey li.done::marker { content: '✓  '; }
  `,
})
export class Verification implements OnInit {
  private api = inject(Api);
  private auth = inject(Auth);
  private toasts = inject(Toasts);
  protected status = signal<VerificationStatusOut | null>(null);
  protected file = signal<File | null>(null);
  protected busy = signal(false);
  protected docType: DocumentType = 'udid';
  protected label = computed(() => VERIFICATION_STATUS[this.status()?.verification_status ?? 'pending_otp']);
  protected canUpload = computed(() => CAN_UPLOAD.has(this.status()?.verification_status ?? ''));

  async ngOnInit(): Promise<void> {
    await this.load();
  }

  private async load(): Promise<void> {
    try {
      this.status.set(await this.api.verificationStatus());
      await this.auth.loadMe(); // keep the booking gate in sync
    } catch (e) {
      this.toasts.error(errorMessage(e));
    }
  }

  protected pick(event: Event): void {
    const f = (event.target as HTMLInputElement).files?.[0] ?? null;
    if (f && f.size > 5 * 1024 * 1024) {
      this.toasts.error('That file is larger than 5 MB');
      return;
    }
    this.file.set(f);
  }

  protected async upload(): Promise<void> {
    this.busy.set(true);
    try {
      await this.api.uploadDocument(this.docType, this.file()!);
      this.toasts.success('Document submitted. The disability cell will review it.');
      this.file.set(null);
      await this.load();
    } catch (e) {
      this.toasts.error(errorMessage(e));
    } finally {
      this.busy.set(false);
    }
  }

  protected when = dateTime;
}
