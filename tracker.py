"""SQLite storage and reporting rules."""
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone

REPORT_ZONE = timezone(timedelta(hours=5))


class ValidationError(ValueError):
    pass


def required(value, label, limit):
    value = value.strip()
    if not 1 <= len(value) <= limit:
        raise ValidationError(f'«{label}» должно содержать от 1 до {limit} символов.')
    return value


def period(first, last):
    try:
        start, end = date.fromisoformat(first), date.fromisoformat(last)
        if start > end or end == date.max:
            raise ValueError
    except ValueError as exc:
        raise ValidationError('Укажите корректный период. Начало должно быть не позже конца.') from exc
    return (
        int(datetime.combine(start, time.min, REPORT_ZONE).timestamp()),
        int(datetime.combine(end + timedelta(days=1), time.min, REPORT_ZONE).timestamp()),
    )


class Tracker:
    def __init__(self, path):
        self.path = path
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS employees (
                    id INTEGER PRIMARY KEY, name TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS entries (
                    id INTEGER PRIMARY KEY,
                    employee_id INTEGER NOT NULL REFERENCES employees(id),
                    task TEXT NOT NULL,
                    started_at INTEGER NOT NULL,
                    stopped_at INTEGER CHECK (stopped_at >= started_at)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_running_timer
                    ON entries(employee_id) WHERE stopped_at IS NULL;
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA foreign_keys = ON')
            with db:
                yield db
        finally:
            db.close()

    def add_employee(self, name):
        with self.connect() as db:
            return db.execute(
                'INSERT INTO employees (name) VALUES (?)',
                (required(name, 'Имя сотрудника', 80),),
            ).lastrowid

    def employees(self):
        with self.connect() as db:
            return db.execute("""
                SELECT e.id, e.name, t.id AS timer_id, t.task, t.started_at
                FROM employees e LEFT JOIN entries t
                    ON t.employee_id = e.id AND t.stopped_at IS NULL
                ORDER BY e.id
            """).fetchall()

    def start(self, employee_id, task, now):
        task = required(task, 'Описание задачи', 200)
        with self.connect() as db:
            if not db.execute('SELECT 1 FROM employees WHERE id = ?', (employee_id,)).fetchone():
                raise ValidationError('Сотрудник не найден.')
            result = db.execute("""
                INSERT INTO entries (employee_id, task, started_at) VALUES (?, ?, ?)
                ON CONFLICT(employee_id) WHERE stopped_at IS NULL DO NOTHING
            """, (employee_id, task, now))
            if result.rowcount == 0:
                raise ValidationError('У сотрудника уже запущен таймер.')

    def stop(self, employee_id, timer_id, now):
        with self.connect() as db:
            result = db.execute("""
                UPDATE entries SET stopped_at = MAX(started_at, ?)
                WHERE employee_id = ? AND id = ? AND stopped_at IS NULL
            """, (now, employee_id, timer_id))
            if result.rowcount == 0:
                raise ValidationError('Этот таймер уже остановлен или не найден.')

    def report(self, start, end):
        with self.connect() as db:
            rows = db.execute("""
                SELECT employee_id, task, started_at, stopped_at
                FROM entries
                WHERE stopped_at IS NOT NULL AND started_at < ?
                  AND (stopped_at > ? OR (started_at = ? AND stopped_at = started_at))
                ORDER BY started_at DESC, id DESC
            """, (end, start, start)).fetchall()
        return [
            (row, min(row['stopped_at'], end) - max(row['started_at'], start))
            for row in rows
        ]
