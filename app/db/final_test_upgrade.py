from sqlalchemy import inspect, text


def ensure_final_test_schema(db):
    columns = {column['name'] for column in inspect(db.get_bind()).get_columns('final_test_records')}
    if 'testing' not in columns:
        db.execute(text('ALTER TABLE final_test_records ADD COLUMN testing BOOLEAN NOT NULL DEFAULT 0'))
        db.commit()
