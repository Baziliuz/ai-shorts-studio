import json
import sqlite3
from pathlib import Path
from ai_shorts_studio.domain.model import Project

class Store:
    def __init__(self, path, recover=True):
        self.path = Path(path)
        self.db = sqlite3.connect(self.path)
        self.db.execute("PRAGMA journal_mode=WAL")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            self.db.close()
            raise ValueError("Проект создан более новой версией приложения")
        with self.db:
            self.db.execute("CREATE TABLE IF NOT EXISTS project (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL)")
            self.db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, status TEXT, output TEXT)")
            self.db.execute("PRAGMA user_version=1")
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

    def close(self):
        self.db.close()
