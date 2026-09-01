from alembic import context
from sqlalchemy import create_engine
from monitor.config import Settings
engine = create_engine(Settings.from_env().database_url)
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=None, transactional_ddl=True)
    with context.begin_transaction():
        context.run_migrations()
