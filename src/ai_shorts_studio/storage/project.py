import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from ai_shorts_studio.domain.model import Project


@dataclass(frozen=True)
class ExportResult:
    attempt_id: str
    clip_id: str
    video: Path
    cover: Path | None = None
    frame_us: int | None = None
    hook: str = ""
    source_start_us: int | None = None


class Store:
    def __init__(self, path, recover=True):
        self.path = Path(path)
        self.db = sqlite3.connect(self.path)
        self.db.execute("PRAGMA journal_mode=WAL")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1, 2, 3, 4):
            self.db.close()
            raise ValueError("Проект создан более новой версией приложения")
        if version in (1, 2, 3):
            backup_path=self.path.with_name(self.path.name+f'.v{version}-backup.sqlite')
            if not backup_path.exists():
                backup=sqlite3.connect(backup_path)
                try: self.db.backup(backup)
                finally: backup.close()
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self.db.execute("CREATE TABLE IF NOT EXISTS project (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL)")
            self.db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, status TEXT, output TEXT)")
            self.db.execute("CREATE TABLE IF NOT EXISTS feedback (source TEXT, start_us INTEGER, end_us INTEGER, status TEXT, PRIMARY KEY(source,start_us,end_us))")
            self.db.execute("""
                CREATE TABLE IF NOT EXISTS exports (
                    id TEXT PRIMARY KEY, clip_id TEXT NOT NULL, video TEXT NOT NULL,
                    is_current INTEGER NOT NULL CHECK(is_current IN (0,1))
                )
            """)
            self.db.execute("CREATE UNIQUE INDEX IF NOT EXISTS current_export ON exports (clip_id) WHERE is_current=1")
            self.db.execute("""
                CREATE TABLE IF NOT EXISTS export_covers (
                    export_id TEXT PRIMARY KEY REFERENCES exports(id), image TEXT NOT NULL,
                    frame_us INTEGER NOT NULL, hook TEXT NOT NULL, source_start_us INTEGER NOT NULL
                )
            """)
            self.db.execute("""
                CREATE TABLE IF NOT EXISTS publication_feedback (
                    clip_id TEXT PRIMARY KEY, status TEXT NOT NULL, note TEXT NOT NULL
                )
            """)
            # Another instance may have migrated the database while this connection waited for the lock.
            if self.db.execute("PRAGMA user_version").fetchone()[0] in (1, 2):
                self.db.execute("""
                    INSERT INTO exports (id, clip_id, video, is_current)
                    SELECT 'legacy:' || id, id, output, 1 FROM jobs
                    WHERE status='completed' AND output IS NOT NULL AND output<>''
                """)
            self.db.execute("PRAGMA user_version=4")
            if recover:
                self.db.execute("UPDATE jobs SET status='interrupted' WHERE status IN ('running','queued')")

    def save(self, project):
        for clip in project.clips:
            clip.validate(project.metadata['duration_us'])
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO project VALUES (1,?)", (json.dumps(project.serialize(), ensure_ascii=False),))

    def load(self):
        row = self.db.execute("SELECT data FROM project WHERE id=1").fetchone()
        return Project.load(json.loads(row[0])) if row else None

    def job(self, identifier, status, output):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO jobs VALUES (?,?,?)", (identifier,status,str(output)))

    def confirm_export(self, result, check_cancelled, replace_attempt=None):
        with self.db:
            if replace_attempt is None:
                self.db.execute("UPDATE exports SET is_current=0 WHERE clip_id=? AND is_current=1", (result.clip_id,))
            else:
                updated = self.db.execute(
                    "UPDATE exports SET is_current=0 WHERE clip_id=? AND is_current=1 AND id=?",
                    (result.clip_id, replace_attempt),
                )
                if updated.rowcount != 1:
                    raise ValueError("У клипа уже есть более новый экспорт. Откройте его обложку.")
            self.db.execute("INSERT INTO exports VALUES (?,?,?,1)",
                            (result.attempt_id, result.clip_id, str(result.video)))
            if result.cover is not None:
                self.db.execute("INSERT INTO export_covers VALUES (?,?,?,?,?)",
                                (result.attempt_id, str(result.cover), result.frame_us,
                                 result.hook, result.source_start_us))
            check_cancelled()

    def current_export(self, clip_id):
        row = self.db.execute("""
            SELECT e.id, e.video, c.image, c.frame_us, c.hook, c.source_start_us
            FROM exports e LEFT JOIN export_covers c ON e.id=c.export_id
            WHERE e.clip_id=? AND e.is_current=1
        """, (clip_id,)).fetchone()
        if row is None:
            return None
        attempt_id, video, image, frame_us, hook, source_start_us = row
        return ExportResult(attempt_id, clip_id, Path(video), Path(image) if image else None,
                            frame_us, hook or "", source_start_us)

    def publication_rating(self, clip_id):
        row = self.db.execute("SELECT status, note FROM publication_feedback WHERE clip_id=?", (clip_id,)).fetchone()
        return row or (None, "")

    def rate_publication(self, clip_id, status, note):
        if status not in ("publishable", "selection_error"):
            raise ValueError("Неизвестная оценка публикации")
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO publication_feedback VALUES (?,?,?)", (clip_id, status, note))

    def feedback(self,source,start_us,end_us,status):
        if status not in ('accepted','rejected'): raise ValueError('Неизвестная оценка')
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO feedback VALUES (?,?,?,?)",(source,start_us,end_us,status))

    def close(self):
        self.db.close()
