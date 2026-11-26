from datetime import datetime, timezone
import json
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from countercheck_web.settings import MENU, CHECKPOINTS, REPORTS
from .audio import AudioWorker

BG = '#f3f5ef'
GREEN = '#173b32'
BLUE = '#285bdb'
FIELDS = ('item', 'quantity', 'size', 'temperature', 'milk', 'modifiers', 'status')


class SearchChoice(ttk.Frame):
    def __init__(self, parent, label, choices, value):
        super().__init__(parent)
        self.choices = choices
        ttk.Label(self, text=label, style='Small.TLabel').pack(anchor='w')
        self.value = tk.StringVar(value=value or '')
        self.entry = ttk.Entry(self, textvariable=self.value)
        self.entry.pack(fill='x', pady=(5, 3))
        self.list = tk.Listbox(self, height=3, exportselection=False, font=('Segoe UI', 10),
                               relief='flat', borderwidth=1, highlightbackground='#d7e1da', highlightthickness=1)
        self.list.pack(fill='x')
        self.list.bind('<<ListboxSelect>>', self.select)
        self.entry.bind('<Down>', self.focus_list)
        self.list.bind('<Return>', self.select)
        self.value.trace_add('write', lambda *_: self.refresh())
        self.refresh()

    def refresh(self):
        query = self.value.get().strip().lower()
        matches = [choice for choice in self.choices if query in choice.lower()]
        self.list.delete(0, 'end')
        for choice in matches:
            self.list.insert('end', choice)

    def focus_list(self, event):
        if self.list.size():
            self.list.focus_set()
            self.list.selection_set(0)
        return 'break'

    def select(self, event=None):
        selected = self.list.curselection()
        if selected:
            value = self.list.get(selected[0])
            self.value.set(value)
        return 'break'


class CounterCheckDesktop:
    def __init__(self, root, autoload=True):
        self.root = root
        self.root.title('CounterCheck Desktop | Local Whisper')
        self.root.geometry('1220x850')
        self.root.minsize(1000, 840)
        self.root.configure(bg=BG)
        self.events = queue.Queue(maxsize=1000)
        self.engine = self.worker = None
        self.running = self.loading = self.closed = self.closing = False
        self.confirmed = self.manual = self.lost = self.pending = False
        self.epoch = 0
        self.items, self.raw, self.decisions, self.corrections, self.receipts = [], [], [], [], []
        self.edited = set()
        self.editor = None
        self.model = tk.StringVar(value='baseline')
        self.search = tk.StringVar()
        self.status = tk.StringVar(value='Loading local Whisper…' if autoload else 'Test mode')
        self.capture = tk.StringVar(value='Microphone off')
        self.partial = tk.StringVar()
        self.notice = tk.StringVar(value='Values marked * were filled by default. Check them with the customer.')
        self.metrics = tk.StringVar(value='Response —   |   RTF —')
        self.reviewed = tk.BooleanVar()
        self.device = tk.StringVar()
        self.devices = {}
        self._build()
        self.search.trace_add('write', lambda *_: self.render())
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.root.after(80, self.poll)
        if autoload:
            self.load_model()

    def _build(self):
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('TFrame', background=BG)
        style.configure('TLabel', background=BG, foreground=GREEN, font=('Segoe UI', 10))
        style.configure('Small.TLabel', font=('Segoe UI', 9), foreground='#526c5d')
        style.configure('TButton', padding=(12, 8), font=('Segoe UI', 10))
        for name, color in [('Green', '#177456'), ('Blue', BLUE), ('Red', '#b6373f')]:
            style.configure(name+'.TButton', background=color, foreground='white')
            style.map(name+'.TButton', background=[('disabled', '#adbeb3'), ('active', color)])
        style.configure('Treeview', font=('Segoe UI', 10), rowheight=35, background='white', fieldbackground='white')
        style.configure('Treeview.Heading', background='#e4eee6', foreground=GREEN, font=('Segoe UI', 9, 'bold'), padding=9)
        header = tk.Frame(self.root, bg=GREEN, padx=24, pady=15)
        header.pack(fill='x')
        tk.Label(header, text='CounterCheck', bg=GREEN, fg='white', font=('Segoe UI', 22, 'bold')).pack(side='left')
        tk.Label(header, text='DESKTOP   ·   LOCAL WHISPER', bg=GREEN, fg='#b9dbc7', font=('Segoe UI', 10)).pack(side='left', padx=24)
        tk.Label(header, textvariable=self.status, bg=GREEN, fg='#d5ecdf', font=('Segoe UI', 10)).pack(side='right')
        shell = ttk.Frame(self.root, padding=20)
        shell.pack(fill='both', expand=True)
        bar = ttk.Frame(shell)
        bar.pack(fill='x', pady=(0, 10))
        self.start_button = ttk.Button(bar, text='▶  Start microphone', style='Green.TButton', command=self.start)
        self.start_button.pack(side='left', padx=(0, 8))
        self.stop_button = ttk.Button(bar, text='■  Stop microphone', style='Red.TButton', command=self.stop)
        self.stop_button.pack(side='left', padx=(0, 14))
        ttk.Label(bar, textvariable=self.capture).pack(side='left')
        ttk.Button(bar, text='Replay audio', command=self.replay).pack(side='right')
        options = ttk.Frame(shell)
        options.pack(fill='x', pady=(0, 12))
        ttk.Label(options, text='Microphone').pack(side='left', padx=(0, 8))
        self.device_select = ttk.Combobox(options, textvariable=self.device, state='readonly', width=43)
        self.device_select.pack(side='left')
        ttk.Button(options, text='Refresh', command=self.refresh_devices).pack(side='left', padx=6)
        ttk.Label(options, text='Model').pack(side='left', padx=(20, 8))
        self.model_select = ttk.Combobox(options, textvariable=self.model, values=list(CHECKPOINTS), width=12, state='readonly')
        self.model_select.pack(side='left')
        self.model_select.bind('<<ComboboxSelected>>', lambda _: self.load_model())
        ttk.Label(options, textvariable=self.metrics, style='Small.TLabel').pack(side='right')
        self.refresh_devices()
        heading = ttk.Frame(shell)
        heading.pack(fill='x', pady=(0, 8))
        ttk.Label(heading, text='Customer order', font=('Segoe UI', 18, 'bold')).pack(side='left')
        self.order_state = ttk.Label(heading, text='Draft', foreground='#98641c')
        self.order_state.pack(side='left', padx=14)
        ttk.Button(heading, text='New order', command=self.new_order).pack(side='right')
        tk.Label(shell, text='COMPLETE DRAFT  ·  Missing details: 1 / medium / hot / whole milk / no extras. Defaults are marked *.',
                 background='#fff2d9', foreground='#8b601c', padx=12, pady=9, anchor='w', font=('Segoe UI', 10)).pack(fill='x')
        searchbar = ttk.Frame(shell)
        searchbar.pack(fill='x', pady=(12, 10))
        ttk.Label(searchbar, text='Find an order item').pack(side='left', padx=(0, 12))
        self.search_entry = ttk.Entry(searchbar, textvariable=self.search, width=35)
        self.search_entry.pack(side='left', fill='x', expand=True)
        ttk.Button(searchbar, text='Clear', command=lambda: self.search.set('')).pack(side='left', padx=6)
        ttk.Button(searchbar, text='+ Add item', style='Blue.TButton', command=lambda: self.open_editor(-1)).pack(side='left', padx=6)
        self.edit_button = ttk.Button(searchbar, text='✎ Edit selected', style='Blue.TButton', command=self.edit_selected)
        self.edit_button.pack(side='left')
        table = ttk.Frame(shell)
        table.pack(fill='both', expand=True)
        self.table = ttk.Treeview(table, columns=FIELDS, show='headings', selectmode='browse', height=4)
        widths = (155, 60, 105, 105, 135, 190, 135)
        for key, width in zip(FIELDS, widths):
            self.table.heading(key, text={'item':'Drink', 'quantity':'Qty', 'modifiers':'Extras'}.get(key, key.title()))
            self.table.column(key, width=width, minwidth=50, stretch=True)
        self.table.tag_configure('default', foreground='#93601a')
        self.table.tag_configure('edited', foreground=BLUE)
        self.table.tag_configure('confirmed', foreground='#177456')
        self.table.bind('<Double-1>', lambda _: self.edit_selected())
        scroll = ttk.Scrollbar(table, orient='vertical', command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        self.table.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        self.count = ttk.Label(shell, text='0 items', style='Small.TLabel')
        self.count.pack(anchor='w', pady=(5, 2))
        ttk.Label(shell, textvariable=self.notice, foreground='#8b601c', wraplength=1120).pack(fill='x', pady=5)
        confirmbar = ttk.Frame(shell)
        confirmbar.pack(fill='x', pady=(5, 12))
        self.review_button = ttk.Checkbutton(confirmbar, text='I checked the order with the customer', variable=self.reviewed, command=self.controls)
        self.review_button.pack(side='left')
        self.confirm_button = ttk.Button(confirmbar, text='✓ Confirm order', style='Green.TButton', command=self.confirm)
        self.confirm_button.pack(side='right')
        self.unlock_button = ttk.Button(confirmbar, text='Correct confirmed order', command=self.unlock)
        self.unlock_button.pack(side='right', padx=8)
        ttk.Button(confirmbar, text='Export JSON', command=self.export).pack(side='right')
        ttk.Label(shell, text='LIVE TRANSCRIPT', style='Small.TLabel').pack(anchor='w')
        self.transcript = tk.Text(shell, height=3, font=('Segoe UI', 11), bg='white', fg=GREEN,
                                  relief='flat', padx=10, pady=8, wrap='word', state='disabled')
        self.transcript.pack(fill='x', pady=5)
        ttk.Label(shell, textvariable=self.partial, foreground='#177456', wraplength=1120).pack(fill='x')
        foot = ttk.Frame(shell)
        foot.pack(fill='x', pady=(8, 0))
        ttk.Label(foot, text='Desktop 2026.11.26  ·  Recording continues across orders  ·  Audio is not saved', style='Small.TLabel').pack(side='left')
        ttk.Button(foot, text='Menu choices', command=self.show_choices).pack(side='right')
        ttk.Button(foot, text='Experiments', command=self.show_experiments).pack(side='right', padx=8)
        self.controls()

    def emit(self, event):
        try:
            self.events.put_nowait(event)
        except queue.Full:
            self.lost = True

    def load_model(self):
        if self.running or self.loading:
            return
        name = self.model.get()
        self.loading = True
        self.engine = None
        self.status.set('Loading '+name+'…')
        self.controls()
        def load():
            try:
                from countercheck_web.engine import WhisperEngine
                engine = WhisperEngine(name)
                self.emit(dict(type='ready', engine=engine, name=name))
            except Exception as error:
                self.emit(dict(type='model_error', text=str(error)))
        threading.Thread(target=load, daemon=True).start()

    def refresh_devices(self):
        try:
            import sounddevice as sd
            self.devices = {'System default microphone': None}
            for index, device in enumerate(sd.query_devices()):
                if device['max_input_channels']:
                    self.devices[f'{index} · {device["name"]}'] = index
            self.device_select['values'] = list(self.devices)
            if self.device.get() not in self.devices:
                self.device.set('System default microphone')
        except Exception as error:
            self.notice.set(f'Microphone setup: {error}. Run setup_desktop.bat.')

    def controls(self):
        ready = self.engine is not None and not self.loading and not self.closing
        self.start_button['state'] = 'normal' if ready and not self.running else 'disabled'
        self.stop_button['state'] = 'normal' if self.running and not self.closing else 'disabled'
        self.model_select['state'] = 'readonly' if ready and not self.running else 'disabled'
        self.device_select['state'] = 'readonly' if not self.running else 'disabled'
        valid = bool(self.items) and all(self.valid_item(item) for item in self.items)
        self.confirm_button['state'] = 'normal' if valid and self.reviewed.get() and not (self.confirmed or self.pending or self.lost or self.editor) else 'disabled'
        self.edit_button['state'] = 'normal' if not self.confirmed and self.items else 'disabled'
        self.unlock_button['state'] = 'normal' if self.confirmed else 'disabled'
        self.review_button['state'] = 'disabled' if self.confirmed else 'normal'
        self.order_state['text'] = '✓ Confirmed' if self.confirmed else 'Edited draft' if self.manual else 'Draft'

    @staticmethod
    def valid_item(item):
        return (item.get('item') in MENU['drink'] and isinstance(item.get('quantity'), int)
                and 1 <= item['quantity'] <= 20 and all(item.get(key) in MENU[key] for key in ('size', 'temperature', 'milk'))
                and all(extra in MENU['extras'] for extra in item.get('modifiers', [])))

    def start(self, path=None):
        if self.running or self.loading or self.engine is None or self.closing:
            return
        self.running = True
        self.capture.set('Starting audio…')
        self.worker = AudioWorker(self.engine, self.emit, self.epoch)
        self.worker.start(device=self.devices.get(self.device.get()), path=path)
        self.controls()

    def stop(self):
        if self.worker and self.running:
            self.worker.stop()
            self.capture.set('Finishing last phrase…')
            self.stop_button['state'] = 'disabled'

    def replay(self):
        if self.running:
            return
        path = filedialog.askopenfilename(parent=self.root, title='Replay a human recording', filetypes=[('Audio', '*.wav *.flac *.ogg'), ('All files', '*.*')])
        if path:
            self.start(path=path)

    def new_order(self):
        if self.editor:
            self.editor.destroy()
            self.editor = None
        self.epoch += 1
        if self.worker and self.running:
            self.worker.new_order(self.epoch)
        self.items, self.raw, self.decisions, self.corrections = [], [], [], []
        self.edited.clear()
        self.manual = self.confirmed = self.pending = self.lost = False
        self.reviewed.set(False)
        self.search.set('')
        self.partial.set('')
        self.notice.set('New order. Listening continues.' if self.running else 'Ready for a new order.')
        self.render_transcript()
        self.render()

    def receive(self, event):
        kind = event['type']
        if kind == 'ready':
            self.engine, self.loading = event['engine'], False
            self.status.set(f'Whisper {event["name"]} · {self.engine.device.upper()} · offline')
        elif kind == 'model_error':
            self.loading = False
            self.status.set('Model unavailable')
            self.notice.set(event['text'])
        elif kind == 'capture_started':
            self.capture.set('● Listening continuously')
        elif kind in ('error', 'audio_loss'):
            self.lost = True
            self.notice.set(event['text'])
        elif kind == 'stopped':
            self.running = False
            self.capture.set('Microphone off')
            self.partial.set('')
        elif kind in ('partial', 'final') and event.get('epoch') == self.epoch:
            self.pending = kind == 'partial' and bool(event.get('text'))
            if event.get('text'):
                self.reviewed.set(False)
            metrics = event.get('metrics', {})
            if metrics:
                self.metrics.set(f'Response {metrics["inference_response_ms"]:.0f} ms   |   RTF {metrics["rtf"]:.2f}')
                if metrics.get('dropped_frames'):
                    self.lost = True
            if kind == 'partial':
                self.partial.set(event['text'])
            else:
                self.partial.set('')
                if event.get('text'):
                    self.raw.append(event['text'])
                    self.raw = self.raw[-1000:]
                    self.render_transcript()
                if event.get('order') and not self.confirmed:
                    order = event['order']
                    self.decisions.extend(order.get('menu_decisions', []))
                    self.decisions = self.decisions[-1000:]
                    if not self.manual and not self.editor:
                        self.items = order['items']
                        self.notice.set(' · '.join(order['warnings'][:2]) or 'Draft ready. Review with the customer, then confirm.')
                    else:
                        self.notice.set('Your edits are protected. Check new speech in the live transcript.')
                if event.get('order_overflow'):
                    self.lost = True
                    self.notice.set('Order too long. Start a new order and repeat.')
                self.render()
        self.controls()

    def poll(self):
        if self.closed:
            return
        for _ in range(100):
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            self.receive(event)
        self.root.after(80, self.poll)

    def render_transcript(self):
        self.transcript['state'] = 'normal'
        self.transcript.delete('1.0', 'end')
        self.transcript.insert('end', '\n'.join(self.raw))
        self.transcript.see('end')
        self.transcript['state'] = 'disabled'

    def render(self):
        selected = self.table.selection()
        self.table.delete(*self.table.get_children())
        tokens = self.search.get().lower().split()
        for index, item in enumerate(self.items):
            haystack = ' '.join(str(item.get(key, '')) for key in FIELDS).lower()
            if not all(token in haystack for token in tokens):
                continue
            defaults = item.get('assumed_fields', [])
            values = []
            for key in FIELDS[:-1]:
                value = ', '.join(item.get(key, [])) or 'No extras' if key == 'modifiers' else str(item.get(key, ''))
                values.append(value + (' *' if key in defaults else ''))
            status = 'Confirmed' if self.confirmed else 'Edited' if index in self.edited else 'Check defaults' if defaults else 'Review match'
            values.append(status)
            tag = 'confirmed' if self.confirmed else 'edited' if index in self.edited else 'default' if defaults else ''
            self.table.insert('', 'end', iid=str(index), values=values, tags=(tag,))
        if selected and self.table.exists(selected[0]):
            self.table.selection_set(selected[0])
        self.count['text'] = f'{len(self.table.get_children())} shown / {len(self.items)} items  ·  Double-click a row to correct it'
        self.controls()

    def edit_selected(self):
        selection = self.table.selection()
        if selection:
            self.open_editor(int(selection[0]))
        else:
            self.notice.set('Select a row first, or double-click the item you want to correct.')

    def open_editor(self, index):
        if self.confirmed or self.editor:
            return
        item = self.items[index] if index >= 0 else dict(item='', quantity=1, size='medium', temperature='hot', milk='whole milk', modifiers=[])
        editor = self.editor = tk.Toplevel(self.root)
        editor.title('CounterCheck | Correct order item')
        editor.geometry('650x610')
        editor.resizable(False, False)
        editor.configure(bg=BG)
        editor.transient(self.root)
        editor.grab_set()
        body = ttk.Frame(editor, padding=22)
        body.pack(fill='both', expand=True)
        ttk.Label(body, text='Correct item' if index >= 0 else 'Add item', font=('Segoe UI', 20, 'bold')).pack(anchor='w')
        ttk.Label(body, text='Type to filter each category, then click a matching choice. Listening continues.', style='Small.TLabel').pack(anchor='w', pady=(3, 15))
        grid = ttk.Frame(body)
        grid.pack(fill='x')
        grid.columnconfigure((0, 1, 2), weight=1)
        choices = {}
        for key, field, row, col in [('drink','item',0,0), ('size','size',0,1), ('temperature','temperature',0,2), ('milk','milk',1,0)]:
            choices[field] = SearchChoice(grid, key.title(), MENU[key], item[field])
            choices[field].grid(row=row, column=col, sticky='nsew', padx=(0, 12), pady=(0, 14))
        qty_frame = ttk.Frame(grid)
        qty_frame.grid(row=1, column=1, sticky='nw')
        ttk.Label(qty_frame, text='Quantity (1–20)', style='Small.TLabel').pack(anchor='w')
        quantity = tk.StringVar(value=str(item['quantity']))
        ttk.Spinbox(qty_frame, from_=1, to=20, textvariable=quantity, width=12).pack(pady=6)
        ttk.Label(body, text='Extras / preferences', style='Small.TLabel').pack(anchor='w')
        extras_query = tk.StringVar()
        ttk.Entry(body, textvariable=extras_query).pack(fill='x', pady=(4, 6))
        extras_frame = ttk.Frame(body)
        extras_frame.pack(fill='x')
        extras = {value:tk.BooleanVar(value=value in item.get('modifiers', [])) for value in MENU['extras']}
        def refresh_extras(*_):
            for child in extras_frame.winfo_children():
                child.destroy()
            for index, (value, variable) in enumerate((pair for pair in extras.items() if extras_query.get().lower() in pair[0])):
                ttk.Checkbutton(extras_frame, text=value, variable=variable).grid(row=index//3, column=index%3, sticky='w', padx=(0, 14), pady=3)
        extras_query.trace_add('write', refresh_extras)
        refresh_extras()
        error = tk.StringVar()
        ttk.Label(body, textvariable=error, foreground='#b6373f').pack(anchor='w', pady=7)
        buttons = ttk.Frame(body)
        buttons.pack(side='bottom', fill='x')
        def close_editor():
            self.editor = None
            editor.destroy()
            self.controls()
        def save():
            try:
                candidate = {field:choice.value.get().strip().lower() for field, choice in choices.items()}
                candidate.update(quantity=int(quantity.get()), modifiers=[value for value, variable in extras.items() if variable.get()], assumed_fields=[], status='review')
                if not self.valid_item(candidate):
                    raise ValueError('Choose listed menu options and a quantity from 1 to 20.')
            except ValueError:
                error.set('Choose listed menu options and a whole-number quantity from 1 to 20.')
                return
            target = len(self.items) if index < 0 else index
            before = json.loads(json.dumps(self.items[index])) if index >= 0 else None
            if index < 0:
                self.items.append(candidate)
            else:
                self.items[index] = candidate
            self.edited.add(target)
            self.corrections.append(dict(at=datetime.now(timezone.utc).isoformat(), before=before, after=candidate))
            self.manual = True
            self.reviewed.set(False)
            close_editor()
            self.notice.set('Correction saved. Incoming transcription cannot overwrite your edits.')
            self.render()
        def remove():
            before = self.items.pop(index)
            self.edited = {i if i < index else i-1 for i in self.edited if i != index}
            self.corrections.append(dict(at=datetime.now(timezone.utc).isoformat(), action='remove', before=before))
            self.manual = True
            self.reviewed.set(False)
            close_editor()
            self.render()
        if index >= 0:
            ttk.Button(buttons, text='Remove item', command=remove).pack(side='left')
        ttk.Button(buttons, text='Save correction', style='Blue.TButton', command=save).pack(side='right')
        ttk.Button(buttons, text='Cancel', command=close_editor).pack(side='right', padx=8)
        editor.protocol('WM_DELETE_WINDOW', close_editor)
        self.editor_fields, self.editor_quantity = choices, quantity
        self.editor_save, self.editor_error = save, error
        self.controls()

    def confirm(self):
        if not self.reviewed.get() or not self.items or not all(self.valid_item(item) for item in self.items) or self.confirmed or self.pending or self.lost or self.editor:
            return
        self.confirmed = True
        self.receipts.append(dict(at=datetime.now(timezone.utc).isoformat(), items=json.loads(json.dumps(self.items))))
        self.receipts = self.receipts[-200:]
        self.notice.set('Order confirmed. Click New order for the next customer; the microphone stays on.')
        self.render()

    def unlock(self):
        self.confirmed = False
        self.manual = True
        self.reviewed.set(False)
        self.render()

    def export(self):
        path = filedialog.asksaveasfilename(parent=self.root, defaultextension='.json', initialfile='countercheck-order.json', filetypes=[('JSON', '*.json')])
        if path:
            from pathlib import Path
            Path(path).write_text(json.dumps(dict(model=self.model.get(), confirmed=self.confirmed, items=self.items, raw_transcript=self.raw,
                                                 menu_decisions=self.decisions, corrections=self.corrections, confirmed_orders=self.receipts), indent=2), encoding='utf-8')

    def show_text(self, title, text):
        window = tk.Toplevel(self.root)
        window.title('CounterCheck | '+title)
        window.geometry('800x500')
        box = tk.Text(window, font=('Consolas', 10), wrap='word', padx=15, pady=15)
        box.pack(fill='both', expand=True)
        box.insert('end', text)
        box['state'] = 'disabled'

    def show_choices(self):
        self.show_text('Menu choices', json.dumps(self.decisions, indent=2) if self.decisions else 'No spelling/menu substitutions yet. * marks a default, not speech evidence.')

    def show_experiments(self):
        path = REPORTS / 'evaluation.json'
        if not path.exists():
            self.show_text('Experiments', 'No local evaluation report. Run reproduce_whisper.bat to generate real measurements.')
            return
        report = json.loads(path.read_text(encoding='utf-8'))
        lines = ['MODEL          CONDITION             WER      CER       RTF    AUDIO/S', '']
        for row in report.get('summaries', []):
            lines.append(f'{row["model"]:<14} {row["condition"]:<20} {row["wer"]*100:6.1f}% {row["cer"]*100:6.1f}% {row["rtf"]:8.3f} {row["throughput"]:8.2f}')
        lines += ['', report.get('interpretation', ''), '', 'WER = (S + D + I) / N. These pilot scores are not complete cafe-menu validation.']
        self.show_text('Experiments', '\n'.join(lines))

    def close(self):
        self.closing = True
        self.stop()
        self.controls()
        if self.worker and self.worker.thread and self.worker.thread.is_alive():
            self.root.after(100, self.close)
            return
        self.closed = True
        self.root.destroy()


def main():
    root = tk.Tk()
    try:
        CounterCheckDesktop(root)
        root.mainloop()
    except Exception as error:
        messagebox.showerror('CounterCheck', str(error), parent=root)
        raise


if __name__ == '__main__':
    main()
