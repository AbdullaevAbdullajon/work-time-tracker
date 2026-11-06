"""Local web interface. Run with python app.py."""
import os
import time
from collections import defaultdict
from datetime import datetime
from html import escape
from pathlib import Path
from urllib.parse import parse_qs, urlencode
from wsgiref.simple_server import make_server

from tracker import REPORT_ZONE, Tracker, ValidationError, period

ROOT = Path(__file__).resolve().parent


def duration(seconds):
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f'{hours:02}:{minutes:02}:{seconds:02}'


def local_time(stamp):
    return datetime.fromtimestamp(stamp, REPORT_ZONE).strftime('%d.%m.%Y %H:%M:%S')


def page(employees, records, first, last, now, error='', notice=''):
    filter_query = escape(urlencode({'from': first, 'to': last}))
    report = defaultdict(list)
    for row, seconds in records or ():
        report[row['employee_id']].append((row, seconds))

    cards = []
    summaries = []
    for employee in employees:
        employee_id = employee['id']
        name = escape(employee['name'])
        fields = f'<input type="hidden" name="employee" value="{employee_id}">'
        if employee['timer_id'] is not None:
            elapsed = max(0, now - employee['started_at'])
            action = f"""
                <p class="running">В работе · <strong data-elapsed="{elapsed}">{duration(elapsed)}</strong></p>
                <p class="task">{escape(employee['task'])}</p>
                <form method="post" action="/stop?{filter_query}">
                  {fields}<input type="hidden" name="timer" value="{employee['timer_id']}">
                  <button class="stop">Остановить</button>
                </form>
            """
        else:
            action = f"""
                <form method="post" action="/start?{filter_query}">
                  {fields}<label>Описание задачи<input name="task" placeholder="Например, сверка отчёта"
                      required maxlength="200"></label><button>Запустить</button>
                </form>
            """
        cards.append(f'<article><h3>{name}</h3>{action}</article>')
        entries = report[employee_id]
        total = duration(sum(seconds for _, seconds in entries))
        rows = ''.join(
            f"<tr><td>{escape(row['task'])}</td><td>{local_time(row['started_at'])}</td>"
            f"<td>{local_time(row['stopped_at'])}</td><td>{duration(seconds)}</td></tr>"
            for row, seconds in entries
        )
        details = (
            '<div class="scroll"><table><thead><tr><th>Задача</th><th>Начало</th>'
            '<th>Остановка</th><th>В периоде</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></div>'
            if rows else '<p class="muted">За выбранный период записей нет.</p>'
        )
        summaries.append(f'<article><h3>{name} <span class="total">{total}</span></h3>{details}</article>')

    message = f'<p class="error" role="alert">{escape(error)}</p>' if error else ''
    if notice:
        message += f'<p class="success" role="status">{escape(notice)}</p>'
    cards_html = ''.join(cards) or '<p class="muted">Добавьте первого сотрудника, чтобы запустить таймер.</p>'
    report_html = ''
    if records is not None:
        report_html = ''.join(summaries) or '<p class="muted">Пока нет сотрудников для отчёта.</p>'
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Учёт рабочего времени</title><link rel="stylesheet" href="/style.css"></head>
<body><main>
<header><p class="eyebrow">РАБОЧЕЕ ВРЕМЯ · UTC+05:00</p><h1>Учёт рабочего времени</h1>
<p>Запускайте таймеры задач и просматривайте отработанное время за период.</p></header>
{message}
<section><h2>Сотрудники и таймеры</h2>
<form method="post" class="bar" action="/employees?{filter_query}">
<label>Имя сотрудника<input name="name" required maxlength="80" placeholder="Например, Анна Ветрова"></label>
<button>Добавить сотрудника</button></form>
<div class="cards">{cards_html}</div></section>
<section><h2>Отчёт за период</h2>
<p class="muted">Даты включительно. В отчёт входят завершённые задачи и время внутри выбранных дат.</p>
<form method="get" action="/" class="bar">
<label>С<input type="date" name="from" value="{escape(first)}" required></label>
<label>По<input type="date" name="to" value="{escape(last)}" required></label>
<button>Показать отчёт</button></form>
<div class="summaries">{report_html}</div></section>
</main><script src="/timer.js"></script></body></html>"""


def create_app(db_path, clock=time.time):
    tracker = Tracker(db_path)
    notices = {'employees': 'Сотрудник добавлен.', 'start': 'Таймер запущен.', 'stop': 'Запись сохранена.'}
    assets = {'/style.css': 'text/css', '/timer.js': 'text/javascript'}

    def app(environ, start_response):
        def respond(status, body='', content_type='text/html', headers=()):
            data = body.encode('utf-8')
            start_response(status, [
                ('Content-Type', f'{content_type}; charset=utf-8'),
                ('Content-Length', str(len(data))),
                *headers,
            ])
            return [data]

        method, path = environ['REQUEST_METHOD'], environ['PATH_INFO']
        if method == 'GET' and path in assets:
            return respond('200 OK', (ROOT / path[1:]).read_text(encoding='utf-8'), assets[path])
        if (method, path) != ('GET', '/') and not (method == 'POST' and path[1:] in notices):
            return respond('404 Not Found', 'Страница не найдена.', 'text/plain')

        query = parse_qs(environ.get('QUERY_STRING', ''), keep_blank_values=True)
        now = int(clock())
        today = datetime.fromtimestamp(now, REPORT_ZONE).date()
        first = query.get('from', [today.replace(day=1).isoformat()])[0]
        last = query.get('to', [today.isoformat()])[0]
        error, records, bounds = '', None, None
        status = '200 OK'
        try:
            bounds = period(first, last)
        except ValidationError as exc:
            status, error = '400 Bad Request', str(exc)
        try:
            if method == 'POST':
                length = int(environ.get('CONTENT_LENGTH') or 0)
                if not 0 < length <= 8192:
                    raise ValidationError('Некорректный размер формы.')
                form = parse_qs(environ['wsgi.input'].read(length).decode('utf-8'), keep_blank_values=True)
                if path == '/employees':
                    tracker.add_employee(form.get('name', [''])[0])
                else:
                    employee_id = int(form.get('employee', [''])[0])
                    if path == '/start':
                        tracker.start(employee_id, form.get('task', [''])[0], now)
                    else:
                        tracker.stop(employee_id, int(form.get('timer', [''])[0]), now)
                redirect_query = {'notice': path[1:]}
                if bounds is not None:
                    redirect_query.update({'from': first, 'to': last})
                target = '/?' + urlencode(redirect_query)
                return respond('303 See Other', headers=[('Location', target)])
        except ValueError as exc:
            status = '400 Bad Request'
            error = str(exc) if isinstance(exc, ValidationError) else 'Некорректные данные формы.'
        if bounds is not None:
            records = tracker.report(*bounds)
        notice = notices.get(query.get('notice', [''])[0], '') if not error else ''
        return respond(status, page(tracker.employees(), records, first, last, now, error, notice))

    return app


if __name__ == '__main__':
    db_path = os.environ.get('TRACKER_DB', ROOT / 'tracker.sqlite3')
    with make_server('127.0.0.1', 8000, create_app(db_path)) as server:
        print('Откройте http://127.0.0.1:8000', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
