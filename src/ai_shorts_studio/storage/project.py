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


class Store:
    def __init__(self, path, recover=True):
        self.path = Path(path)
        self.db = sqlite3.connect(self.path)
        self.db.execute("PRAGMA journal_mode=WAL")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1, 2, 3):
            self.db.close()
            raise ValueError("Проект создан более новой версией приложения")
        if version in (1, 2):
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
            # Another instance may have migrated the database while this connection waited for the lock.
            if self.db.execute("PRAGMA user_version").fetchone()[0] in (1, 2):
                self.db.execute("""
                    INSERT INTO exports (id, clip_id, video, is_current)
                    SELECT 'legacy:' || id, id, output, 1 FROM jobs
                    WHERE status='completed' AND output IS NOT NULL AND output<>''
                """)
            self.db.execute("PRAGMA user_version=3")
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

    def confirm_export(self, result, check_cancelled):
        with self.db:
            self.db.execute("UPDATE exports SET is_current=0 WHERE clip_id=? AND is_current=1", (result.clip_id,))
            self.db.execute("INSERT INTO exports VALUES (?,?,?,1)",
                            (result.attempt_id, result.clip_id, str(result.video)))
            check_cancelled()

    def current_export(self, clip_id):
        row = self.db.execute("SELECT id, video FROM exports WHERE clip_id=? AND is_current=1",
                              (clip_id,)).fetchone()
        return ExportResult(row[0], clip_id, Path(row[1])) if row else None

    def feedback(self,source,start_us,end_us,status):
        if status not in ('accepted','rejected'): raise ValueError('Неизвестная оценка')
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO feedback VALUES (?,?,?,?)",(source,start_us,end_us,status))

    def close(self):
        self.db.close()
