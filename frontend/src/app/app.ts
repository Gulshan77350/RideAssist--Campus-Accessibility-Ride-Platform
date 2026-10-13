import { Component } from '@angular/core';
import { RouterOutlet } from '@angular/router';

import { ToastHost } from './core/toast';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, ToastHost],
  template: `<router-outlet /><app-toasts />`,
})
export class App {}
