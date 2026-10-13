import { Injectable, inject } from '@angular/core';
import { Observable, Subject } from 'rxjs';

import { Auth } from './auth';
import { LatLng, TrackEvent } from './models';

const TERMINAL = new Set(['completed', 'cancelled', 'expired']);
/** Server close codes that mean "don't bother reconnecting". */
const FATAL_CLOSE = new Set([4401, 4404, 4409]);

export interface RideSocket {
  events: Observable<TrackEvent>;
  send(pos: LatLng): void;
  close(): void;
}

/** WebSocket client for /track/rides/{id}: first-message auth, auto-reconnect with backoff. */
@Injectable({ providedIn: 'root' })
export class Tracking {
  private auth = inject(Auth);

  connect(rideId: string): RideSocket {
    const events = new Subject<TrackEvent>();
    let ws: WebSocket | null = null;
    let closedByUs = false;
    let attempt = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const url = `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/api/v1/track/rides/${rideId}`;

    const open = () => {
      ws = new WebSocket(url);
      ws.onopen = () => {
        attempt = 0;
        ws?.send(JSON.stringify({ type: 'auth', token: this.auth.accessToken }));
      };
      ws.onmessage = (msg) => {
        const event = JSON.parse(msg.data) as TrackEvent;
        events.next(event);
        if (event.type === 'status' && TERMINAL.has(event.status)) closedByUs = true;
      };
      ws.onclose = (e) => {
        if (closedByUs || FATAL_CLOSE.has(e.code)) {
          events.complete();
          return;
        }
        attempt += 1;
        timer = setTimeout(open, Math.min(1000 * 2 ** attempt, 15000));
      };
    };
    open();

    return {
      events: events.asObservable(),
      send: (pos) => {
        if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify(pos));
      },
      close: () => {
        closedByUs = true;
        clearTimeout(timer);
        ws?.close();
        events.complete();
      },
    };
  }
}
