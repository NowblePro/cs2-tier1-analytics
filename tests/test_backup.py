from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.backup import create_database_backup, export_analytics_data
from app.config import Settings
from app.models.schema import Base, Team


def test_sqlite_backup_and_csv_export(tmp_path: Path):
    database = tmp_path / "source.db"
    settings = Settings(database_url=f"sqlite:///{database}")
    engine = create_engine(settings.database_url)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session.begin() as session:
        session.add(Team(hltv_team_id=1, name="Falcons"))
    backup = create_database_backup(settings, tmp_path / "backups")
    with Session() as session:
        exported = export_analytics_data(session, tmp_path / "exports")
    assert Path(backup["path"]).is_file()
    assert Path(exported["path"]).is_file()
    assert exported["tables"]["teams"] == 1
